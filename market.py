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


class SubmissionRejected(Pause):
    """The order was rejected, or validation failed before sending an order."""


CLONE_STATES = {"shutdown", "shutdown_by_starting_error", "shutdown_by_running_error",
                "re_init_failed", "create_failed"}
ORDER_REJECTIONS = {"TORecharge", "TORealName", "AuthorizeFailed", "NoPermission",
                    "PermissionDenied", "Unauthorized", "Forbidden"}
GIB = 1024 ** 3


def clone_reason(row, region):
    if row.get("status") not in CLONE_STATES:
        return "源实例需要处于可克隆的关机状态。"
    if row.get("disk_health_status") in ("alert", "abnormal"):
        return "源实例磁盘预警或异常，请先在 AutoDL 处理。"
    if not region or not region.get("clone_instance"):
        return "源实例所在地区暂不支持克隆。"
    return ""


def quote_prices(quote, expand):
    """AutoDL quotes GPU hourly fees and data disk daily fees separately (milli-yuan)."""
    hourly = Decimal(str(quote["origin_price"])) / 1000
    daily = Decimal(0)
    if expand:
        disks = [item for item in quote["discount_list"]
                 if item["product_type"] == "data_disk" and item["type"] == "member_discount"]
        if len(disks) != 1:
            raise ValueError("Missing unambiguous data disk quote")
        daily = Decimal(str(disks[0]["origin_price"])) / 1000
        if not daily.is_finite() or daily <= 0:
            raise ValueError("Invalid data disk quote")
    if not hourly.is_finite() or hourly <= 0:
        raise ValueError("Invalid GPU quote")
    return hourly + daily / 24, daily


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


