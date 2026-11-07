import asyncio
import json
import socket
import webbrowser
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

import uvicorn

URL = "http://127.0.0.1:8765"


def dashboard_running():
    # Bypass proxies for the local service. Do not mistake an unrelated app for ours.
    try:
        with build_opener(ProxyHandler({})).open(URL + "/api/task", timeout=0.5) as response:
            data = json.load(response)
        return (isinstance(data, dict) and data.get("phase") in
                {"idle", "checking", "waiting", "paused", "submitting", "review", "stopped", "success"}
                and isinstance(data.get("attempts"), int)
                and data.get("browser_mode") in {"dedicated", "readonly", "offline"})
    except (OSError, URLError, ValueError):
        return False


def open_existing():
    print("AutoDL Grab 已在运行，正在打开工作台。 / Already running; opening the dashboard.")
    webbrowser.open(URL)


async def serve(server, listener):
    # SystemExit must not escape a background task and abort the event loop.
    try:
        await server.serve(sockets=[listener])
    except SystemExit as error:
        raise RuntimeError("服务启动失败，请查看上方错误。 / Service startup failed; see the error above.") from error


async def main():
    if await asyncio.to_thread(dashboard_running):
        open_existing()
        return
    # Reserve the port before application startup acquires the database lock.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", 8765))
        except OSError:
            # Another copy may still be starting between our probe and bind.
            for _ in range(10):
                if await asyncio.to_thread(dashboard_running):
                    open_existing()
                    return
                await asyncio.sleep(0.2)
            raise RuntimeError("端口 8765 已被占用或无法使用，请检查已有启动窗口。 / Port 8765 is occupied or unavailable; check existing startup windows.") from None
        server = uvicorn.Server(uvicorn.Config("app:app", host="127.0.0.1", port=8765, workers=1))
        running = asyncio.create_task(serve(server, listener))
        while not server.started and not running.done():
            await asyncio.sleep(0.1)
        if server.started:
            webbrowser.open(URL)
        await running


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as error:
        print(error)
        raise SystemExit(1) from None
