import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from engine import Engine, safe_reason
from market import Market, Pause
from bridge import ORIGIN as EXTENSION_ORIGIN, router as bridge_router

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("AUTODLGRAB_DATA", ROOT / ".local"))


@asynccontextmanager
async def lifespan(app):
    DATA.mkdir(parents=True, exist_ok=True)
    # Keep a second server process from recovering or submitting this account's active task.
    guard = sqlite3.connect(DATA / "worker.lock.db", timeout=0)
    try:
        guard.execute("BEGIN EXCLUSIVE")
        app.state.market = Market(DATA / "browser")
        app.state.engine = Engine(app.state.market, DATA / "tasks.db")
        app.state.engine.recover()
        try:
            yield
        finally:
            await app.state.engine.close()
            await app.state.market.close()
    finally:
        guard.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
app.include_router(bridge_router)


@app.middleware("http")
async def same_origin(request: Request, call_next):
    if request.method == "POST":
        origin = request.headers.get("origin")
        if request.url.path.startswith("/bridge/"):
            if origin != EXTENSION_ORIGIN or not request.headers.get("x-grab-bridge"):
                return JSONResponse({"detail": "仅接受本地 AutoDL 扩展。"}, status_code=403)
        elif request.headers.get("x-grab-panel") != "1" or (origin and origin != str(request.base_url).rstrip("/")):
            return JSONResponse({"detail": "仅接受本地面板操作。"}, status_code=403)
    return await call_next(request)


class Interval(BaseModel):
    interval: int = Field(strict=True, gt=0, le=2147483647)


class Settings(Interval):
    gpu: list[str] = Field(min_length=1)

    @field_validator("gpu", mode="before")
    @classmethod
    def selected_models(cls, value):
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            if not all(isinstance(item, str) and item.strip() for item in value):
                raise ValueError("显卡型号不能为空。")
            return list(dict.fromkeys(item.strip() for item in value))
        return value
    image: str = Field(pattern="^(base|private):.+$")
    count: int = Field(strict=True, ge=1)
    limit: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    account: str = Field(min_length=1)

    def values(self):
        values = self.model_dump()
        if self.limit is not None:
            values["limit"] = str(self.limit)
        return values


class Resolution(BaseModel):
    name: str


@app.exception_handler(Pause)
async def paused(request, error):
    return JSONResponse({"detail": str(error)}, status_code=409)


@app.exception_handler(Exception)
async def remote_error(request, error):
    return JSONResponse({"detail": safe_reason(error)}, status_code=502)


@app.get("/")
async def panel():
    return FileResponse(ROOT / "static/index.html")


@app.get("/static/i18n.js")
async def localization_script():
    return FileResponse(ROOT / "extension/i18n.js", media_type="text/javascript")


@app.get("/static/translations.json")
async def localization_catalog():
    return FileResponse(ROOT / "extension/translations.json", media_type="application/json")


@app.post("/api/login")
async def login():
    if app.state.engine.state["phase"] in ("checking", "submitting"):
        raise Pause("市场操作进行中，请等待本轮完成。")
    await app.state.market.login()
    return {"ok": True}


@app.get("/api/options")
async def options(gpu: list[str] | None = Query(default=None)):
    account, models = await app.state.market.models()
    images = []
    if gpu:
        if any(name not in models for name in gpu):
            raise Pause("当前市场没有所选显卡型号。")
        catalogs = await app.state.market.image_catalogs(gpu, models, account)
        images = list({key: image for catalog in catalogs.values() for key, image in catalog.items()}.values())
    return {"account": account, "gpus": list(models), "images": images}


@app.post("/api/preview")
async def preview(config: Settings):
    if app.state.engine.state["phase"] not in ("idle", "success", "stopped"):
        raise Pause("请先结束当前任务。")
    host = await app.state.market.candidate(config.values())
    return {"match": host and {key: host[key] for key in ("gpu", "machine", "price")}}


@app.get("/api/task")
async def status():
    bridge = getattr(app.state.market, "bridge", None)
    return {**app.state.engine.state, "browser_mode": (
        "readonly" if bridge and bridge.online else "offline" if bridge else "dedicated")}


@app.post("/api/start")
async def start(config: Settings):
    if getattr(app.state.market, "bridge", None):
        raise Pause("浏览器扩展当前仅用于只读检查。")
    app.state.engine.start(config.values())
    return app.state.engine.state


@app.post("/api/interval")
async def interval(config: Interval):
    app.state.engine.interval(config.interval)
    return app.state.engine.state


@app.post("/api/stop")
async def stop():
    await app.state.engine.stop()
    return app.state.engine.state


@app.post("/api/resume")
async def resume():
    app.state.engine.resume()
    return app.state.engine.state


@app.post("/api/resolve")
async def resolve(config: Resolution):
    app.state.engine.clear_review(config.name)
    return app.state.engine.state


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