def price_request(host, count, expand=0):
    return {"machine_id": host["machine_id"], "charge_type": "payg", "duration": 1,
            "num": count, "coupon_id_list": [], "expand_data_disk": expand}


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
                    executable_path=str(edge) if edge else None,
                    ignore_default_args=["--enable-automation"],
                    args=["--disable-blink-features=AutomationControlled"])
                await self.context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
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

    async def call(self, endpoint, body=None, account=None, params=None):
        if self.bridge:
            result = await self.bridge.call(endpoint, body, account, params)
        else:
            if not self.page or self.page.is_closed():
                raise Pause("请打开专用浏览器登录，或从已登录的 AutoDL 标签页连接本地扩展。")
            result = await self.page.evaluate("(" + REQUEST + ")", {
                "endpoint": endpoint, "body": body, "account": account, "params": params})
        if result["code"] == "MachineGpuNumUpdateFailed":
            raise SoldOut()
        if result["code"] != "Success":
            error_type = SubmissionRejected if result["code"] in ORDER_REJECTIONS else Pause
            raise error_type(str(result.get("msg") or result["code"])[:180])
        return result

    async def instance_rows(self, account, name=""):
        page = 1
        while True:
            data = (await self.call("instance", {"page_index": page, "page_size": 50,
                                    "name": name, "status": [], "charge_type": []}, account))["data"]
            for row in data["list"] or []:
                yield row
            if not data["list"] or page * 50 >= data["result_total"]:
                return
            page += 1

    async def clone_sources(self):
        account, _ = await self.models()
        regions = (await self.call("common", account=account))["data"]["region_list"]
        sources = []
        async for row in self.instance_rows(account):
            region = next((r for r in regions if row.get("region_sign") in r["region_sign"]), None)
            sources.append({"uuid": row["uuid"], "name": row.get("name") or row["uuid"],
                            "region": row.get("region_sign"), "region_name": (region or {}).get("region_name", ""),
                            "status": row.get("status"), "reason": clone_reason(row, region),
                            "data_disk_bytes": (row.get("usage_info") or {}).get("data_disk_total_size"),
                            "expand_data_disk_bytes": row.get("expand_data_disk_size", 0)})
        return {"account": account, "sources": sources}

    async def clone_source(self, settings):
        account = settings["account"]
        source = None
        async for row in self.instance_rows(account):
            if row["uuid"] == settings["source_instance_uuid"]:
                source = row
                break
        if source is None:
            raise Pause("源实例已释放或当前账号无法访问。")
        regions = (await self.call("common", account=account))["data"]["region_list"]
        region = next((r for r in regions if source.get("region_sign") in r["region_sign"]), None)
        reason = clone_reason(source, region)
        if reason:
            raise Pause(reason)
        snapshot = (await self.call("instance/snapshot", account=account,
                                   params={"instance_uuid": source["uuid"]}))["data"]
        image = snapshot["image"]
        if not image.get("cpu_arch") or not image.get("chip_corp"):
            raise Pause("源实例镜像元数据不完整，请到 AutoDL 检查。")
        # The snapshot exposes the provisioned free data disk plus its paid expansion.
        base = int(snapshot["machine_info_snapshot"]["max_instance_disk_size"])
        expansion = int(snapshot["expand_data_disk_size"])
        if base <= 0 or expansion < 0:
            raise Pause("源实例磁盘容量信息无效。")
        return {"uuid": source["uuid"], "name": source.get("name") or source["uuid"],
                "region_name": region["region_name"], "regions": region["region_sign"],
                "data_disk_bytes": base + expansion,
                "image": {"arch": image["cpu_arch"], "chip": image["chip_corp"],
                          "cuda": image.get("cuda_version")},
                "instance_info": {
                    "base_image_uuid": image["image_name"] if image["image_type"] == "public" else "",
                    "private_image_uuid": source.get("private_image_uuid") or "",
                    "reproduction_uuid": source.get("reproduction_uuid") or "",
                    "reproduction_id": source.get("reproduction_id") or 0,
                    "cg_application_uuid": snapshot.get("cg_application_uuid") or "",
                    "cg_application_info": snapshot.get("cg_application_info") or {}}}

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
        source = None
        images = {}
        if settings.get("mode") == "clone":
            source = await self.clone_source(settings)
            images = dict.fromkeys(names, source["image"])
        else:
            catalogs = await self.image_catalogs(names, models, account)
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
        if source:
            query["region_sign_list"] = source["regions"]
        while True:
            data = (await self.call("user/machine/list", query, account))["data"]
            for host in data["list"] or []:
                name = host["gpu_name"]
                if source and host.get("region_sign") not in source["regions"]:
                    continue
                if name not in images or not eligible(
                        host, settings["count"], images[name], models[name].get("gpu_series_type"), min_expand):
                    continue
                expand = 0
                if source and settings.get("copy_data_disk", True):
                    # Expansion is billed in whole GiB. Free disk capacity varies by host.
                    required = max(0, source["data_disk_bytes"] - int(host["max_instance_disk_size"]))
                    expand = ((required + GIB - 1) // GIB) * GIB
                    if expand > int(host.get("max_data_disk_expand_size") or 0):
                        continue
                price = price_request(host, settings["count"], expand)
                quote = (await self.call("order/price/preview", price, account))["data"]
                # Undiscounted one-hour quote for the requested quantity is a conservative ceiling.
                try:
                    hourly, daily = quote_prices(quote, expand)
                except (InvalidOperation, ValueError, TypeError, KeyError):
                    if expand:
                        raise Pause("扩容数据盘报价不完整，请检查 AutoDL 报价后重试。") from None
                    continue
                if settings["limit"] is not None and hourly > Decimal(settings["limit"]):
                    continue
                return {"gpu": name, "machine": host["machine_id"], "price": str(hourly),
                        "region": host.get("region_name") or (source or {}).get("region_name", ""),
                        "expand_gb": expand // GIB, "disk_daily_price": str(daily),
                        "price_info": price, "source": source}
            if not data["list"] or query["page_index"] * 90 >= data["result_total"]:
                return None
            query["page_index"] += 1

    async def submit(self, settings, candidate, name):
        clone = settings.get("mode") == "clone"
        source = None
        if clone:
            # Failures here are known to precede the paid request and may safely pause.
            try:
                source = await self.clone_source(settings)
                if source != candidate["source"]:
                    raise Pause("源实例信息已变化，请重新检查匹配。")
            except Pause as error:
                raise SubmissionRejected(str(error)) from error
            except Exception as error:
                raise SubmissionRejected("克隆前检查失败，请检查登录状态和网络后继续。") from error
        kind, uuid = ("", "") if clone else settings["image"].split(":", 1)
        instance = {"machine_id": candidate["machine"], "charge_type": "payg",
                    "req_gpu_amount": settings["count"], "instance_name": name,
                    "base_image_uuid": uuid if kind == "base" else "",
                    "private_image_uuid": uuid if kind == "private" else "",
                    "reproduction_uuid": "", "cg_application_uuid": "", "reproduction_id": 0,
                    "cg_application_info": {"image_name": "", "current_version_id": 0,
                                            "current_version": "", "image_id": 0},
                    "expand_data_disk": candidate["price_info"]["expand_data_disk"]}
        endpoint = "order/instance/create/payg"
        payload = {"instance_info": instance, "price_info": candidate["price_info"]}
        if clone:
            endpoint = "order/instance/clone/payg"
            payload["instance_uuid"] = source["uuid"]
            instance.update(source["instance_info"])
            instance.update(copy_data_disk_after_clone=settings.get("copy_data_disk", True),
                            keep_src_user_service_address_after_clone=False, ft_sparse=False)
        reply = await self.call(endpoint, payload, settings["account"])
        uuid = reply["data"]
        if not isinstance(uuid, str) or not uuid.strip():
            raise Pause("创建响应未包含实例 ID。")
        return uuid

    async def reconcile(self, settings, name):
        async for row in self.instance_rows(settings["account"], name):
            if row["name"] == name and row.get("uuid"):
                return row["uuid"]
        return None
