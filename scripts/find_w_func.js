/** 深入探索 botion SDK 找到 w 生成函数。
 *  用法: node scripts/find_w_func.js
 */
const fs = require('fs');
const path = require('path');
const root = path.join(__dirname, '..');

global.self = global; global.window = global; global.top = global; global.parent = global;
global.setTimeout = setTimeout; global.clearTimeout = clearTimeout;
global.setInterval = setInterval; global.clearInterval = clearInterval;
global.console = console;
global.localStorage = { getItem:()=>null, setItem:()=>{}, removeItem:()=>{}, clear:()=>{} };
global.sessionStorage = global.localStorage;
global.document = { createElement:()=>({style:{},addEventListener:()=>{}}), getElementById:()=>null,
    querySelector:()=>null, querySelectorAll:()=>[], addEventListener:()=>{}, documentElement:{style:{}}, body:{style:{}}, cookie:'' };
global.navigator = { userAgent:'Mozilla/5.0', language:'zh-CN', platform:'MacIntel', vendor:'Google Inc.', webdriver:false, plugins:{length:5}, mimeTypes:{length:4} };
global.location = { href:'https://www.leyu.me/user/login', protocol:'https:', host:'www.leyu.me', pathname:'/user/login' };
global.XMLHttpRequest = function() { this.open=()=>{}; this.send=()=>{}; this.setRequestHeader=()=>{}; this.abort=()=>{}; };
global.fetch = async () => ({ ok:true, status:200, json:async()=>({}), text:async()=>'{}' });
global.Image = function(){};
global.crypto = { getRandomValues:(a)=>require('crypto').randomFillSync(a) };

try { eval("(function(){var m={exports:{}},e=m.exports;"+fs.readFileSync(path.join(root, 'data/botion_js/gct4.js'),'utf8')+";return m.exports;})();"); } catch(e) {}
try { eval(fs.readFileSync(path.join(root, 'data/botion_js/bcaptcha.js'),'utf8')); } catch(e) { console.log('bc error:', e.message.substring(0,80)); }

// Deep scan _lib for callable methods
console.log('=== _lib 深度扫描 ===');
if (typeof _lib === 'object') {
    // Check prototype chain
    let proto = Object.getPrototypeOf(_lib);
    while (proto && proto !== Object.prototype) {
        console.log('prototype:', Object.getOwnPropertyNames(proto));
        proto = Object.getPrototypeOf(proto);
    }
    
    // Check all properties
    const allProps = new Set();
    let obj = _lib;
    do {
        Object.getOwnPropertyNames(obj).forEach(p => allProps.add(p));
    } while ((obj = Object.getPrototypeOf(obj)) && obj !== Object.prototype);
    
    console.log('All _lib properties:', [...allProps]);
    
    // _lib might be callable
    console.log('_lib is callable:', typeof _lib === 'function');
    if (typeof _lib === 'function') {
        console.log('_lib.length:', _lib.length);
        console.log('_lib.name:', _lib.name);
        console.log('_lib.toString():', _lib.toString().substring(0, 300));
        
        // Try calling it
        try {
            const r = _lib();
            console.log('_lib() result:', r);
        } catch(e) {
            console.log('_lib() error:', e.message.substring(0, 100));
        }
        try {
            const r = _lib('EKAI');
            console.log('_lib("EKAI"):', r);
        } catch(e) {}
        try {
            const r = _lib({ lang: 'zh', ep: '123' });
            console.log('_lib(obj):', JSON.stringify(r).substring(0, 200));
        } catch(e) {}
    }
}

console.log('\n=== __BOTION__ 深度扫描 ===');
const botion = __BOTION__;
console.log('__BOTION__ keys:', Object.keys(botion));
for (const k of Object.keys(botion)) {
    const v = botion[k];
    console.log(`  ${k}: ${typeof v}`);
    if (typeof v === 'function') {
        console.log(`    ${v.toString().substring(0, 200)}`);
    } else if (typeof v === 'object' && v !== null) {
        console.log(`    keys: ${Object.keys(v).slice(0, 10)}`);
        console.log(`    values: ${JSON.stringify(v).substring(0, 200)}`);
    } else {
        console.log(`    value: ${JSON.stringify(v)}`);
    }
}

// Also look for any other callable objects on the global scope
console.log('\n=== 全局可调用对象 ===');
const globalFuncs = Object.getOwnPropertyNames(global)
    .filter(k => typeof global[k] === 'function')
    .filter(k => !['eval','Function','Object','Array','String','Number','Boolean',
                   'Date','Symbol','Error','RegExp','Map','Set','Promise',
                   'parseInt','parseFloat','isNaN','isFinite','encodeURI',
                   'decodeURI','encodeURIComponent','decodeURIComponent',
                   'parse','Buffer','clearImmediate','setImmediate',
                   'setTimeout','clearTimeout','setInterval','clearInterval',
                   'queueMicrotask','atob','btoa','structuredClone',
                   'gc','require','constructor'].includes(k))
    .filter(k => !k.startsWith('_') && !k.includes('$'));
console.log('Global functions:', globalFuncs);

// Try to find the w generation by looking at URL construction
console.log('\n=== bcaptcha.js 中的 verify URL ===');
const js = fs.readFileSync(path.join(root, 'data/botion_js/bcaptcha.js'), 'utf8');

const verifyIdx = js.indexOf('/verify');
console.log('/verify byte position:', verifyIdx);
// Look for the closure that handles verify
// Search for nearby assign operations
const context = js.substring(Math.max(0, verifyIdx - 200), verifyIdx + 500);
// Remove most whitespace for readability
const compact = context.replace(/\s+/g, ' ');
console.log('Context around /verify:');
console.log(compact.substring(0, 400));
console.log('...');
console.log(compact.substring(compact.length - 200, compact.length));
