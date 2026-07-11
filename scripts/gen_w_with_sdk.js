/** 使用真实 botion SDK 生成 w 参数。
 *  用法: node scripts/gen_w_with_sdk.js
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const root = path.join(__dirname, '..');

// ===== Polyfill =====
global.self = global; global.window = global; global.top = global; global.parent = global;
global.setTimeout = setTimeout; global.clearTimeout = clearTimeout;
global.setInterval = setInterval; global.clearInterval = clearInterval;
global.console = console;
class Store { constructor() { this._d = {}; } getItem(k) { return this._d[k] || null; } setItem(k, v) { this._d[k] = String(v); } removeItem(k) { delete this._d[k]; } clear() { this._d = {}; } }
global.localStorage = new Store(); global.sessionStorage = new Store();
global.document = { createElement: (t) => ({ tagName: t.toUpperCase(), style: {}, addEventListener: () => {}, getBoundingClientRect: () => ({x:0,y:0,w:300,h:200}), querySelector: () => null, querySelectorAll: () => [] }), getElementById: () => null, querySelector: () => null, querySelectorAll: () => [], addEventListener: () => {}, documentElement: { style: {} }, body: { style: {}, appendChild: () => {} }, cookie: '' };
global.navigator = { userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36', language: 'zh-CN', platform: 'MacIntel', vendor: 'Google Inc.', webdriver: false, plugins: { length: 5, item: () => null }, mimeTypes: { length: 4, item: () => null }, hardwareConcurrency: 8, deviceMemory: 8, connection: { effectiveType: '4g' } };
global.location = { href: 'https://www.leyu.me/user/login', protocol: 'https:', host: 'www.leyu.me', hostname: 'www.leyu.me', pathname: '/user/login', origin: 'https://www.leyu.me' };
global.XMLHttpRequest = function() { this.open = () => {}; this.send = () => {}; this.setRequestHeader = () => {}; this.abort = () => {}; this.onload = null; this.onerror = null; this.readyState = 4; this.status = 200; this.responseText = '{}'; };
global.fetch = async () => ({ ok: true, status: 200, json: async () => ({}), text: async () => '{}' });
global.Image = function() { this.naturalWidth = 300; this.naturalHeight = 200; this.complete = true; this.src = ''; this.addEventListener = () => {}; };
global.WebSocket = function() { this.send = () => {}; this.close = () => {}; };
global.MutationObserver = function() { this.observe = () => {}; this.disconnect = () => {}; };
global.crypto = { getRandomValues: (a) => require('crypto').randomFillSync(a), subtle: require('crypto').webcrypto.subtle, randomUUID: () => require('crypto').randomUUID() };
global.PerformanceObserver = function() {};

// 加载 SDK
try {
    eval("(function(){var m={exports:{}},e=m.exports;" + fs.readFileSync(path.join(root, 'data/botion_js/gct4.js'), 'utf8') + ";return m.exports;})();");
} catch(e) {}
try {
    eval(fs.readFileSync(path.join(root, 'data/botion_js/bcaptcha.js'), 'utf8'));
} catch(e) { console.log('bc error (non-fatal):', e.message.substring(0, 100)); }

// ===== 检查 SDK 提供的功能 =====
console.log('=== 可用功能 ===');
console.log('_lib:', typeof _lib, Object.keys(_lib));
console.log('__BOTION__.ctKey:', __BOTION__.ctKey);
console.log('__BOTION__.ctStore:', JSON.stringify(__BOTION__.ctStore).substring(0, 150));

// 搜索 SDK 中暴露的可用函数
const sdkKeys = Object.keys(global).filter(k => 
    !['console','Buffer','process','setTimeout','clearTimeout','setInterval','clearInterval',
      'global','self','window','top','parent','document','navigator','location',
      'localStorage','sessionStorage','XMLHttpRequest','fetch','Image','WebSocket',
      'crypto','MutationObserver','Array','Object','String','Number','Boolean',
      'Date','Math','JSON','RegExp','Map','Set','Promise','Symbol','Error',
      'isNaN','isFinite','parseInt','parseFloat','encodeURI','decodeURI',
      'Infinity','NaN','undefined','Function','Proxy','Reflect',
      'clearImmediate','setImmediate','queueMicrotask','structuredClone',
      'atob','btoa','gc','URL','URLSearchParams','TextEncoder','TextDecoder',
      'require','module','exports','__dirname','__filename','root','path','fs',
      'crypto','Store','performance','PerformanceObserver','performance'].includes(k)
);
console.log('\nSDK 暴露的全局:', sdkKeys);

// 尝试找到加密函数
// 在 bcaptcha.js 中，加密函数可能在闭包内，需要通过某种方式调用
// 让我们尝试通过 _lib 来触发

// _lib 的 debug 信息
console.log('\n=== _lib 详解 ===');
if (typeof _lib === 'object') {
    const desc = Object.getOwnPropertyDescriptor(global, '_lib');
    console.log('descriptor:', desc ? 'configurable=' + desc.configurable + ', enumerable=' + desc.enumerable : 'N/A');
    console.log('prototype:', Object.getPrototypeOf(_lib));
    
    // _lib 可能是一个函数对象，同时也有属性
    console.log('_lib keys:', Object.keys(_lib));
    console.log('_lib values:', Object.values(_lib));
    
    // 检查是否有隐藏的 getter/setter
    const proto = Object.getPrototypeOf(_lib);
    console.log('proto keys:', Object.keys(proto));
    
    // 检查 __BOTION__ 的功能
    console.log('\n=== __BOTION__ 功能 ===');
    for (const k of Object.keys(__BOTION__)) {
        console.log(`  ${k}: ${typeof __BOTION__[k]}`);
    }
}

// 尝试手动实现 w 生成（在 Node.js 中，使用 Node crypto 而不是 PyCryptodome）
console.log('\n=== 手动 w 生成 (Node.js) ===');

function generateW(loadData, captchaId, coordsStr) {
    const lotNumber = loadData.lot_number;
    const powDetail = loadData.pow_detail;
    
    // Parse coordinates
    const coordsArray = coordsStr.split('|').map(p => p.split(',').map(Number));
    
    // Lot parser
    const ctStore = __BOTION__.ctStore;
    const ctKey = __BOTION__.ctKey;
    const mapping = ctStore[ctKey];
    const lotParserKey = Object.keys(mapping)[0];
    const lotParserValue = mapping[lotParserKey];
    
    // Extract lot parser values
    function parseSlices(parts) {
        const result = parts.split('+.+').map(part => {
            if (part.includes('+')) {
                return part.split('+').map(s => {
                    const m = s.match(/\[(\d+):(\d+)\]/);
                    return m ? [parseInt(m[1]), parseInt(m[2])] : [0, 0];
                });
            }
            const m = part.match(/\[(\d+):(\d+)\]/);
            return m ? [[parseInt(m[1]), parseInt(m[2])]] : [[0, 0]];
        });
        return result;
    }
    
    const keyParts = parseSlices(lotParserKey);
    const valueParts = parseSlices(lotParserValue);
    
    function buildStr(parsed, num) {
        return parsed.map(p => p.map(s => {
            const start = s[0], end = s[1] + 1;
            return num.substring(start, end);
        }).join('')).join('.');
    }
    
    const key = buildStr(keyParts, lotNumber);
    const val = buildStr(valueParts, lotNumber);
    
    // Build nested dict from key path
    function buildDict(keys, value) {
        const parts = keys.split('.');
        let result = {};
        let current = result;
        for (let i = 0; i < parts.length; i++) {
            if (i === parts.length - 1) {
                current[parts[i]] = value;
            } else {
                current[parts[i]] = {};
                current = current[parts[i]];
            }
        }
        return result;
    }
    
    // Generate pow
    const hashFunc = powDetail.hashfunc;
    const version = powDetail.version;
    const bits = parseInt(powDetail.bits);
    const dt = powDetail.datetime;
    const br = bits % 4;
    const bd = Math.floor(bits / 4);
    const prefix = '0'.repeat(bd);
    const powTemplate = `${version}|${bits}|${hashFunc}|${dt}|${captchaId}|${lotNumber}||`;
    
    let powData = null;
    for (let attempt = 0; attempt < 100; attempt++) {
        const rand = Array.from({length: 4}, () => 
            (65536 * (1 + Math.random()) | 0).toString(16).slice(-4)
        ).join('');
        const combined = powTemplate + rand;
        const hash = require('crypto').createHash(hashFunc).update(combined).digest('hex');
        if (br === 0) {
            if (hash.startsWith(prefix)) {
                powData = { pow_msg: combined, pow_sign: hash };
                break;
            }
        } else if (hash.startsWith(prefix) && prefix.length <= [0, 7, 3, 1][br]) {
            powData = { pow_msg: combined, pow_sign: hash };
            break;
        }
    }
    if (!powData) throw new Error('Failed PoW');
    
    // Build e_obj
    const eobj = {
        ...powData,
        ...buildDict(key, val),  // lot_parser dynamic key
        ...Object.fromEntries(Object.entries(_lib)),  // EKAI: "y7R8"
        "biht": "1426265548",
        "device_id": "",
        "em": {"cp": 0, "ek": "11", "nt": 0, "ph": 0, "sc": 0, "si": 0, "wd": 1},
        "gee_guard": {"roe": {"auh": "3", "aup": "3", "cdc": "3", "egp": "3",
                               "res": "3", "rew": "3", "sep": "3", "snh": "3"}},
        "ep": "123",
        "geetest": "captcha",
        "lang": "zh",
        "lot_number": lotNumber,
        "userresponse": coordsArray,
        "passtime": Math.floor(Math.random() * 600 + 600),
    };
    
    const eobjJson = JSON.stringify(eobj, Object.keys(eobj).sort());
    // Actually geetest_signer uses specific separators: separators=(',', ':')
    // But the standard is: JSON.stringify without separators = with spaces
    // Let's use compact format
    const eobjCompact = JSON.stringify(eobj, Object.keys(eobj).sort()).replace(/ /g, '');
    // Wait, the separators are actually the default. Let me just use the native JSON format.
    // The GeeTest SDK uses JSON.stringify(obj) without special separators
    // But our Python code uses separators=(',', ':') - which is the same as default JS JSON.stringify
    
    console.log(`e_obj: ${eobjCompact.length} bytes`);
    
    // AES-CBC encrypt
    const randKey = Array.from({length: 4}, () => 
        (65536 * (1 + Math.random()) | 0).toString(16).slice(-4)
    ).join('');
    
    const cipher = crypto.createCipheriv('aes-128-cbc', randKey, '0000000000000000');
    let encrypted = cipher.update(eobjCompact, 'utf8', 'hex');
    encrypted += cipher.final('hex');
    
    // RSA encrypt
    const rsaPub = `-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDB45NNFhRGWzMFPn9I7k7IexS5
XviJR3E9Je7L/350x5l9AtwdlFH3ndXRwQwprLaptNbfs0AKe2cZ4XclZfCa9icV
eZIhrvkYmc4IyNaG10iyCjYDviMYymvCtZcGWSoSGdC/Bcn2UCOiHSMwM...

    // Wait, I need to construct the RSA key from modulus and exponent
    const NodeRSA = require('crypto').publicEncrypt;
    // Actually Node's crypto.publicEncrypt needs a proper PEM key
    // Let me use the forge library or direct crypto
    // For now, just use crypto.publicEncrypt with proper key
    
    // Actually, crypto.publicEncrypt needs PEM. Let me create one.
    const n = BigInt('0x00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81');
    const e = BigInt('0x10001');
    
    // Convert to DER for crypto.publicEncrypt
    function bigIntToPem(n, e) {
        // This requires DER encoding which is complex
        // For now, use a simpler approach
        return null;
    }
    
    console.log(`AES key: ${randKey}`);
    console.log(`AES encrypted: ${encrypted.substring(0, 40)}...`);
    console.log(`e_obj compact length: ${eobjCompact.length}`);
    
    return { eobj, eobjCompact, encrypted, randKey };
}

// Just build the e_obj for now (skip RSA which needs PEM encoding)
console.log('\n--- Building e_obj with SDK keys ---');

const sampleCoords = '74,124|235,132|176,65';
const sampleLoad = {
    lot_number: 'test1234567890123456789012345678',
    pow_detail: { version: '1', bits: 0, datetime: '2026-07-04T12:00:00.000+08:00', hashfunc: 'md5' },
};

try {
    const result = generateW(sampleLoad, 'eaffad4f65a38a259ae369faf0c2f1a3', sampleCoords);
    console.log('Done');
} catch(e) {
    console.log('Error:', e.message);
}
