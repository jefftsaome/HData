/** Node.js 尝试加载 botion GeeTest SDK 并生成 w 参数。
 *  提供完整 polyfill 模拟浏览器环境。
 *  用法: node scripts/run_geetest_sdk.js
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const root = path.join(__dirname, '..');

// ===== 浏览器 polyfill =====
global.self = global;
global.window = global;
global.top = global;
global.parent = global;

// setTimeout / setInterval
global.setTimeout = setTimeout;
global.clearTimeout = clearTimeout;
global.setInterval = setInterval;
global.clearInterval = clearInterval;

// console
global.console = console;

// Math / Date / JSON - native

// localStorage
class Storage {
    constructor() { this._data = {}; }
    getItem(k) { return this._data[k] || null; }
    setItem(k, v) { this._data[k] = String(v); }
    removeItem(k) { delete this._data[k]; }
    clear() { this._data = {}; }
}
global.localStorage = new Storage();
global.sessionStorage = new Storage();

// document
global.document = {
    createElement: (tag) => {
        const el = {
            tagName: tag.toUpperCase(),
            attributes: {},
            style: {},
            children: [],
            parentNode: null,
            addEventListener: () => {},
            removeEventListener: () => {},
            getAttribute: (k) => el.attributes[k],
            setAttribute: (k, v) => { el.attributes[k] = v; },
            appendChild: (c) => { el.children.push(c); c.parentNode = el; },
            getBoundingClientRect: () => ({ x: 0, y: 0, w: 300, h: 200, top: 0, left: 0, right: 300, bottom: 200 }),
            querySelector: () => null,
            querySelectorAll: () => [],
            contains: () => false,
            cloneNode: () => ({}),
        };
        if (tag === 'canvas') {
            el.getContext = () => ({
                drawImage: () => {},
                getImageData: () => ({ data: new Uint8Array(400) }),
                measureText: () => ({ width: 10 }),
            });
            el.toDataURL = () => '';
        }
        if (tag === 'img') {
            el.src = '';
            el.naturalWidth = 300;
            el.naturalHeight = 200;
            el.complete = true;
        }
        if (tag === 'script') {
            el.src = '';
            el.textContent = '';
        }
        return el;
    },
    createElementNS: (ns, tag) => global.document.createElement(tag),
    getElementById: () => null,
    getElementsByClassName: () => [],
    getElementsByTagName: () => [],
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    removeEventListener: () => {},
    documentElement: { style: {}, clientWidth: 1920, clientHeight: 1080 },
    body: { style: {}, appendChild: () => {}, clientWidth: 1920, clientHeight: 1080 },
    head: { appendChild: () => {} },
    createTextNode: () => ({}),
    cookie: '',
};

// navigator
global.navigator = {
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
    appVersion: '5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    platform: 'MacIntel',
    vendor: 'Google Inc.',
    language: 'zh-CN',
    languages: ['zh-CN', 'zh'],
    cookieEnabled: true,
    doNotTrack: null,
    hardwareConcurrency: 8,
    maxTouchPoints: 0,
    product: 'Gecko',
    productSub: '20030107',
    plugins: { length: 5, item: () => null, namedItem: () => null,
               refresh: () => {}, [Symbol.iterator]: function*() {} },
    mimeTypes: { length: 4, item: () => null, namedItem: () => null,
                 [Symbol.iterator]: function*() {} },
    webdriver: false,
    getBattery: () => Promise.resolve({ level: 1, charging: true }),
    connection: { effectiveType: '4g', rtt: 50, downlink: 10 },
    deviceMemory: 8,
};

// location
global.location = {
    href: 'https://www.leyu.me/user/login',
    protocol: 'https:',
    host: 'www.leyu.me',
    hostname: 'www.leyu.me',
    port: '',
    pathname: '/user/login',
    search: '',
    hash: '',
    origin: 'https://www.leyu.me',
    ancestorOrigins: [],
    assign: () => {},
    replace: () => {},
    reload: () => {},
};

// history
global.history = {
    length: 1,
    state: null,
    pushState: () => {},
    replaceState: () => {},
    back: () => {},
    forward: () => {},
    go: () => {},
};

// Performance
global.performance = {
    now: () => Date.now(),
    timing: { navigationStart: Date.now() - 10000 },
    getEntries: () => [],
    mark: () => {},
    measure: () => {},
};

// screen
global.screen = {
    width: 1920,
    height: 1080,
    availWidth: 1920,
    availHeight: 1050,
    colorDepth: 24,
    pixelDepth: 24,
};

// XMLHttpRequest
global.XMLHttpRequest = function() {
    this.readyState = 0;
    this.status = 0;
    this.responseText = '';
    this.response = '';
    this.onreadystatechange = null;
    this.onload = null;
    this.onerror = null;
};
XMLHttpRequest.prototype.open = function(method, url, async) {
    this._method = method;
    this._url = url;
};
XMLHttpRequest.prototype.setRequestHeader = function(k, v) {};
XMLHttpRequest.prototype.send = function(data) {
    // 异步模拟响应
    const self = this;
    setTimeout(() => {
        self.readyState = 4;
        self.status = 200;
        self.responseText = '{}';
        self.response = '{}';
        if (self.onload) self.onload();
        if (self.onreadystatechange) self.onreadystatechange();
    }, 10);
};
XMLHttpRequest.prototype.abort = function() {};
XMLHttpRequest.prototype.getAllResponseHeaders = () => '';
XMLHttpRequest.prototype.getResponseHeader = () => '';

// fetch polyfill
global.fetch = async (url, opts) => ({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => ({}),
    text: async () => '{}',
    blob: async () => new Blob(),
    arrayBuffer: async () => new ArrayBuffer(0),
    headers: new Map(),
});

// WebSocket
global.WebSocket = function(url) {
    this.url = url;
    this.readyState = 0;
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
    setTimeout(() => {
        this.readyState = 1;
        if (this.onopen) this.onopen();
    }, 10);
};
WebSocket.prototype.send = function(data) {};
WebSocket.prototype.close = function() {};
WebSocket.CONNECTING = 0;
WebSocket.OPEN = 1;
WebSocket.CLOSING = 2;
WebSocket.CLOSED = 3;

// Image
global.Image = function() { this.naturalWidth = 300; this.naturalHeight = 200; this.complete = true; this.src = ''; };

// Audio
global.Audio = function() { this.play = () => Promise.resolve(); this.pause = () => {}; };

// crypto subtle (Web Crypto API)
global.crypto = {
    subtle: crypto.webcrypto.subtle,
    getRandomValues: (arr) => crypto.randomFillSync(arr),
    randomUUID: () => crypto.randomUUID(),
};

// MutationObserver
global.MutationObserver = function(cb) { this.observe = () => {}; this.disconnect = () => {}; };
global.IntersectionObserver = function(cb) { this.observe = () => {}; this.disconnect = () => {}; };
global.ResizeObserver = function(cb) { this.observe = () => {}; this.disconnect = () => {}; };

// 加载 SDK
console.log('=== Loading GeeTest SDK ===');
const gct4 = fs.readFileSync(path.join(root, 'data/botion_js/gct4.js'), 'utf8');
const bcaptcha = fs.readFileSync(path.join(root, 'data/botion_js/bcaptcha.js'), 'utf8');

try {
    console.log('Executing gct4.js...');
    // gct4 might use UMD - try loading it
    eval("(function() { var module = {exports: {}}, exports = module.exports; " + gct4 + "; return module.exports; })();");
    console.log('  gct4.js done');
} catch(e) {
    console.log('  gct4.js error (non-fatal):', e.message.substring(0, 100));
}

try {
    console.log('Executing bcaptcha.js...');
    eval(bcaptcha);
    console.log('  bcaptcha.js done');
    
    // Check what was created in global scope
    const keys = Object.keys(global).filter(k => 
        !['console','Buffer','process','setTimeout','clearTimeout','setInterval','clearInterval',
          'global','self','window','top','parent','document','navigator','location','history',
          'localStorage','sessionStorage','screen','crypto','fetch','WebSocket','XMLHttpRequest',
          'Image','Audio','performance','MutationObserver','IntersectionObserver','ResizeObserver',
          'Storage','Array','Object','String','Number','Boolean','Date','Math','JSON','RegExp',
          'Map','Set','WeakMap','WeakSet','Promise','Symbol','Reflect','Proxy','Error','TypeError',
          'RangeError','SyntaxError','ReferenceError','EvalError','URIError','Function',
          'isNaN','isFinite','parseInt','parseFloat','encodeURI','encodeURIComponent',
          'decodeURI','decodeURIComponent','Infinity','NaN','undefined'].includes(k)
    );
    console.log('\nNew globals from SDK:', keys.slice(0, 30));
    
    // Check for specific GeeTest objects
    if (typeof _gct !== 'undefined') {
        console.log('\n_gct:', typeof _gct);
        if (typeof _gct === 'function') {
            const result = _gct({ lang: 'zh', ep: '123' });
            console.log('  _gct result:', result);
        }
    }
    
} catch(e) {
    console.log('  bcaptcha.js error:', e.message.substring(0, 200));
    console.log('  Stack:', e.stack.substring(0, 300));
}
