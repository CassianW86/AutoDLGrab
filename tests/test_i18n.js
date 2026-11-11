"use strict";
// Run with: node --test tests/test_i18n.js
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const dictionary = JSON.parse(fs.readFileSync(path.join(root, "extension/translations.json"), "utf8"));

async function setup(saved = "en") {
  const storage = new Map([["autodlgrab.language", saved]]);
  const context = vm.createContext({
    console, URL, Event, navigator:{language:"zh-CN"},
    document:{currentScript:{src:"http://localhost/static/i18n.js"}, documentElement:{},
      querySelectorAll:() => [], getElementById:() => null},
    localStorage:{getItem:key => storage.get(key), setItem:(key,value) => storage.set(key,value)},
    fetch:async () => ({ok:true, json:async () => dictionary}), dispatchEvent:() => {}
  });
  context.window = context;
  vm.runInContext(fs.readFileSync(path.join(root,"extension/i18n.js"),"utf8"), context);
  await context.i18n.ready;
  return {context,storage};
}

test("language switches preserve messages, dynamic values and preference", async () => {
  const {context:c,storage} = await setup();
  const original = "请打开专用浏览器登录，或从已登录的 AutoDL 标签页连接本地扩展。";
  const english = c.t(original);
  assert.match(english, /signed-in AutoDL tab/);
  assert.equal(c.t("已创建 RTX 4090 实例，抢卡结束。"), "Created a RTX 4090 instance. Task complete.");
  assert.equal(c.t("订单待核对：尚未找到同名实例；可稍后再次核对。"),
    "Order needs verification: No instance with this name was found yet. Verify again later.");
  const quote = c.t("匹配到 {gpu}，主机 {machine}，整机报价 ¥{price}/小时。", {gpu:"RTX 5090",machine:"123",price:"5.50"});
  assert.equal(quote,"Matched RTX 5090, host 123, total price ¥5.50/hour.");
  assert.equal(c.t("AutoDL remote message"),"AutoDL remote message");
  c.i18n.setLanguage("zh-CN");
  assert.equal(c.t(english),original);
  assert.equal(c.t(quote),"匹配到 RTX 5090，主机 123，整机报价 ¥5.50/小时。");
  assert.equal(storage.get("autodlgrab.language"),"zh-CN");
  c.i18n.setLanguage("invalid");
  assert.equal(c.i18n.language,"zh-CN");
});

test("static UI and local application messages have English translations", () => {
  for (const file of ["static/index.html","extension/connect.html"]) {
    const source = fs.readFileSync(path.join(root,file),"utf8");
    for (const match of source.matchAll(/data-i18n(?:>|\s[^>]*>)([^<]+)/g)) {
      const text = match[1];
      if (/\d+ 次/.test(text)) continue; // Rendered from the count template.
      assert.ok(dictionary[text], `${file}: ${text}`);
    }
  }
  for (const file of ["app.py","bridge.py","engine.py","market.py","extension/request.js","extension/connect.js","static/panel.js"]) {
    const source = fs.readFileSync(path.join(root,file),"utf8");
    assert.ok(!source.includes("小智"),file);
    for (const match of source.matchAll(/["']([^"'\n]*[\u4e00-\u9fff][^"'\n]*)["']/g)) {
      const text = match[1];
      if (["我的镜像 / ","订单待核对：","正在核对提交结果："].includes(text)) continue;
      assert.ok(dictionary[text],`${file}: ${text}`);
    }
  }
});
