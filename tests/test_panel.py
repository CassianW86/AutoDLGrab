"""Exercise the dashboard with intercepted APIs in a fresh headless browser."""
import json
import os
import unittest
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
IDLE = {"phase": "idle", "attempts": 0, "message": "等待开始", "due": None,
        "instance": None, "settings": None, "browser_mode": "dedicated"}
SOURCE = {"uuid": "source-id", "name": "training", "region": "region-A", "region_name": "Example region",
          "status": "shutdown", "reason": "", "data_disk_bytes": 150 * 1024 ** 3,
          "expand_data_disk_bytes": 100 * 1024 ** 3}


class PanelTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        executable = Path(self.driver.chromium.executable_path)
        if not executable.is_file():
            executable = next((Path(root) / "Microsoft/Edge/Application/msedge.exe"
                               for var in ("ProgramFiles(x86)", "ProgramFiles")
                               if (root := os.environ.get(var))
                               and (Path(root) / "Microsoft/Edge/Application/msedge.exe").is_file()), None)
        if executable is None:
            self.skipTest("Install Playwright Chromium or Edge to run headless UI tests")
        self.browser = await self.driver.chromium.launch(executable_path=str(executable), headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page()
        self.state = dict(IDLE)
        self.posts = {}
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        await self.page.route("**/*", self.route)

    async def route(self, route):
        path = route.request.url.removeprefix("http://grab.test")
        if route.request.method == "POST":
            self.posts[path] = route.request.post_data_json
        if path == "/api/task":
            data = self.state
        elif path.startswith("/api/options"):
            data = {"account": "test-user", "gpus": ["RTX 4090", "RTX 5090"],
                    "images": [{"id": "base:img", "label": "Test image"}]}
        elif path == "/api/instances":
            data = {"account": "test-user", "sources": [SOURCE,
                    {**SOURCE, "uuid": "running-id", "status": "running",
                     "reason": "源实例需要处于可克隆的关机状态。"}]}
        elif path == "/api/preview":
            data = {"match": {"gpu": "RTX 4090", "machine": "target", "price": "4.4",
                              "region": "Example region", "expand_gb": 50, "disk_daily_price": "4.8"}}
        elif path == "/api/start":
            self.state = {**IDLE, "phase": "waiting", "name": "grab-browser-test",
                          "settings": self.posts[path]}
            data = self.state
        else:
            files = {"/": ROOT / "static/index.html",
                     "/static/i18n.js": ROOT / "extension/i18n.js",
                     "/static/translations.json": ROOT / "extension/translations.json",
                     "/static/panel.js": ROOT / "static/panel.js", "/static/style.css": ROOT / "static/style.css"}
            if path not in files:
                await route.abort()
                return
            await route.fulfill(path=str(files[path]))
            return
        await route.fulfill(content_type="application/json", body=json.dumps(data))

    async def open(self):
        await self.page.goto("http://grab.test/")
        await self.page.wait_for_function("typeof state !== 'undefined' && state !== null")

    async def choose_clone(self):
        await self.page.select_option("#mode", "clone")
        await self.page.click("#load")
        await self.page.wait_for_selector('#source option[value="source-id"]', state="attached")
        await self.page.wait_for_function("!pending")
        await self.page.select_option("#source", "source-id")
        await self.page.check('#models input[value="RTX 4090"]')
        await self.page.fill("#count", "2")

    async def test_clone_entrypoint_preview_submit_and_language(self):
        await self.open()
        await self.choose_clone()
        self.assertFalse(await self.page.is_visible("#imageField"))
        self.assertTrue(await self.page.is_checked("#copyDisk"))
        self.assertIsNotNone(await self.page.locator('#source option[value="running-id"]').get_attribute("disabled"))
        self.assertIn("150", await self.page.text_content("#sourceInfo"))
        await self.page.click("#preview")
        await self.page.wait_for_function("!pending")
        self.assertIn("50", await self.page.text_content("#feedback"))
        await self.page.select_option("#language", "en")
        self.assertIn("Data disk", await self.page.text_content("#sourceInfo"))
        self.assertIn("total", await self.page.text_content("#feedback"))
        await self.page.uncheck("#copyDisk")
        await self.page.click("#start")
        await self.page.wait_for_function("state.phase === 'waiting' && !pending")
        self.assertEqual(self.posts["/api/start"]["source_instance_uuid"], "source-id")
        self.assertIsNone(self.posts["/api/start"]["image"])
        self.assertFalse(self.posts["/api/start"]["copy_data_disk"])
        self.assertTrue(await self.page.is_disabled("#mode"))
        self.assertFalse(self.errors)

    async def test_active_clone_restores_and_readonly_blocks_start(self):
        self.state = {**IDLE, "phase": "paused", "name": "grab-restored", "settings": {
            "mode": "clone", "source_instance_uuid": "source-id", "copy_data_disk": False,
            "gpu": ["RTX 4090"], "account": "test-user", "count": 2, "interval": 7,
            "limit": "4.4", "disk": None}}
        await self.open()
        await self.page.wait_for_function("sources.length > 0")
        self.assertEqual(await self.page.input_value("#mode"), "clone")
        self.assertEqual(await self.page.input_value("#source"), "source-id")
        self.assertFalse(await self.page.is_checked("#copyDisk"))
        self.assertEqual(await self.page.input_value("#interval"), "7")
        self.assertTrue(await self.page.is_disabled("#source"))
        self.state = {**IDLE, "browser_mode": "readonly"}
        await self.page.evaluate("poll()")
        self.assertTrue(await self.page.is_disabled("#start"))
        self.assertFalse(self.errors)

    async def test_switch_back_to_new_instance_and_mobile_layout(self):
        await self.page.set_viewport_size({"width": 390, "height": 844})
        await self.open()
        await self.choose_clone()
        self.assertTrue(await self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        await self.page.select_option("#mode", "create")
        await self.page.wait_for_selector('#image option[value="base:img"]', state="attached")
        await self.page.select_option("#image", "base:img")
        self.assertFalse(await self.page.is_visible("#cloneFields"))
        await self.page.click("#preview")
        await self.page.wait_for_function("!pending")
        self.assertEqual(self.posts["/api/preview"]["mode"], "create")
        self.assertEqual(self.posts["/api/preview"]["image"], "base:img")
        self.assertIsNone(self.posts["/api/preview"]["source_instance_uuid"])
        self.assertTrue(await self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        self.assertFalse(self.errors)

    async def test_required_source_and_image_block_submission(self):
        await self.open()
        await self.choose_clone()
        await self.page.select_option("#source", "")
        await self.page.click("#preview")
        await self.page.wait_for_function("!pending")
        self.assertNotIn("/api/preview", self.posts)
        await self.page.select_option("#mode", "create")
        await self.page.wait_for_selector('#image option[value="base:img"]', state="attached")
        await self.page.click("#preview")
        await self.page.wait_for_function("!pending")
        self.assertNotIn("/api/preview", self.posts)
