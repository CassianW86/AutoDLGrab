"use strict";
const el = id => document.getElementById(id);
const labels = {idle:"等待开始", checking:"正在检查", waiting:"等待重试", paused:"已暂停",
  submitting:"正在创建", review:"待核对", stopped:"已停止", success:"已抢到"};
let lastFeedback;
let state, account, lastTask, optionsRevision = 0, pending = false;
let sources = [], sourcesAccount;
const sourceStatuses = {shutdown:"已关机", shutdown_by_starting_error:"开机异常",
  shutdown_by_running_error:"意外宕机", re_init_failed:"重置失败", create_failed:"创建失败",
  running:"运行中", starting:"开机中", shutting_down:"关机中", shutdown_cloning:"克隆锁定中"};

async function api(path, body) {
  const opts = body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json",
    "X-Grab-Panel":"1"}, body:JSON.stringify(body)};
  const res = await fetch("/api/" + path, {...opts, cache:"no-store"});
  const data = await res.json();
  if (!res.ok) throw Error(typeof data.detail === "string" ? data.detail : t("输入有误，请检查数量、间隔和价格。"));
  return data;
}
function feedback(text, error=false) {
  lastFeedback = {text, error};
  el("feedback").textContent = t(text);
  el("feedback").classList.toggle("error", error);
}
function action(id, callback) {
  el(id).addEventListener("click", async event => {
    event.preventDefault();
    if (pending) return;
    pending = true;
    el(id).disabled = true;
    try {
      const operation = callback();
      if (state) render(state);
      await operation;
    }
    catch (error) { feedback(t(error.message), true); }
    finally { pending = false; el(id).disabled = false; await poll(); }
  });
}
function select(id, items, hint, value="") {
  el(id).replaceChildren(new Option(hint,""), ...items.map(i => {
    const option = new Option(t(i.label), i.id);
    option.disabled = Boolean(i.disabled);
    return option;
  }));
  el(id).value = value;
}
function updateMode() {
  const clone = el("mode").value === "clone";
  const locked = pending || Boolean(state && !["idle","success","stopped"].includes(state.phase));
  el("cloneFields").hidden = !clone;
  el("imageField").hidden = clone;
  el("image").required = !clone;
  el("source").required = clone;
  el("image").disabled = locked || clone;
  el("source").disabled = locked || !clone;
  sourceDetails();
}
function sourceDetails() {
  const source = sources.find(s => s.uuid === el("source").value);
  el("sourceInfo").textContent = source ? t("{region} · {status} · 数据盘 {size} GB，已扩容 {expand} GB", {
    region:source.region_name || source.region, status:sourceStatuses[source.status] || source.status,
    size:source.data_disk_bytes == null ? t("未知") : source.data_disk_bytes / 1024 ** 3,
    expand:source.expand_data_disk_bytes / 1024 ** 3
  }) : "";
}
function showSources(value=el("source").value) {
  const items = sources.map(source => ({id:source.uuid, disabled:Boolean(source.reason),
    label:t("{name} / {uuid} · {region} · {status} {reason}", {
      name:source.name, uuid:source.uuid, region:source.region_name || source.region,
      status:sourceStatuses[source.status] || source.status, reason:source.reason
    })}));
  // An active task may refer to an instance that has since disappeared or become locked.
  if (value && !sources.some(source => source.uuid === value)) items.push({id:value,label:value});
  select("source", items, t("请选择源实例"), value);
  sourceDetails();
}
async function loadSources(expectedAccount=account) {
  const revision = ++optionsRevision;
  const result = await api("instances");
  if (revision !== optionsRevision || el("mode").value !== "clone") return;
  if (result.account !== expectedAccount) throw Error(t("账号发生变化，请重新读取选项。"));
  const previous = sourcesAccount === result.account ? el("source").value : "";
  sources = result.sources;
  sourcesAccount = result.account;
  showSources(previous);
}
function selectedModels() {
  return [...el("models").querySelectorAll("input:checked")].map(input => input.value);
}
function showModels(names, selected=[]) {
  el("models").replaceChildren(...names.map(name => {
    const label = document.createElement("label");
    const check = document.createElement("input");
    check.type = "checkbox";
    check.value = name;
    check.checked = selected.includes(name);
    label.append(check, document.createTextNode(name));
    return label;
  }));
}
async function images() {
  if (el("mode").value === "clone") return;
  const revision = ++optionsRevision;
  const gpus = selectedModels();
  const previous = el("image").value;
  select("image", [], t("正在读取镜像"));
  if (!gpus.length) return select("image", [], t("请先选择显卡"));
  const query = new URLSearchParams(gpus.map(name => ["gpu",name]));
  const result = await api("options?" + query);
  if (revision !== optionsRevision) return;
  if (result.account !== account) throw Error(t("账号发生变化，请重新读取选项。"));
  select("image", result.images, t("请选择镜像"), result.images.some(i => i.id === previous) ? previous : "");
}
el("gpu").onchange = () => images().catch(e => feedback(t(e.message),true));
el("source").onchange = sourceDetails;
el("mode").onchange = async () => {
  ++optionsRevision;
  updateMode();
  try {
    if (el("mode").value === "clone" && account) await loadSources();
    if (el("mode").value === "create") await images();
  } catch (error) { feedback(t(error.message), true); }
};
action("login", async () => { await api("login", {}); feedback(t("在专用窗口登录后，点击「读取选项」。")); });
action("load", async () => {
  const result = await api("options");
  if (account !== result.account) {
    sources = [];
    sourcesAccount = undefined;
    showSources("");
  }
  account = result.account;
  ++optionsRevision;
  showModels(result.gpus);
  select("image", [], t("请先选择显卡"));
  if (el("mode").value === "clone") await loadSources();
  feedback(t("已读取当前账号的显卡型号。"));
});
function config() {
  const gpu = selectedModels();
  if (!gpu.length) throw Error(t("请至少选择一种候选显卡。"));
  if (!el("form").reportValidity()) throw Error(t("请填写完整配置。"));
  const mode = el("mode").value;
  return {account,gpu,mode,image:mode === "create" ? el("image").value : null,
    source_instance_uuid:mode === "clone" ? el("source").value : null,
    copy_data_disk:el("copyDisk").checked,count:el("count").valueAsNumber,
    interval:el("interval").valueAsNumber,limit:el("limit").value === "" ? null : el("limit").valueAsNumber,
    disk:el("disk").value === "" ? null : el("disk").valueAsNumber};
}
action("preview", async () => {
  const {match} = await api("preview", config());
  if (!match) return feedback(t("当前没有符合条件的主机。"));
  feedback(el("mode").value === "clone"
    ? t("匹配到 {gpu}，主机 {machine}，地区 {region}，扩容 {expand_gb} GB，整机报价 ¥{price}/小时（含磁盘日费 ¥{disk_daily_price}/日的折算）。", match)
    : t("匹配到 {gpu}，主机 {machine}，整机报价 ¥{price}/小时。", match));
});
action("start", async () => { await api("start", config()); feedback(t("任务已启动。")); });
el("form").onsubmit = event => {event.preventDefault(); if (!el("start").disabled) el("start").click();};
action("save", async () => {
  if (!el("interval").reportValidity()) return;
  await api("interval", {interval:el("interval").valueAsNumber});
  feedback(t("重试间隔已保存。"));
});
action("stop", async () => { await api("stop", {}); feedback(t("任务已停止。")); });
action("resume", async () => { await api("resume", {}); });
action("resolve", async () => {
  const name = prompt(t("请在「我的实例」核实没有创建本次实例，再输入完整订单标记结束任务。"));
  if (name) await api("resolve", {name});
});
function render(data) {
  const live = !["idle","success","stopped"].includes(data.phase);
  if (live && lastTask !== data.name) {
    const cfg = data.settings;
    const selected = Array.isArray(cfg.gpu) ? cfg.gpu : [cfg.gpu];
    const available = [...el("models").querySelectorAll("input")].map(input => input.value);
    showModels([...new Set([...available, ...selected])], selected);
    el("mode").value = cfg.mode || "create";
    el("copyDisk").checked = cfg.copy_data_disk ?? true;
    ++optionsRevision;
    if (el("mode").value === "clone") {
      sourcesAccount = cfg.account;
      showSources(cfg.source_instance_uuid);
      loadSources(cfg.account).catch(error => feedback(t(error.message), true));
    } else if (el("image").value !== cfg.image) {
      select("image", [{id:cfg.image,label:cfg.image}], "", cfg.image);
    }
    updateMode();
    account = cfg.account;
    el("interval").value = cfg.interval;
    el("count").value = cfg.count;
    el("limit").value = cfg.limit ?? "";
    el("disk").value = cfg.disk ?? "";
  }
  lastTask = data.name;
  for (const id of ["mode","source","copyDisk","gpu","image","count","limit","disk","load","preview","start"]) el(id).disabled = live || pending;
  if (data.browser_mode === "readonly" || data.browser_mode === "offline") {
    el("start").disabled = true;
    el("connection").textContent = data.browser_mode === "readonly" ? t("浏览器扩展已连接 · 只读") : t("浏览器扩展已断开");
  } else {
    el("connection").textContent = t("本地控制台");
  }
  el("image").disabled = live || pending || el("mode").value === "clone";
  el("source").disabled = live || pending || el("mode").value !== "clone";
  el("phase").textContent = t(labels[data.phase]);
  el("attempts").textContent = t("{count} 次", {count:data.attempts});
  el("message").textContent = t(data.message);
  const showName = ["submitting","review","success"].includes(data.phase);
  el("orderLabel").hidden = !showName;
  el("order").textContent = showName ? data.name : "";
  el("idLabel").hidden = !data.instance;
  el("instance").textContent = data.instance || "";
  el("save").hidden = !["checking","waiting","paused"].includes(data.phase);
  el("stop").hidden = el("save").hidden;
  el("resume").hidden = !["paused","review"].includes(data.phase);
  el("resume").textContent = data.phase === "review" ? t("再次核对") : t("继续任务");
  el("resolve").hidden = data.phase !== "review";
  el("due").textContent = data.due ? t("{time} 检查", {time:new Date(data.due*1000).toLocaleTimeString(i18n.language)}) : t("尚未安排");
  state = data;
  tick();
}
function tick() {
  el("timer").textContent = state?.due ? Math.max(0, Math.ceil(state.due - Date.now()/1000)) + " s" : "—";
}
async function poll() {
  try { render(await api("task")); }
  catch (error) { feedback(t("无法连接本地服务，请检查启动窗口。"), true); }
}
async function refresh() { await poll(); setTimeout(refresh, 1000); }
setInterval(tick, 250);
i18n.ready.then(refresh);

window.addEventListener("languagechange", () => {
  if (state) render(state);
  if (lastFeedback) feedback(lastFeedback.text, lastFeedback.error);
  for (const option of el("image").options) option.textContent = t(option.textContent);
  showSources();
});
