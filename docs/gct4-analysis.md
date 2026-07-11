# gct4.js 逆向分析

> 文件: `https://static.botion.com/v1/gct/gct4.614b49d4a6f9b9c251919ce8a63098bd.js`
> 大小: 3089 bytes
> 日期: 2026-07-04

## 概述

gct4.js 是 GeeTest v4 的**核心完整性校验模块**。它不是验证码加密引擎（AES/RSA 在 bcaptcha.js 和 GeeTest SDK 中），而是负责**浏览器环境指纹的哈希校验**——防止客户端代码被篡改。

## 混淆技术

1. **控制流平坦化**: 所有循环被转换为 `switch-case` 状态机（`for(;huQ!==14;){switch(huQ){...}}`）
2. **字符串加密**: 所有字符串被 XOR 编码后 URL-encode，密钥 `6Kjyv[`
3. **二维数组打乱**: `AMbjL.CGk.pQo(12,3)` 生成 12×12 置换表，用于数组索引混淆
4. **UMD 封装**: 支持 CommonJS、AMD、全局变量三种导出方式

## 解码后的字符串表

```
[5]  'function'          [51] 'call'
[6]  '_gct'              [55] 'toString'
[22] '[object Object]'   [58] 'nqfq'
[32] 'length'            [61] 'object'
[33] 'charCodeAt'        [66] 'undefined'
[34] 'exports'           [70] 'amd'
[49] 'lang'              [84] 'Geetest'
                         [96] 'prototype'
                         [99] 'ep'
```

## 核心逻辑

```javascript
// 1. DJB 哈希函数
function FIHs(str) {
    var hash = 5381;
    var len = str.length;
    var i = 0;
    while (len--) {
        hash = (hash << 5) + hash + str.charCodeAt(i++);
    }
    hash &= ~(1 << 31);  // 清除符号位
    return hash;
}

// 2. 对象哈希（用于环境指纹校验）
function Gpxq(obj) {
    if (obj['lang'] && obj['ep']) {
        // 如果对象有 lang 和 ep 属性，先对函数本身做双重哈希加盐
        obj['nqfq'] = FIHs(Gpxq.toString() + FIHs(FIHs.toString())) + '';
    }
    return FIHs(FIHs.toString());
}

// 3. 导出为全局 _gct
// 外部调用: _gct(obj) → 返回哈希值
```

## 用途

`_gct` 在 GeeTest 生态中的作用：

1. **代码完整性**: `FIHs(FIHs.toString())` 对哈希函数本身做散列，如果被篡改则值不同
2. **环境指纹校验**: 对浏览器环境对象做哈希，检测 WebDriver、headless 等特征
3. **参数签名**: bcaptcha.js 调用 `_gct(obj)` 来生成 `ep`、`nqfq` 等字段的校验值

## 与验证码流程的关系

```
gct4.js 负责:  环境指纹哈希 (ep, nqfq 等字段)
bcaptcha.js:   验证码 UI + w 参数中的 e_obj 组装
GeeTest SDK:   完整加密管线 (PoW + AES-CBC + RSA-1024)
```

## 对我们代码的影响

- `geetest_signer.py` 中的 `generate_w()` 已经包含了正确的 `ep: "123"` 和 `nqfq` 相关字段
- gct4.js 不负责 `captcha_output` 的生成
- `captcha_output` 在 bcaptcha.js 或更上层的 GeeTest SDK 中生成
- gct4.js 的 DJB hash 和我们的代码无关——它在浏览器端做完整性自检
