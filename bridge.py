"""Read-only request transport for an explicitly selected browser tab."""
import asyncio
import secrets
import time

from fastapi import APIRouter, HTTPException, Request

from market import Pause

ORIGIN = "chrome-extension://lhkogmmebfmebikjjojbacjahiilojcf"
READ_ONLY = {"machine/gpu_type", "image/all", "image/private/get",
             "user/machine/list", "order/price/preview"}
router = APIRouter(prefix="/bridge")


class BrowserBridge:
    def __init__(self):
        self.ticket = secrets.token_urlsafe(32)
        self.jobs = asyncio.Queue()
        self.pending = {}
        self.seen = time.monotonic()

    @property
    def online(self):
        return time.monotonic() - self.seen < 35

    async def call(self, endpoint, body, account):
        if endpoint not in READ_ONLY:
            raise Pause("浏览器扩展当前使用只读连接。")
        if not self.online:
            raise Pause("浏览器扩展连接已断开，请从 AutoDL 标签页重新点击扩展。")
        job_id = secrets.token_hex(12)
        future = asyncio.get_running_loop().create_future()
        self.pending[job_id] = future
        self.jobs.put_nowait(dict(id=job_id, endpoint=endpoint, body=body, account=account))
        try:
            return await asyncio.wait_for(future, timeout=23)
        except asyncio.TimeoutError:
            raise Pause("浏览器扩展查询超时，请确认两个浏览器标签页均保持打开。") from None
        finally:
            self.pending.pop(job_id, None)

    def disconnect(self):
        for future in self.pending.values():
            if not future.done():
                future.set_exception(Pause("浏览器连接已切换。"))


def authenticated(request):
    bridge = request.app.state.market.bridge
    if not bridge or not secrets.compare_digest(bridge.ticket, request.headers.get("x-grab-bridge", "")):
        raise HTTPException(403, "连接已过期。")
    bridge.seen = time.monotonic()
    return bridge


@router.post("/connect")
async def connect(request: Request):
    if request.app.state.engine.state["phase"] not in ("idle", "stopped", "success"):
        raise HTTPException(409, "请先处理当前任务。")
    market = request.app.state.market
    if market.bridge:
        market.bridge.disconnect()
    market.bridge = BrowserBridge()
    return {"ticket": market.bridge.ticket}


@router.post("/next")
async def next_job(request: Request):
    bridge = authenticated(request)
    try:
        while True:
            job = await asyncio.wait_for(bridge.jobs.get(), timeout=10)
            if job["id"] in bridge.pending:
                return job
    except asyncio.TimeoutError:
        return {}


@router.post("/reply")
async def reply(request: Request):
    bridge = authenticated(request)
    payload = await request.json()
    future = bridge.pending.get(payload["id"])
    if future and not future.done():
        if payload.get("error"):
            future.set_exception(Pause(str(payload["error"])[:220]))
        else:
            future.set_result(payload["result"])
    return {"ok": True}
