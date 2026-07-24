#!/usr/bin/env node
/**
 * 开发期兼容入口 — 实现已移入 hdata 包（hdata/auth/sign_wasm.cjs，随 wheel 分发）。
 * 本文件仅做转发，避免两份签名逻辑漂移；修改签名逻辑请改包内文件。
 *
 * 用法（与历史一致）:
 *   node scripts/sign_wasm.cjs <path前缀> [env]
 *   MSYS_NO_PATHCONV=1 node scripts/sign_wasm.cjs /site/api prod
 */
"use strict";

const impl = require("../hdata/auth/sign_wasm.cjs");

if (require.main === module) {
  const apiPath = process.argv[2];
  const env = process.argv[3] || "prod";
  if (!apiPath) {
    console.error("usage: node scripts/sign_wasm.cjs <apiPath> [env]");
    process.exit(1);
  }
  // 与前端 87802 模块一致的路径归一化
  let p = apiPath;
  if (p.includes("/component")) p = "/site/api";
  if (p.includes("/page/fd")) p = "/fd/api";
  console.log(impl.sign(p, env));
}

module.exports = impl;
