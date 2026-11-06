import asyncio
import webbrowser

import uvicorn


async def main():
    server = uvicorn.Server(uvicorn.Config("app:app", host="127.0.0.1", port=8765, workers=1))
    running = asyncio.create_task(server.serve())
    while not server.started and not running.done():
        await asyncio.sleep(0.1)
    if server.started:
        webbrowser.open("http://127.0.0.1:8765")
    await running


if __name__ == "__main__":
    asyncio.run(main())
