"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function transport(user={uuid:"user-id"}) {
  const calls = [];
  const storage = {token:"test-token", user:JSON.stringify(user), app_version:'{"version":"1"}'};
  const context = vm.createContext({URL, AbortSignal, location:{origin:"https://www.autodl.com",pathname:"/market/list"},
    localStorage:{getItem:key => storage[key]}, document:{querySelector:() => null},
    fetch:async (url, options) => {
      calls.push({url:String(url),options});
      return {json:async () => ({code:"Success",data:{}})};
    }
  });
  const source = fs.readFileSync(path.join(__dirname,"../extension/request.js"),"utf8");
  vm.runInContext(source,context);
  return {context,calls};
}

test("snapshot uses GET with encoded query parameters and account binding", async () => {
  const {context,calls} = transport();
  const result = await context.marketRequest({endpoint:"instance/snapshot",body:null,account:"user-id",
    params:{instance_uuid:"source &/?id"}});
  const url = new URL(calls[0].url);
  assert.equal(url.pathname,"/api/v1/instance/snapshot");
  assert.equal(url.searchParams.get("instance_uuid"),"source &/?id");
  assert.equal(calls[0].options.method,"GET");
  assert.equal(calls[0].options.body,undefined);
  assert.equal(result.account,"user-id");
  await assert.rejects(context.marketRequest({endpoint:"instance/snapshot",body:null,account:"other"}), /账号/);
  assert.equal(calls.length,1);
});

test("clone order retains POST payload and sub-user route", async () => {
  const {context,calls} = transport({uuid:"user-id",sub_name:"child"});
  const body = {instance_uuid:"source",instance_info:{copy_data_disk_after_clone:false}};
  await context.marketRequest({endpoint:"order/instance/clone/payg",body,account:"user-id"});
  assert.equal(new URL(calls[0].url).pathname,"/api/v1/sub_user/order/instance/clone/payg");
  assert.equal(calls[0].options.method,"POST");
  assert.deepEqual(JSON.parse(calls[0].options.body),body);
});

test("extension allowlist includes clone reads and excludes paid or lifecycle actions", () => {
  const code = fs.readFileSync(path.join(__dirname,"../extension/connect.js"),"utf8");
  const declaration = code.match(/const allowed = new Set\([\s\S]*?\);/)[0];
  const allowed = vm.runInNewContext(declaration + " allowed;");
  for (const endpoint of ["common","instance","instance/snapshot"]) assert.ok(allowed.has(endpoint));
  for (const endpoint of ["order/instance/clone/payg","instance/power_off","instance/snapshot?x=y"])
    assert.ok(!allowed.has(endpoint));
});
