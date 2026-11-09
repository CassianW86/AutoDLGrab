"""Single event-loop worker. State transitions commit before any paid request."""
import asyncio
import json
import logging
import sqlite3
import time
from uuid import uuid4

from market import Pause, SoldOut

logger = logging.getLogger(__name__)


class Engine:
    def __init__(self, market, database, on_success=None):
        self.market = market
        self.db = sqlite3.connect(database)
        self.on_success = on_success
        self.db.execute("CREATE TABLE IF NOT EXISTS current (id INTEGER PRIMARY KEY, state TEXT NOT NULL)")
        row = self.db.execute("SELECT state FROM current WHERE id=1").fetchone()
        self.state = json.loads(row[0]) if row else {"phase": "idle", "attempts": 0, "message": "等待开始",
                                                       "due": None, "instance": None, "settings": None}
        self.worker = None
        self.changed = asyncio.Event()

    def save(self, **changes):
        updated = {**self.state, **changes}
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO current VALUES (1, ?)", (json.dumps(updated),))
        self.state = updated

    def recover(self):
        if self.state["phase"] in ("submitting", "review"):
            self.save(phase="review", due=None, message="提交可能已到达平台，正在核对实例。")
            self.worker = asyncio.create_task(self.check_order())
        elif self.state["phase"] in ("checking", "waiting"):
            self.save(phase="paused", due=None, message="服务已重启，登录后可继续。")

    def start(self, settings):
        if self.state["phase"] not in ("idle", "stopped", "success") or self.busy:
            raise Pause("请先处理当前任务。")
        self.save(phase="checking", attempts=0, due=None, instance=None, settings=settings, matched_gpu=None,
                  name="grab-" + uuid4().hex[:16], message="正在查询市场。")
        self.worker = asyncio.create_task(self.run())

    @property
    def busy(self):
        return self.worker is not None and not self.worker.done()

    def interval(self, seconds):
        if self.state["phase"] not in ("checking", "waiting", "paused"):
            raise Pause("当前没有可修改间隔的任务。")
        settings = {**self.state["settings"], "interval": seconds}
        due = time.time() + seconds if self.state["phase"] == "waiting" else None
        self.save(settings=settings, due=due)
        self.changed.set()

    async def stop(self):
        if self.state["phase"] in ("submitting", "review"):
            raise Pause("订单已进入核对阶段。请先核对实例结果。")
        if self.state["phase"] not in ("checking", "waiting", "paused"):
            return
        self.save(phase="stopped", due=None, message="已停止。")
        if self.busy:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)

    def resume(self):
        if self.busy:
            raise Pause("当前操作仍在进行。")
        if self.state["phase"] == "review":
            self.worker = asyncio.create_task(self.check_order())
        elif self.state["phase"] == "paused":
            self.save(phase="checking", message="正在继续。")
            self.worker = asyncio.create_task(self.run())
        else:
            raise Pause("当前任务无需恢复。")

    def clear_review(self, name):
        if self.state["phase"] != "review" or self.busy or name != self.state["name"]:
            raise Pause("请先核对 AutoDL 控制台，并输入本次任务的完整实例名。")
        self.save(phase="stopped", message="已手动确认没有创建实例，任务结束。")

    async def check_order(self):
        try:
            uuid = await self.market.reconcile(self.state["settings"], self.state["name"])
            if uuid:
                self.success(uuid)
                return
            reason = "尚未找到同名实例；可稍后再次核对。"
        except Exception as error:
            reason = safe_reason(error)
        self.save(phase="review", due=None, message="订单待核对：" + reason)

    def success(self, uuid):
        gpu = self.state.get("matched_gpu")
        self.save(phase="success", instance=uuid, due=None,
                  message=f"已创建 {gpu} 实例，抢卡结束。" if gpu else "已取得实例 ID，抢卡结束。")
        if self.on_success:
            try:
                self.on_success()
            except Exception:
                logger.exception("Success notification failed for instance %s", uuid)

    async def run(self):
        try:
            while True:
                self.save(phase="checking", attempts=self.state["attempts"] + 1, due=None,
                          message="正在检查主机、镜像和报价。")
                candidate = await self.market.candidate(self.state["settings"])
                reason = "没有符合条件的空闲主机。"
                if candidate:
                    self.save(phase="submitting", machine=candidate["machine"], price=candidate["price"], matched_gpu=candidate["gpu"],
                              message="已记录订单，正在创建实例。")
                    try:
                        uuid = await self.market.submit(self.state["settings"], candidate, self.state["name"])
                    except SoldOut:
                        reason = "提交时该主机库存已变化。"
                    else:
                        self.success(uuid)
                        return
                self.save(phase="waiting", message=reason, due=time.time() + self.state["settings"]["interval"])
                while self.state["due"] > time.time():
                    self.changed.clear()
                    try:
                        await asyncio.wait_for(self.changed.wait(), self.state["due"] - time.time())
                    except asyncio.TimeoutError:
                        break
        except Exception as error:
            if self.state["phase"] == "submitting":
                self.save(phase="review", due=None, message="正在核对提交结果：" + safe_reason(error))
                await self.check_order()
            else:
                self.save(phase="paused", due=None, message=safe_reason(error))

    async def close(self):
        if self.busy:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        self.db.close()


def safe_reason(error):
    if isinstance(error, Pause):
        return str(error)
    for message in ("请在专用窗口完成 AutoDL 登录", "账号发生变化", "请打开 AutoDL 窗口", "请在专用窗口完成人机验证"):
        if message in str(error):
            return message
    return "市场请求失败或响应格式已变化。请检查浏览器登录状态和网络，然后重试。"
