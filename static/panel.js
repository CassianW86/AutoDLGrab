"use strict";
const el = id => document.getElementById(id);
const labels = {idle:"等待开始", checking:"正在检查", waiting:"等待重试", paused:"已暂停",
  submitting:"正在创建", review:"待核对", stopped:"已停止", success:"已抢到"};
let state, account, lastTask, optionsRevision = 0, pending = false;

async function api(path, body) {
  const opts = body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json",
    "X-Grab-Panel":"1"}, body:JSON.stringify(body)};
  const res = await fetch("/api/" + path, {...opts, cache:"no-store"});
  const data = await res.json();
  if (!res.ok) throw Error(typeof data.detail === "string" ? data.detail : "输入有误，请检查数量、间隔和价格。");
  return data;
}
function feedback(text, error=false) {
  el("feedback").textContent = text;
  el("feedback").classList.toggle("error", error);
}
function action(id, callback) {
  el(id).addEventListener("click", async event => {
    event.preventDefault();
    if (pending) return;
    pending = true;
    el(id).disabled = true;
    try { await callback(); }
    catch (error) { feedback(error.message, true); }
    finally { pending = false; el(id).disabled = false; await poll(); }
  });
}
function select(id, items, hint, value="") {
  el(id).replaceChildren(new Option(hint,""), ...items.map(i => new Option(i.label,i.id)));
  el(id).value = value;
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
  const revision = ++optionsRevision;
  const gpus = selectedModels();
  const previous = el("image").value;
  select("image", [], "正在读取镜像");
  if (!gpus.length) return select("image", [], "请先选择显卡");
  const query = new URLSearchParams(gpus.map(name => ["gpu",name]));
  const result = await api("options?" + query);
  if (revision !== optionsRevision) return;
  if (result.account !== account) throw Error("账号发生变化，请重新读取选项。");
  select("image", result.images, "请选择镜像", result.images.some(i => i.id === previous) ? previous : "");
}
el("gpu").onchange = () => images().catch(e => feedback(e.message,true));
action("login", async () => { await api("login", {}); feedback("在专用窗口登录后，点击「读取选项」。"); });
action("load", async () => {
  const result = await api("options");
  account = result.account;
  ++optionsRevision;
  showModels(result.gpus);
  select("image", [], "请先选择显卡");
  feedback("已读取当前账号的显卡型号。");
});
function config() {
  const gpu = selectedModels();
  if (!gpu.length) throw Error("请至少选择一种候选显卡。");
  if (!el("form").reportValidity()) throw Error("请填写完整配置。");
  return {account,gpu,image:el("image").value,count:el("count").valueAsNumber,
    interval:el("interval").valueAsNumber,limit:el("limit").value === "" ? null : el("limit").valueAsNumber};
}
action("preview", async () => {
  const {match} = await api("preview", config());
  feedback(match ? "匹配到 " + match.gpu + "，主机 " + match.machine + "，整机报价 ¥" + match.price + "/小时。" : "当前没有符合条件的主机。");
});
action("start", async () => { await api("start", config()); feedback("任务已启动。"); });
el("form").onsubmit = event => {event.preventDefault(); if (!el("start").disabled) el("start").click();};
action("save", async () => {
  if (!el("interval").reportValidity()) return;
  await api("interval", {interval:el("interval").valueAsNumber});
  feedback("重试间隔已保存。");
});
action("stop", async () => { await api("stop", {}); feedback("任务已停止。"); });
action("resume", async () => { await api("resume", {}); });
action("resolve", async () => {
  const name = prompt("请在「我的实例」核实没有创建本次实例，再输入完整订单标记结束任务。");
  if (name) await api("resolve", {name});
});
function render(data) {
  const live = !["idle","success","stopped"].includes(data.phase);
  if (live && lastTask !== data.name) {
    const cfg = data.settings;
    const selected = Array.isArray(cfg.gpu) ? cfg.gpu : [cfg.gpu];
    const available = [...el("models").querySelectorAll("input")].map(input => input.value);
    showModels([...new Set([...available, ...selected])], selected);
    if (el("image").value !== cfg.image) select("image", [{id:cfg.image,label:cfg.image}], "", cfg.image);
    account = cfg.account;
    el("interval").value = cfg.interval;
    el("count").value = cfg.count;
    el("limit").value = cfg.limit ?? "";
  }
  lastTask = data.name;
  for (const id of ["gpu","image","count","limit","load","preview","start"]) el(id).disabled = live || pending;
  if (data.browser_mode === "readonly" || data.browser_mode === "offline") {
    el("start").disabled = true;
    el("connection").textContent = data.browser_mode === "readonly" ? "小智已连接 · 只读" : "小智已断开";
  } else {
    el("connection").textContent = "本地控制台";
  }
  el("phase").textContent = labels[data.phase];
  el("attempts").textContent = data.attempts + " 次";
  el("message").textContent = data.message;
  const showName = ["submitting","review","success"].includes(data.phase);
  el("orderLabel").hidden = !showName;
  el("order").textContent = showName ? data.name : "";
  el("idLabel").hidden = !data.instance;
  el("instance").textContent = data.instance || "";
  el("save").hidden = !["checking","waiting","paused"].includes(data.phase);
  el("stop").hidden = el("save").hidden;
  el("resume").hidden = !["paused","review"].includes(data.phase);
  el("resume").textContent = data.phase === "review" ? "再次核对" : "继续任务";
  el("resolve").hidden = data.phase !== "review";
  el("due").textContent = data.due ? new Date(data.due*1000).toLocaleTimeString() + " 检查" : "尚未安排";
  state = data;
  tick();
}
function tick() {
  el("timer").textContent = state?.due ? Math.max(0, Math.ceil(state.due - Date.now()/1000)) + " s" : "—";
}
async function poll() {
  try { render(await api("task")); }
  catch (error) { feedback("无法连接本地服务，请检查启动窗口。", true); }
}
async function refresh() { await poll(); setTimeout(refresh, 1000); }
setInterval(tick, 250);
refresh();
