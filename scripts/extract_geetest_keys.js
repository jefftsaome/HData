/** 从 GeeTest JS 文件中提取 RSA 公钥和动态参数。用法: node scripts/extract_geetest_keys.js */

const fs = require('fs');
const path = require('path');

// 加载 gct4.js 和 bcaptcha.js
const root = path.join(__dirname, '..');
const gct4 = fs.readFileSync(path.join(root, 'data/gct4.js'), 'utf8');
const bc = fs.readFileSync(path.join(root, 'data/bcaptcha.js'), 'utf8');

// 模拟浏览器环境
global.window = global;
global.document = { createElement: () => ({}), addEventListener: () => {} };
global.navigator = { userAgent: 'Mozilla/5.0', language: 'zh-CN' };
global.location = { href: 'https://www.1d8e47.vip:9249/', protocol: 'https:' };
global.XMLHttpRequest = function() { this.open = () => {}; this.send = () => {}; };
global.Image = function() {};

try {
    // 执行 GeeTest 代码
    eval(gct4);
    eval(bc);

    // 尝试提取全局 GeeTest 对象
    const geeKeys = Object.keys(global).filter(k =>
        k.includes('Geetest') || k.includes('_gct') || k.includes('botion') ||
        k.includes('gee') || k.includes('captcha') || k.includes('gct')
    );
    console.log('GeeTest 全局对象:', geeKeys);

    // 尝试提取 _gct 对象的方法
    if (global._gct) {
        console.log('_gct keys:', Object.keys(global._gct));
        if (global._gct.getPublicKey) {
            console.log('Public key:', global._gct.getPublicKey());
        }
    }

    // 搜索 RSA 相关
    const allKeys = Object.keys(global);
    for (const k of allKeys) {
        try {
            const v = global[k];
            if (typeof v === 'object' && v !== null) {
                const methods = Object.keys(v);
                if (methods.some(m => m.includes('encrypt') || m.includes('RSA') || m.includes('public'))) {
                    console.log(`${k} methods:`, methods.slice(0, 20));
                }
            }
        } catch(e) {}
    }

} catch(e) {
    console.error('Execution error:', e.message);
}
