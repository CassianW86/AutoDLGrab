import asyncio
import unittest

from bridge import BrowserBridge
from market import Pause


class BridgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_endpoints(self):
        bridge = BrowserBridge()
        with self.assertRaises(Pause):
            await bridge.call("order/instance/create/payg", {}, "1")
        self.assertTrue(bridge.jobs.empty())

    async def test_query_and_disconnect(self):
        bridge = BrowserBridge()
        read = asyncio.create_task(bridge.call("machine/gpu_type", None, None))
        job = await bridge.jobs.get()
        self.assertEqual(job["endpoint"], "machine/gpu_type")
        bridge.pending[job["id"]].set_result({"code": "Success", "data": []})
        self.assertEqual((await read)["data"], [])
        read = asyncio.create_task(bridge.call("image/all", {}, "1"))
        await bridge.jobs.get()
        bridge.disconnect()
        with self.assertRaises(Pause):
            await read
        self.assertFalse(bridge.pending)
