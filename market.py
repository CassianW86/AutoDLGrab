"""AutoDL ordinary marketplace adapter. All remote traffic uses the logged-in browser."""
import asyncio
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

ORIGIN = "https://www.autodl.com"
MARKET = ORIGIN + "/market/list"
CONSOLE = ORIGIN + "/console/instance/list"


class Pause(Exception):
    """User action or a service contract check is needed."""


class SoldOut(Exception):
    pass


def base_images(nodes, path=()):
    for node in nodes:
        label = node["label_name"]
        if isinstance(label, str):
            yield from base_images(node.get("children", []), (*path, label))
        elif label.get("uuid"):
            yield {"id": "base:" + label["uuid"], "label": " / ".join((*path, str(label["v"]))),
                   "cuda": label["v"], "arch": label.get("cpu_arch"), "chip": label.get("chip_corp")}


def version(value):
    match = re.fullmatch(r"\s*(\d+)(?:\.(\d+))?(?:\.\d+)?\s*", str(value))
    return (int(match[1]), int(match[2] or 0)) if match else None


def eligible(host, count, img, series, min_expand_bytes=0):
    if min_expand_bytes > 0 and int(host.get("max_data_disk_expand_size") or 0) < min_expand_bytes:
        return False
    if (host["gpu_idle_num"] < count
            or host["on_line"] in (3, 4, 5, 6)
            or host["max_instance_num"] <= host["binding_instance_num"]):
        return False
    mode = host.get("rent_mode")
    if mode:
        step = re.search(r"\d+", mode)
        if not step or int(step[0]) == 0:
            return False
        minimum = int(step[0])
        if host["gpu_idle_num"] >= minimum:
            if count < minimum or (not mode.startswith("Base") and count % minimum):
                return False
    if img["arch"] != host["cpu_arch"] or img["chip"] != host["chip_corp"]:
        return False
    if img["chip"] == "nvidia" and img["cuda"]:
        requested, highest = version(img["cuda"]), version(host.get("highest_cuda_version"))
        floor = {"NVIDIA-Ampere": (11, 0), "NVIDIA-Ada": (11, 3),
                 "NVIDIA-Blackwell": (12, 8)}.get(series, (0, 0))
        return bool(requested and highest and floor <= requested <= highest)
    if img["chip"] == "mthreads":
        required = {"2.7.0": "3.1.0", "5.2.0-server": "5.2.0"}.get(host.get("driver_version"))
        return not required or str(img["cuda"]) == required
    return True


def price_request(host, count):
    return {"machine_id": host["machine_id"], "charge_type": "payg", "duration": 1,
            "num": count, "coupon_id_list": [], "expand_data_disk": 0}


# Credentials stay on AutoDL's origin, including when the user navigates the login tab.
REQUEST = (Path(__file__).parent / "extension/request.js").read_text(encoding="utf-8")


