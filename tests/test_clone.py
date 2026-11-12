import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app import Settings, instances, preview, start, app
from engine import Engine
from market import GIB, ORDER_REJECTIONS, Market, Pause, SoldOut, SubmissionRejected
from pydantic import ValidationError
from test_grab import CONFIG, SimulatedMarket, host


CLONE_CONFIG = {**CONFIG, "mode": "clone", "image": None,
                "source_instance_uuid": "source-id", "copy_data_disk": True}


class CloneMarket(SimulatedMarket):
    def __init__(self):
        super().__init__()
        self.bridge = None
        self.source = {"uuid": "source-id", "name": "training", "status": "shutdown",
                       "region_sign": "region-A", "disk_health_status": "normal",
                       "private_image_uuid": "private-img", "reproduction_uuid": "",
                       "reproduction_id": 0, "expand_data_disk_size": 100 * GIB,
                       "usage_info": {"data_disk_total_size": 150 * GIB}, "ssh_pwd": "secret"}
        self.sources = [self.source]
        self.regions = [{"region_sign": sign, "region_name": "Same region", "data_center": "sameDC",
                         "setting": {"clone_instance": True}} for sign in ("region-A", "region-B")]
        self.snapshot = {"image": {"cpu_arch": "x86", "chip_corp": "nvidia", "cuda_version": "12.4",
                                   "image_type": "private", "image_name": "private-img"},
                         "expand_data_disk_size": 100 * GIB,
                         "machine_info_snapshot": {"max_instance_disk_size": 50 * GIB},
                         "cg_application_uuid": "", "cg_application_info": {}}
        self.snapshot_error = False
        self.hosts = [{**host("target"), "region_sign": "region-B", "max_instance_disk_size": 100 * GIB}]
        self.queries = []
        self.quotes = []
        self.disk_quote = True
        self.change_after_quote = False
        self.endpoint = None

    async def call(self, endpoint, body=None, account=None, params=None):
        if account is not None:
            assert account == "test-user"
        if endpoint == "common":
            data = {"region_list": self.regions}
        elif endpoint == "instance":
            rows = self.sources + self.orders
            begin = (body["page_index"] - 1) * body["page_size"]
            data = {"list": rows[begin:begin + body["page_size"]], "result_total": len(rows)}
        elif endpoint == "instance/snapshot":
            assert params == {"instance_uuid": "source-id"}
            if self.snapshot_error:
                raise TimeoutError()
            data = copy.deepcopy(self.snapshot)
        elif endpoint == "order/price/preview":
            self.quotes.append(copy.deepcopy(body))
            data = {"origin_price": self.price, "discount_list": []}
            if self.disk_quote and body["expand_data_disk"]:
                data["discount_list"].append({"product_type": "data_disk", "type": "member_discount",
                                              "origin_price": body["expand_data_disk"] // GIB * 96,
                                              "total_reduction": 100})
            if self.change_after_quote:
                self.source["status"] = "running"
        elif endpoint == "order/instance/clone/payg":
            self.endpoint = endpoint
            if self.outcome == "rejected":
                raise SubmissionRejected("TORecharge")
            if self.outcome == "malformed":
                return {"data": {}, "account": "test-user"}
            return await super().call("order/instance/create/payg", body, account)
        else:
            if endpoint == "user/machine/list":
                self.queries.append(copy.deepcopy(body))
            return await super().call(endpoint, body, account)
        return {"data": data, "account": "test-user"}


class CloneTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "tasks.db"
        self.market = CloneMarket()
        self.sound = Mock()
        self.engine = Engine(self.market, self.database, on_success=self.sound)

    async def asyncTearDown(self):
        await self.engine.close()
        self.directory.cleanup()

    async def phase(self, wanted):
        async with asyncio.timeout(5):
            while self.engine.state["phase"] != wanted:
                await asyncio.sleep(.01)

    async def test_region_capacity_price_and_native_payload(self):
        self.market.hosts.insert(0, {**self.market.hosts[0], "machine_id": "wrong-region",
                                    "region_sign": "region-C"})
        self.engine.start(CLONE_CONFIG)
        await self.engine.worker
        self.assertEqual(self.engine.state["phase"], "success")
        self.assertEqual(self.engine.state["price"], "4.4")
        self.assertEqual(self.market.queries[0]["region_sign_list"], ["region-A", "region-B"])
        self.assertEqual(self.market.endpoint, "order/instance/clone/payg")
        payload = self.market.requests[0]
        self.assertEqual(payload["instance_uuid"], "source-id")
        self.assertEqual(payload["price_info"], self.market.quotes[0])
        info = payload["instance_info"]
        self.assertEqual(info["machine_id"], "target")
        self.assertEqual(info["expand_data_disk"], 50 * GIB)
        self.assertEqual(info["private_image_uuid"], "private-img")
        self.assertEqual(info["instance_name"], self.engine.state["name"])
        self.assertTrue(info["copy_data_disk_after_clone"])
        self.assertFalse(info["keep_src_user_service_address_after_clone"])
        self.assertFalse(info["ft_sparse"])
        self.assertIn("复制进度", self.engine.state["message"])
        self.sound.assert_called_once_with()
        self.assertNotIn("secret", str(self.engine.state))

    async def test_storage_daily_fee_is_prorated_in_budget(self):
        self.assertIsNone(await self.market.candidate({**CLONE_CONFIG, "limit": "4.3999"}))
        self.assertIsNotNone(await self.market.candidate({**CLONE_CONFIG, "limit": "4.4"}))

    async def test_capacity_rounding_and_insufficient_space(self):
        self.market.snapshot["expand_data_disk_size"] += 1
        self.market.hosts[0]["max_data_disk_expand_size"] = 50 * GIB
        self.assertIsNone(await self.market.candidate(CLONE_CONFIG))
        self.market.hosts[0]["max_data_disk_expand_size"] = 51 * GIB
        candidate = await self.market.candidate(CLONE_CONFIG)
        self.assertEqual(candidate["expand_gb"], 51)
        self.assertEqual(candidate["price_info"]["expand_data_disk"], 51 * GIB)

    async def test_free_disk_can_cover_source_and_copy_can_be_disabled(self):
        self.market.hosts[0]["max_instance_disk_size"] = 160 * GIB
        self.assertEqual((await self.market.candidate(CLONE_CONFIG))["expand_gb"], 0)
        self.market.hosts[0]["max_instance_disk_size"] = 10 * GIB
        cfg = {**CLONE_CONFIG, "copy_data_disk": False}
        match = await self.market.candidate(cfg)
        await self.market.submit(cfg, match, "grab-system-only")
        info = self.market.requests[0]["instance_info"]
        self.assertEqual(info["expand_data_disk"], 0)
        self.assertFalse(info["copy_data_disk_after_clone"])

    async def test_compatibility_uses_source_snapshot(self):
        for change in ({"cpu_arch": "arm"}, {"chip_corp": "mthreads"}, {"cuda_version": "13.0"}):
            with self.subTest(change=change):
                original = copy.deepcopy(self.market.snapshot["image"])
                self.market.snapshot["image"].update(change)
                self.assertIsNone(await self.market.candidate(CLONE_CONFIG))
                self.market.snapshot["image"] = original
        self.market.hosts.insert(0, {**self.market.hosts[0], "gpu_name": "RTX 5090"})
        match = await self.market.candidate({**CLONE_CONFIG, "gpu": ["RTX 5090", "RTX 4090"]})
        self.assertEqual(match["gpu"], "RTX 4090")

    async def test_sources_expose_safe_summary_and_disable_invalid_sources(self):
        for status in ("running", "shutdown_cloning", "migrating"):
            self.market.sources.append({**self.market.source, "uuid": status, "status": status})
        result = await self.market.clone_sources()
        self.assertEqual(result["account"], "test-user")
        self.assertEqual(result["sources"][0]["data_disk_bytes"], 150 * GIB)
        self.assertFalse(result["sources"][0]["reason"])
        self.assertTrue(all(s["reason"] for s in result["sources"][1:]))
        self.assertNotIn("ssh_pwd", str(result))
        self.assertNotIn("secret", str(result))

    async def test_source_list_pagination(self):
        self.market.sources = [{**self.market.source, "uuid": str(i)} for i in range(51)] + [self.market.source]
        self.assertEqual(len((await self.market.clone_sources())["sources"]), 52)
        self.assertEqual((await self.market.clone_source(CLONE_CONFIG))["uuid"], "source-id")

    async def test_invalid_source_conditions_pause_before_order(self):
        for key, value in (("status", "running"), ("status", "shutdown_cloning"),
                           ("disk_health_status", "alert"), ("disk_health_status", "abnormal")):
            original = self.market.source[key]
            self.market.source[key] = value
            with self.assertRaises(Pause):
                await self.market.candidate(CLONE_CONFIG)
            self.market.source[key] = original
        self.market.regions[0]["setting"]["clone_instance"] = False
        with self.assertRaises(Pause):
            await self.market.candidate(CLONE_CONFIG)
        self.market.regions[0]["setting"]["clone_instance"] = True
        self.market.sources = []
        with self.assertRaises(Pause):
            await self.market.candidate(CLONE_CONFIG)
        self.assertFalse(self.market.requests)

    async def test_raw_common_regions_group_by_data_center_and_match_exact_signs(self):
        self.market.regions.append({"region_sign": "region-A-extra", "region_name": "Other region",
                                    "data_center": "otherDC", "setting": {"clone_instance": False}})
        groups = await self.market.clone_regions("test-user")
        self.assertEqual(groups[0]["region_sign"], ["region-A", "region-B"])
        self.assertTrue(groups[0]["clone_instance"])
        self.assertFalse(groups[1]["clone_instance"])
        self.assertEqual((await self.market.clone_sources())["sources"][0]["reason"], "")
        self.market.source["region_sign"] = "region"
        self.assertTrue((await self.market.clone_sources())["sources"][0]["reason"])

    async def test_already_grouped_regions_remain_supported(self):
        self.market.regions = [{"region_sign": ["region-A", "region-B"], "region_name": "Same region",
                                "clone_instance": True}]
        self.assertEqual((await self.market.clone_regions("test-user"))[0]["region_sign"],
                         ["region-A", "region-B"])
        self.assertFalse((await self.market.clone_sources())["sources"][0]["reason"])

    async def test_revalidation_failure_pauses_without_order_review(self):
        self.market.change_after_quote = True
        self.engine.start(CLONE_CONFIG)
        await self.engine.worker
        self.assertEqual(self.engine.state["phase"], "paused")
        self.assertFalse(self.market.requests)
        self.assertFalse(self.sound.called)

    async def test_snapshot_change_and_preflight_network_failure_are_safe(self):
        candidate = await self.market.candidate(CLONE_CONFIG)
        self.market.snapshot["image"]["cuda_version"] = "12.8"
        with self.assertRaises(SubmissionRejected):
            await self.market.submit(CLONE_CONFIG, candidate, "grab-test")
        self.market.snapshot_error = True
        with self.assertRaises(SubmissionRejected):
            await self.market.submit(CLONE_CONFIG, candidate, "grab-test")
        self.assertFalse(self.market.requests)

    async def test_public_image_and_community_application_metadata(self):
        self.market.source["private_image_uuid"] = ""
        self.market.snapshot["image"].update(image_type="public", image_name="base-img")
        match = await self.market.candidate(CLONE_CONFIG)
        await self.market.submit(CLONE_CONFIG, match, "grab-public")
        self.assertEqual(self.market.requests[-1]["instance_info"]["base_image_uuid"], "base-img")
        self.market.source.update(reproduction_uuid="community-img", reproduction_id=123)
        self.market.snapshot["image"]["image_type"] = "cg"
        self.market.snapshot.update(cg_application_uuid="application", cg_application_info={"image_id": 456})
        match = await self.market.candidate(CLONE_CONFIG)
        await self.market.submit(CLONE_CONFIG, match, "grab-application")
        info = self.market.requests[-1]["instance_info"]
        self.assertEqual(info["reproduction_uuid"], "community-img")
        self.assertEqual(info["reproduction_id"], 123)
        self.assertEqual(info["cg_application_uuid"], "application")
        self.assertEqual(info["cg_application_info"], {"image_id": 456})

    async def test_missing_disk_quote_pauses(self):
        self.market.disk_quote = False
        self.engine.start(CLONE_CONFIG)
        await self.engine.worker
        self.assertEqual(self.engine.state["phase"], "paused")
        self.assertFalse(self.market.requests)

    async def test_explicit_rejection_pauses(self):
        self.market.outcome = "rejected"
        self.engine.start(CLONE_CONFIG)
        await self.engine.worker
        self.assertEqual(self.engine.state["phase"], "paused")
        self.assertFalse(self.market.orders)

    async def test_sold_out_waits_and_then_creates_one_clone(self):
        self.market.outcome = "sold"
        self.engine.start(CLONE_CONFIG)
        await self.phase("waiting")
        self.market.outcome = "success"
        self.engine.interval(1)
        await self.phase("success")
        self.assertEqual(len(self.market.orders), 1)
        self.sound.assert_called_once_with()

    async def test_timeout_reconciles_one_clone(self):
        self.market.outcome = "timeout"
        self.engine.start(CLONE_CONFIG)
        await self.engine.worker
        self.assertEqual(self.engine.state["phase"], "success")
        self.assertEqual(len(self.market.requests), 1)
        self.sound.assert_called_once_with()

    async def test_uncertain_order_restarts_and_reconciles_without_resubmission(self):
        self.market.outcome = "unknown"
        self.engine.start(CLONE_CONFIG)
        await self.engine.worker
        self.assertEqual(self.engine.state["phase"], "review")
        await self.engine.close()
        self.engine = Engine(self.market, self.database)
        self.engine.recover()
        await self.engine.worker
        with self.assertRaises(Pause):
            self.engine.start(CLONE_CONFIG)
        with self.assertRaises(Pause):
            await self.engine.stop()
        self.market.orders.append({"name": self.engine.state["name"], "uuid": "late-clone"})
        self.engine.resume()
        await self.engine.worker
        self.assertEqual(self.engine.state["instance"], "late-clone")
        self.assertEqual(len(self.market.requests), 1)

    async def test_malformed_creation_reply_enters_review(self):
        self.market.outcome = "malformed"
        self.engine.start(CLONE_CONFIG)
        await self.engine.worker
        self.assertEqual(self.engine.state["phase"], "review")

    async def test_api_preview_sources_and_readonly_start(self):
        previous_market = getattr(app.state, "market", None)
        previous_engine = getattr(app.state, "engine", None)
        app.state.market, app.state.engine = self.market, self.engine
        try:
            result = await preview(Settings(**CLONE_CONFIG))
            self.assertEqual(result["match"]["expand_gb"], 50)
            self.assertNotIn("source", result["match"])
            self.assertEqual((await instances())["account"], "test-user")
            self.market.bridge = object()
            with self.assertRaises(Pause):
                await start(Settings(**CLONE_CONFIG))
        finally:
            app.state.market, app.state.engine = previous_market, previous_engine

    def test_settings_and_legacy_defaults(self):
        self.assertEqual(Settings(**CONFIG).mode, "create")
        self.assertEqual(Settings(**CLONE_CONFIG).source_instance_uuid, "source-id")
        minimal = {key: value for key, value in CLONE_CONFIG.items() if key not in ("image", "copy_data_disk")}
        self.assertTrue(Settings(**minimal).copy_data_disk)
        for overrides in ({"source_instance_uuid": None}, {"source_instance_uuid": " "},
                          {"mode": "invalid"}, {"copy_data_disk": "false"}):
            with self.assertRaises(ValidationError):
                Settings(**{**CLONE_CONFIG, **overrides})
        with self.assertRaises(ValidationError):
            Settings(**{**CONFIG, "image": None})


class RemoteRejectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_known_rejections_allow_retrying_without_review(self):
        class Page:
            code = "TORecharge"

            def is_closed(self):
                return False

            async def evaluate(self, script, args):
                return {"code": self.code}

        market = Market(Path("unused"))
        market.page = Page()
        for code in ORDER_REJECTIONS:
            market.page.code = code
            with self.assertRaises(SubmissionRejected):
                await market.call("order/instance/clone/payg", {})
        market.page.code = "UnknownInternalError"
        with self.assertRaises(Pause) as caught:
            await market.call("order/instance/clone/payg", {})
        self.assertNotIsInstance(caught.exception, SubmissionRejected)
        market.page.code = "MachineGpuNumUpdateFailed"
        with self.assertRaises(SoldOut):
            await market.call("order/instance/clone/payg", {})
