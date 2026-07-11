/** 检查 botion GeeTest SDK 的 _lib 和 __BOTION__ 对象。
 *  用法: node scripts/check_sdk.js
 */
const fs = require('fs');
const path = require('path');
const root = path.join(__dirname, '..');

// ===== 浏览器 polyfill (精简版) =====
global.self = global; global.window = global; global.top = global; global.parent = global;
global.setTimeout = setTimeout; global.clearTimeout = clearTimeout;
global.setInterval = setInterval; global.clearInterval = clearInterval;
global.console = console;
global.localStorage = { getItem:()=>null, setItem:()=>{}, removeItem:()=>{}, clear:()=>{} };
global.sessionStorage = global.localStorage;
global.document = { createElement:()=>({style:{}}), getElementById:()=>null,
    querySelector:()=>null, querySelectorAll:()=>[], addEventListener:()=>{},
    documentElement:{style:{}}, body:{style:{}}, cookie:'' };
global.navigator = { userAgent:'Mozilla/5.0', language:'zh-CN', platform:'MacIntel',
    vendor:'Google Inc.', webdriver:false, plugins:{length:5}, mimeTypes:{length:4} };
global.location = { href:'https://www.leyu.me/user/login', protocol:'https:',
    host:'www.leyu.me', pathname:'/user/login' };
global.XMLHttpRequest = function() { this.open=()=>{}; this.send=()=>{}; this.setRequestHeader=()=>{}; this.abort=()=>{}; };
global.fetch = async () => ({ ok:true, status:200, json:async()=>({}), text:async()=>'{}' });
global.Image = function(){};
global.crypto = { getRandomValues:(a)=>require('crypto').randomFillSync(a) };

const gct4 = fs.readFileSync(path.join(root, 'data/botion_js/gct4.js'), 'utf8');
const bc = fs.readFileSync(path.join(root, 'data/botion_js/bcaptcha.js'), 'utf8');
try { eval("(function(){var m={exports:{}},e=m.exports;"+gct4+";return m.exports;})();"); } catch(e) {}
try { eval(bc); } catch(e) { console.log('bc error:', e.message); }

// 1. Check _lib
console.log('=== _lib ===');
if (typeof _lib !== 'undefined') {
    console.log('type:', typeof _lib);
    if (typeof _lib === 'object') {
        const keys = Object.keys(_lib);
        console.log('keys:', keys);
        for (const k of keys) {
            console.log(`  _lib["${k}"] = ${JSON.stringify(_lib[k])}`);
        }
    } else if (typeof _lib === 'function') {
        // Try calling it
        try {
            const result = _lib('ZAhG');
            console.log('  _lib("ZAhG"):', result);
        } catch(e) {}
        try {
            const result = _lib('EKAI');
            console.log('  _lib("EKAI"):', result);
        } catch(e) {}
        console.log('_lib.toString():', _lib.toString().substring(0, 200));
    }
} else {
    console.log('_lib is UNDEFINED');
}

// 2. Check __BOTION__
console.log('\n=== __BOTION__ ===');
if (typeof __BOTION__ !== 'undefined') {
    console.log('__BOTION__ keys:', Object.keys(__BOTION__));
    for (const k of Object.keys(__BOTION__)) {
        const v = __BOTION__[k];
        if (typeof v === 'object' && v !== null) {
            console.log(`  ${k}:`, JSON.stringify(v).substring(0, 200));
        } else if (typeof v === 'function') {
            console.log(`  ${k}: function`);
            console.log(`    ${v.toString().substring(0, 150)}`);
        } else {
            console.log(`  ${k}: ${JSON.stringify(v)}`);
        }
    }
    
    // Check ctStore
    if (__BOTION__.ctStore) {
        console.log('\n  ctStore:', JSON.stringify(__BOTION__.ctStore).substring(0, 300));
    }
} else {
    console.log('__BOTION__ is UNDEFINED');
}

// 3. Check global GeeTest objects
console.log('\n=== Global GeeTest keys ===');
const allKeys = Object.keys(global).filter(k => 
    k !== 'console' && k !== 'Buffer' && !k.startsWith('_') && 
    !['Array','Object','String','Number','Boolean','Date','Math','JSON','RegExp',
      'Map','Set','Promise','Symbol','Error','Function','isNaN','isFinite',
      'parseInt','parseFloat','encodeURI','decodeURI','Infinity','NaN','undefined',
      'setTimeout','clearTimeout','setInterval','clearInterval','global',
      'self','window','top','parent','localStorage','sessionStorage',
      'document','navigator','location','XMLHttpRequest','fetch','Image','crypto',
      'fs','path','root','require','module','exports','__dirname','__filename',
      'Buffer','process','gc','URL','URLSearchParams','TextEncoder','TextDecoder',
      'clearImmediate','setImmediate','queueMicrotask','structuredClone','atob','btoa',
    ].includes(k)
);
console.log('  Additional globals:', allKeys);