class Market:
    def __init__(self, directory: Path):
        self.directory = directory
        self.driver = self.context = self.page = None
        self.bridge = None
        self.browser_lock = asyncio.Lock()

    async def login(self):
        from playwright.async_api import async_playwright

        async with self.browser_lock:
            if not self.context:
                if not self.driver:
                    self.driver = await async_playwright().start()
                edge = next((Path(root) / "Microsoft/Edge/Application/msedge.exe"
                             for var in ("ProgramFiles(x86)", "ProgramFiles")
                             if (root := os.environ.get(var))
                             and (Path(root) / "Microsoft/Edge/Application/msedge.exe").is_file()), None)
                self.context = await self.driver.chromium.launch_persistent_context(
                    str(self.directory), headless=False, no_viewport=True,
                    executable_path=str(edge) if edge else None)
                self.context.on("close", self._closed)
            if not self.page or self.page.is_closed():
                self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            await self.page.goto(MARKET, wait_until="domcontentloaded")
            if not await self.page.evaluate("Boolean(localStorage.getItem('token'))"):
                await self.page.goto(ORIGIN + "/login?url=%2Fmarket%2Flist", wait_until="domcontentloaded")
            await self.page.bring_to_front()
            if self.bridge:
                self.bridge.disconnect()
                self.bridge = None

    def _closed(self, *_):
        self.context = self.page = None

    async def close(self):
        if self.context:
            await self.context.close()
        if self.driver:
            await self.driver.stop()

    async def call(self, endpoint, body=None, account=None):
        if self.bridge:
            result = await self.bridge.call(endpoint, body, account)
        else:
            if not self.page or self.page.is_closed():
                raise Pause("请打开专用浏览器登录，或从已登录的 AutoDL 标签页连接本地扩展。")
            result = await self.page.evaluate("(" + REQUEST + ")", {"endpoint": endpoint, "body": body, "account": account})
        if result["code"] == "MachineGpuNumUpdateFailed":
            raise SoldOut()
        if result["code"] != "Success":
            raise Pause(str(result.get("msg") or result["code"])[:180])
        return result

    async def models(self):
        reply = await self.call("machine/gpu_type")
        return reply["account"], {g["gpu_name"]: g for g in reply["data"]}

    async def images(self, gpu, account):
        filters = {"cpu_arch": [gpu["cpu_arch"]], "chip_corp": [gpu["chip_corp"]]}
        public = (await self.call("image/all", filters, account))["data"]
        private = (await self.call("image/private/get", filters, account))["data"]
        images = list(base_images(public or []))
        for item in private or []:
            images.append({"id": "private:" + item["image_uuid"], "label": "我的镜像 / " + item["name"],
                           "cuda": item.get("cuda_v"), "arch": item.get("cpu_arch"),
                           "chip": item.get("chip_corp")})
        return images

    async def image_catalogs(self, names, models, account):
        catalogs = {}
        for name in names:
            gpu = models[name]
            architecture = (gpu["chip_corp"], gpu["cpu_arch"])
            if architecture not in catalogs:
                catalogs[architecture] = {i["id"]: i for i in await self.images(gpu, account)}
        return catalogs

    async def candidate(self, settings):
        account, models = await self.models()
        if account != settings["account"]:
            raise Pause("登录账号已变化。")
        min_expand = 0
        if settings.get("disk"):
            min_expand = int(Decimal(str(settings["disk"])) * 1024 ** 3)
        selected = settings["gpu"]
        names = [name for name in ([selected] if isinstance(selected, str) else selected) if name in models]
        if not names:
            raise Pause("所选型号已从市场移除，请重新读取选项。")
        catalogs = await self.image_catalogs(names, models, account)
        images = {}
        for name in names:
            gpu = models[name]
            image = catalogs[(gpu["chip_corp"], gpu["cpu_arch"])].get(settings["image"])
            if image:
                images[name] = image
        if not images:
            raise Pause("所选镜像对全部候选型号均不可用，请重新选择。")
        query = {"charge_type": "payg", "region_sign": "", "region_sign_list": [],
                 "gpu_type_name": list(images), "machine_tag_name": [],
                 "gpu_idle_num": settings["count"], "mount_net_disk": False,
                 "cpu_arch": [], "chip_corp": [], "default_order": True,
                 "page_index": 1, "page_size": 90}
        while True:
            data = (await self.call("user/machine/list", query, account))["data"]
            for host in data["list"] or []:
                name = host["gpu_name"]
                if name not in images or not eligible(
                        host, settings["count"], images[name], models[name].get("gpu_series_type"), min_expand):
                    continue
                price = price_request(host, settings["count"])
                quote = (await self.call("order/price/preview", price, account))["data"]
                # Undiscounted one-hour quote for the requested quantity is a conservative ceiling.
                try:
                    hourly = Decimal(str(quote["origin_price"])) / 1000
                except (InvalidOperation, TypeError, KeyError):
                    continue
                if not hourly.is_finite() or hourly <= 0:
                    continue
                if settings["limit"] is not None and hourly > Decimal(settings["limit"]):
                    continue
                return {"gpu": name, "machine": host["machine_id"], "price": str(hourly), "price_info": price}
            if not data["list"] or query["page_index"] * 90 >= data["result_total"]:
                return None
            query["page_index"] += 1

    async def submit(self, settings, candidate, name):
        kind, uuid = settings["image"].split(":", 1)
        instance = {"machine_id": candidate["machine"], "charge_type": "payg",
                    "req_gpu_amount": settings["count"], "instance_name": name,
                    "base_image_uuid": uuid if kind == "base" else "",
                    "private_image_uuid": uuid if kind == "private" else "",
                    "reproduction_uuid": "", "cg_application_uuid": "", "reproduction_id": 0,
                    "cg_application_info": {"image_name": "", "current_version_id": 0,
                                            "current_version": "", "image_id": 0},
                    "expand_data_disk": 0}
        reply = await self.call("order/instance/create/payg",
                                {"instance_info": instance, "price_info": candidate["price_info"]},
                                settings["account"])
        uuid = reply["data"]
        if not isinstance(uuid, str) or not uuid.strip():
            raise Pause("创建响应未包含实例 ID。")
        return uuid

    async def reconcile(self, settings, name):
        page = 1
        while True:
            data = (await self.call("instance", {"page_index": page, "page_size": 50,
                                    "name": name, "status": [], "charge_type": []},
                                    settings["account"]))["data"]
            for row in data["list"] or []:
                if row["name"] == name and row.get("uuid"):
                    return row["uuid"]
            if not data["list"] or page * 50 >= data["result_total"]:
                return None
            page += 1
