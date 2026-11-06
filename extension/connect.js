"use strict";
const allowed = new Set(["machine/gpu_type","image/all","image/private/get","user/machine/list","order/price/preview"]);
const message = document.getElementById("message");
const tabId = Number(location.hash.slice(1));
let ticket;

async function local(path, body={}) {
  const res = await fetch("http://127.0.0.1:8765/bridge/" + path, {
    method:"POST", headers:{"Content-Type":"application/json","X-Grab-Bridge": ticket || "connect"},
    body:JSON.stringify(body), cache:"no-store", redirect:"error"
  });
  if (!res.ok) throw Error((await res.json()).detail || "本地服务拒绝连接");
  return res.json();
}
async function run() {
  if (!tabId) throw Error("请回到已登录的 www.autodl.com 标签页，再点击扩展图标。");
  const target = {tabId};
  const checked = await chrome.scripting.executeScript({target, func:() =>
    location.origin === "https://www.autodl.com" && Boolean(localStorage.getItem("token"))});
  if (!checked[0]?.result) throw Error("这个标签页还未登录 AutoDL。");
  ticket = (await local("connect")).ticket;
  message.textContent = "已连接。当前仅允许读取型号、镜像、主机和报价。";
  while (true) {
    const job = await local("next");
    if (!job.id) continue;
    let result, error;
    try {
      if (!allowed.has(job.endpoint)) throw Error("只读连接拒绝该请求。");
      const replies = await chrome.scripting.executeScript({target,func:marketRequest,args:[job]});
      result = replies[0]?.result;
      if (!result) throw Error("查询未返回结果，请检查 AutoDL 标签页是否仍然登录。");
    } catch (e) { error = e.message; }
    await local("reply",{id:job.id,result,error});
  }
}
run().catch(e => {message.textContent = "连接未就绪：" + e.message + "。请确认本地服务已启动，再回到 AutoDL 点击扩展图标。";});
