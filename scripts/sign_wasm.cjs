#!/usr/bin/env node
/**
 * 乐鱼 X-API-XXX 签名 runner — 直接运行站点 wasm（wasm_api_sign_bg.wasm）。
 *
 * 用法:
 *   node scripts/sign_wasm.cjs <path前缀> [env]
 *   node scripts/sign_wasm.cjs /site/api prod
 *
 * 输出: 64 hex 签名（与浏览器 X-API-XXX 头等价）。
 * 说明: wasm 内部使用 Date.now() 与 Math.random()，每次结果不同，属正常。
 */
"use strict";

const fs = require("fs");
const path = require("path");

const WASM_PATH =
  process.env.LEYU_SIGN_WASM || path.join(__dirname, "wasm_api_sign_bg.wasm");

const decoder = new TextDecoder("utf-8", { ignoreBOM: true, fatal: true });
const encoder = new TextEncoder();

let wasm = null;

function getWasm() {
  if (wasm) return wasm;
  const bytes = fs.readFileSync(WASM_PATH);
  const imports = {
    "./wasm_api_sign_bg.js": {
      __wbg_now_513c8208bd94c09b: () => Date.now(),
      __wbg_random_9f33d5bdc74069f8: () => Math.random(),
      __wbg_floor_a68aa7c1b572044e: Math.floor,
      __wbindgen_throw: (ptr, len) => {
        const mem = new Uint8Array(wasm.exports.memory.buffer);
        throw new Error(`wasm throw: ${decoder.decode(mem.subarray(ptr, ptr + len))}`);
      },
    },
  };
  wasm = new WebAssembly.Instance(new WebAssembly.Module(bytes), imports);
  // 浏览器 webpack 加载时会调用空名导出完成初始化（38464 模块: r[""]()），
  // 不调用则 sign 内部 unreachable。
  if (typeof wasm.exports[""] === "function") wasm.exports[""]();
  return wasm;
}

// ---- wasm-bindgen 胶水复刻 ----
let cachedU8 = null;
function memU8() {
  const w = getWasm();
  if (!cachedU8 || cachedU8.byteLength === 0) {
    cachedU8 = new Uint8Array(w.exports.memory.buffer);
  }
  return cachedU8;
}

let passLen = 0;
function passString(str) {
  const w = getWasm();
  const buf = encoder.encode(str);
  const ptr = w.exports.__wbindgen_malloc(buf.length);
  memU8().subarray(ptr, ptr + buf.length).set(buf);
  passLen = buf.length;
  return ptr;
}

function signOnce(apiPath, env = "prod") {
  const w = getWasm();
  const stack = w.exports.__wbindgen_add_to_stack_pointer(-16);
  try {
    const p0 = passString(apiPath);
    const l0 = passLen;
    const p1 = passString(env);
    const l1 = passLen;
    w.exports.sign(stack, p0, l0, p1, l1);
    const i32 = new Int32Array(w.exports.memory.buffer);
    const rPtr = i32[stack / 4 + 0];
    const rLen = i32[stack / 4 + 1];
    return decoder.decode(memU8().subarray(rPtr, rPtr + rLen));
  } finally {
    w.exports.__wbindgen_add_to_stack_pointer(16);
  }
}

// wasm 内部依赖 Date.now()/Math.random()，实测存在偶发 unreachable，
// 重建实例重试即可（与浏览器端行为一致，浏览器失败时会回退静态表）。
function sign(apiPath, env = "prod", retries = 3) {
  let lastErr;
  for (let i = 0; i < retries; i++) {
    try {
      return signOnce(apiPath, env);
    } catch (e) {
      lastErr = e;
      wasm = null; // 重建实例
      cachedU8 = null;
    }
  }
  throw lastErr;
}

module.exports = { sign };

if (require.main === module) {
  const apiPath = process.argv[2];
  const env = process.argv[3] || "prod";
  if (!apiPath) {
    console.error("usage: node sign_wasm.cjs <apiPath> [env]");
    process.exit(1);
  }
  // 与前端 87802 模块一致的路径归一化
  let p = apiPath;
  if (p.includes("/component")) p = "/site/api";
  if (p.includes("/page/fd")) p = "/fd/api";
  console.log(sign(p, env));
}
