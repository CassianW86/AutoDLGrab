import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from engine import Engine
from market import Market, Pause, SoldOut
from app import Settings
from pydantic import ValidationError


def host(id, idle=2):
    return {"machine_id": id, "gpu_name": "RTX 4090", "gpu_idle_num": idle, "on_line": 1,
            "max_instance_num": 4, "binding_instance_num": 0, "cpu_arch": "x86",
            "chip_corp": "nvidia", "highest_cuda_version": "12.8"}


CONFIG = {"count": 2, "gpu": "RTX 4090", "image": "base:img", "interval": 30, "limit": "5",
          "account": "test-user"}


class SimulatedMarket(Market):
    """Same candidate, pricing, submit and reconciliation code as the live adapter."""
    def __init__(self):
        self.hosts = []
        self.price = 4200
        self.cuda = "12.4"
        self.orders = []
        self.outcome = "success"
        self.started = asyncio.Event()
        self.gate = asyncio.Event()
        self.gate.set()
        self.requests = []
        self.calls = 0
        self.peak = 0

    async def call(self, endpoint, body=None, account=None):
        data = None
        if endpoint == "machine/gpu_type":
            data = [{"gpu_name": "RTX 4090", "gpu_series_type": "NVIDIA-Ada", "cpu_arch": "x86",
                     "chip_corp": "nvidia"},
                    {"gpu_name": "RTX 5090", "gpu_series_type": "NVIDIA-Blackwell", "cpu_arch": "x86",
                     "chip_corp": "nvidia"}]
        elif endpoint == "image/all":
            data = [{"label_name": "PyTorch", "children": [{"label_name": {"uuid": "img",
                     "cpu_arch": "x86", "chip_corp": "nvidia", "v": self.cuda}}]}]
        elif endpoint == "image/private/get":
            data = []
        elif endpoint == "user/machine/list":
            self.calls += 1
            self.peak = max(self.peak, self.calls)
            self.started.set()
            try:
                await self.gate.wait()
            finally:
                self.calls -= 1
            data = {"result_total": len(self.hosts), "list": self.hosts}
        elif endpoint == "order/price/preview":
            assert body["num"] == 2 and body["duration"] == 1
            data = {"origin_price": self.price}
        elif endpoint == "order/instance/create/payg":
            self.requests.append(body)
            if self.outcome == "sold":
                raise SoldOut()
            if self.outcome != "unknown":
                self.orders.append({"name": body["instance_info"]["instance_name"], "uuid": "mock-id"})
            if self.outcome in ("timeout", "unknown"):
                raise TimeoutError()
            data = "mock-id"
        elif endpoint == "instance":
            data = {"result_total": len(self.orders), "list": self.orders}
        else:
            raise AssertionError(endpoint)
        return {"account": "test-user", "data": data}


class EngineTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.file = Path(self.dir.name) / "tasks.db"
        self.market = SimulatedMarket()
        self.engine = Engine(self.market, self.file)

    async def asyncTearDown(self):
        await self.engine.close()
        self.dir.cleanup()

    async def phase(self, wanted):
        async with asyncio.timeout(5):
            while self.engine.state["phase"] != wanted:
                await asyncio.sleep(0.01)

    async def test_stock_returns_and_waiting_interval_resets(self):
        self.engine.start(CONFIG)
        await self.phase("waiting")
        previous = self.engine.state["due"]
        self.engine.interval(1)
        self.assertLess(self.engine.state["due"], previous)
        self.assertAlmostEqual(self.engine.state["due"] - time.time(), 1, delta=.1)
        self.market.hosts = [host("available")]
        await self.phase("success")
        self.assertEqual(self.engine.state["attempts"], 2)
        self.assertEqual(self.engine.state["instance"], "mock-id")
        self.assertEqual(len(self.market.requests), 1)
        self.assertEqual(self.market.peak, 1)

    async def test_change_during_check_and_stop(self):
        self.market.gate.clear()
        self.engine.start(CONFIG)
        await self.market.started.wait()
        self.engine.interval(7)
        with self.assertRaises(Pause):
            self.engine.start(CONFIG)
        self.market.gate.set()
        await self.phase("waiting")
        self.assertAlmostEqual(self.engine.state["due"] - time.time(), 7, delta=.1)
        await self.engine.stop()
        self.assertEqual(self.engine.state["phase"], "stopped")
        self.assertIsNone(self.engine.state["due"])

    async def test_stop_while_checking(self):
        self.market.gate.clear()
        self.market.hosts = [host("ready")]
        self.engine.start(CONFIG)
        await self.market.started.wait()
        await self.engine.stop()
        self.assertFalse(self.market.requests)

    async def test_filters(self):
        self.market.hosts = [host("a", 1), host("b", 1)]
        self.assertIsNone(await self.market.candidate(CONFIG))
        self.market.hosts = [host("a")]
        self.market.cuda = "13.1"
        self.assertIsNone(await self.market.candidate(CONFIG))
        self.market.cuda = "12.4"
        for price in (5100, None, -1, "NaN"):
            self.market.price = price
            self.assertIsNone(await self.market.candidate(CONFIG))
        self.market.price = 5000
        self.assertEqual((await self.market.candidate(CONFIG))["machine"], "a")

    async def test_alternative_models_create_one_instance(self):
        self.market.cuda = "12.8"
        self.market.hosts = [{**host("other"), "gpu_name": "RTX 3090"},
                             host("short", 1), {**host("ready"), "gpu_name": "RTX 5090"}]
        self.engine.start({**CONFIG, "gpu": ["RTX 4090", "RTX 5090"]})
        await self.phase("success")
        self.assertEqual(self.engine.state["matched_gpu"], "RTX 5090")
        self.assertEqual(len(self.market.requests), 1)
        self.assertEqual(self.market.requests[0]["instance_info"]["machine_id"], "ready")

    async def test_compatibility_uses_actual_gpu(self):
        self.market.hosts = [{**host("too-new"), "gpu_name": "RTX 5090"}, host("compatible")]
        match = await self.market.candidate({**CONFIG, "gpu": ["delisted", "RTX 5090", "RTX 4090"]})
        self.assertEqual(match["machine"], "compatible")

    def test_selection_validation_and_legacy_input(self):
        self.assertEqual(Settings(**CONFIG).gpu, ["RTX 4090"])
        self.assertEqual(Settings(**{**CONFIG, "gpu": ["RTX 4090", "RTX 4090"]}).gpu, ["RTX 4090"])
        for invalid in ([], [""], " "):
            with self.assertRaises(ValidationError):
                Settings(**{**CONFIG, "gpu": invalid})

    async def test_sold_at_submission(self):
        self.market.hosts = [host("ready")]
        self.market.outcome = "sold"
        self.engine.start(CONFIG)
        await self.phase("waiting")
        self.assertFalse(self.market.orders)

    async def test_timeout_after_order(self):
        self.market.hosts = [host("ready")]
        self.market.outcome = "timeout"
        self.engine.start(CONFIG)
        await self.phase("success")
        self.assertEqual(len(self.market.requests), 1)

    async def test_uncertain_submission_survives_restart(self):
        self.market.hosts = [host("ready")]
        self.market.outcome = "unknown"
        self.engine.start(CONFIG)
        await self.phase("review")
        await self.engine.close()
        self.engine = Engine(self.market, self.file)
        self.engine.recover()
        await self.engine.worker
        with self.assertRaises(Pause):
            self.engine.start(CONFIG)
        with self.assertRaises(Pause):
            await self.engine.stop()
        self.market.orders.append({"name": self.engine.state["name"], "uuid": "late-id"})
        self.engine.resume()
        await self.phase("success")
        self.assertEqual(self.engine.state["instance"], "late-id")
        self.assertEqual(len(self.market.requests), 1)

    async def test_restart_during_submit(self):
        self.engine.save(phase="submitting", settings=CONFIG, name="grab-test")
        self.market.orders = [{"name": "grab-test", "uuid": "recovered-id"}]
        self.engine.recover()
        await self.phase("success")
        self.assertFalse(self.market.requests)


if __name__ == "__main__":
    unittest.main()
