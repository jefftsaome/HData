# 乐鱼平台认证与参数解密研究

> **目标:** 在不依赖 Chrome 浏览器的环境下，获取乐鱼 WebSocket 连接所需的 JWT token。
>
> **状态:** 2026-06-27 — params 解密算法已完全逆向成功（AES-ECB, key=ttl+AES）。
>   Cloudflare/Kuafu 网络层面的绕过尚未解决。

---

## 目录

1. [整体认证流程](#1-整体认证流程)
2. [params 解密算法](#2-params-解密算法)
3. [AnalysisUrlUtils 逆向过程](#3-analysisurlutils-逆向过程)
4. [Token 获取方案](#4-token-获取方案)
5. [WS URL 构造](#5-ws-url-构造)
6. [剩余问题](#6-剩余问题)

---

## 1. 整体认证流程

```
用户通过浏览器登录乐鱼
    │
    1. 主站 (nhfspi.vip / vadp.irwrek.com) 认证
    │  - Cloudflare 防护
    │  - 账号密码登录
    │  - 生成加密 params + ttl + signature
    │
    2. 跳转到游戏 iframe:
    │  pc.lisxdc.com:2083/egret/hall?params=xxx&ttl=xxx&signature=xxx
    │  - params: AES-ECB 加密的 JSON（≈2008 base64 字符）
    │  - ttl: 时间戳（如 `1782535308601`）
    │  - signature: 签名（用途未确认）
    │
    3. 页面内 inline script 解密 params:
    │  - Key = ttl + "AES" → "1782535308601AES" (16 字节)
    │  - AES-ECB 解密 → JSON.parse → window.urlParams
    │  - 包含: playerId, token, backendDomainUrl, deviceId 等
    │
    4. 从 localStorage 读取 + URL params 设置:
    │  - localStorage.setItem("token", urlParams.token)
    │  - localStorage.setItem("fixedDeviceId", ...)
    │
    5. 构造 WS URL:
    │  wss://wsproxy.{host}:{port}/?playerId=...&jwtToken=...&deviceId=...&deviceType=2&platform=6
    │  - host/port 来自 backendDomainUrlList（逗号分隔的多个地址）
    │
    6. WebSocket 连接
    │  - curl_cffi 伪装 TLS 指纹
    │  - wsproxy 有 Cloudflare 防护（HTTP 500）
    │  - 备份端点 pc.lisxdc.com:2083/ws 已失效（HTTP 404）
```

---

## 2. params 解密算法

### 2.1 解密函数（Python，已验证）

```python
import base64, json
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def decrypt_params(params_b64: str, ttl: str) -> dict:
    """解密乐鱼 params 参数。

    Args:
        params_b64: URL 中的 params 参数值（base64 编码，保留 + 号）
        ttl: URL 中的 ttl 参数值

    Returns:
        dict 包含 playerId, token, backendDomainUrl 等
    """
    key = (ttl + "AES").encode("ascii")       # 16 字节 ASCII
    ct = base64.b64decode(params_b64)          # base64 → 密文
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    padded = cipher.decryptor().update(ct) + cipher.decryptor().finalize()
    data = padded[: -padded[-1]]               # PKCS7 去填充
    return json.loads(data)
```

### 2.2 算法参数

| 参数 | 值 |
|:----|:----|
| 加密算法 | AES-128 |
| 模式 | ECB（无 IV） |
| 密钥 | `ttl + "AES"`，如 `"1782535308601AES"`（ASCII） |
| 编码 | Base64（标准，非 base64url） |
| 填充 | PKCS7 |
| 输出 | JSON 字符串（**无 GZIP 压缩**）|

### 2.3 解密后的数据结构

```json
{
  "playerId": 105452510,
  "token": "eyJhbGciOiJIUzI1NiJ9...",
  "backendDomainUrl": "1j2cdazl.com:21027",
  "backendDomainUrlList": "1j2cdazl.com:21027,64vlwlq.com:18026,2umtefe.com:19026",
  "domainUrlList": "lisxdc.com:2083,...",
  "deviceType": 1,
  "sessionid": "1370884383",
  "agentId": 2933,
  "currency": "CNY",
  "brandType": 1,
  "h5DomainUrlList": "wby45m.com,...",
  ...
}
```

### 2.4 脚本

```bash
# 从 CDP 获取 params+ttl 并解密
uv run python scripts/decrypt_params.py

# 直接传入解密（离线场景）
uv run python scripts/decrypt_params.py --params 'Gwd/...' --ttl '1782535308601'
```

---

## 3. AnalysisUrlUtils 逆向过程

### 3.1 查找过程

`AnalysisUrlUtils.getLoginData()` 是解密 params 的入口函数。查找路径：

1. **游戏页面 URL**: `pc.lisxdc.com:2083/egret/hall?params=...`
2. **内联 script (#8)**: 8579 字节，调用 `e.DataHandle.aesDecrypt(n, i+"AES")`
   - `e` = `window`（函数参数传递）
   - `n` = URL 解码后的 params
   - `i` = ttl
3. **vendor bundle**: `vendor-GH5N...release.js` (2.8MB)，包含 CryptoJS、Vue、Element Plus 等
4. **动态加载 chunk**: `index-GH5N...release.js` (57KB)，引用了 `AnalysisUrlUtils`
5. **assets chunk**: `assets-GH5N...release.js` (8.4MB)，也引用了 `AnalysisUrlUtils`

### 3.2 DataHandle 模块

`window.DataHandle` — 构造函数，`new DataHandle()` 返回空实例。

`window.dataHandle.default` — 包含解密方法的模块对象：

```javascript
// AES 解密（返回 WordArray）
dataHandle.default.aesDecrypt = function(t, e, r) {
    r = "CBC" === r ? CryptoJS.mode.CBC : CryptoJS.mode.ECB;
    if (typeof e === "string") e = CryptoJS.enc.Utf8.parse(e);
    return CryptoJS.AES.decrypt(t, e, {
        iv: e,
        mode: r,
        padding: CryptoJS.pad.Pkcs7
    });
};

// 解密 + GZIP 解压（返回字符串）
dataHandle.default.decrypt = function(t, e) {
    return unzip(aesDecrypt(t, CryptoJS.enc.Utf8.parse(e)));
};
```

关键发现：脚本中调用 `aesDecrypt(params, ttl+"AES")` **只传了 2 个参数**，`r` 参数为 `undefined`。`"CBC" === undefined` 为 false，所以默认使用 **ECB 模式**。

### 3.3 页面脚本原文

```javascript
// 从 RAW URL 提取 params（不经过 URL 解码器，保留 + 号）
var n = getPara("params", window.location.href);

// URL 解码（仅替换 %XX 编码，不解码 +）
n = n.replaceAll("%3D", "=")
     .replaceAll("%20", "+")
     .replaceAll("%2B", "+")
     .replaceAll("%2F", "/");

// 解密（AES-ECB，无 GZIP！）
var c = window.DataHandle.aesDecrypt(n, ttl + "AES");

// 直接 JSON 解析，不需要 GZIP
window.urlParams = JSON.parse(c);

// 存入 localStorage
localStorage.setItem("token", urlParams.token);
```

### 3.4 关于 WASM

文档中早期提到 "AnalysisUrlUtils 在 WASM 中，window 上不可见，需挖 WASM"。当前版本（2026-06）不再使用 WASM 做 params 解密——解密逻辑在 `vendor bundle` 的 CryptoJS 中，纯 JavaScript。

页面中唯一加载的 WASM 是 `prod.all.wasm.combine.js`（媒体流解码器），与认证无关。

---

## 4. Token 获取方案

### 4.1 方案对比

| 方案 | 依赖 | 自动化 | 可靠性 | 说明 |
|:----|:----|:------|:------|:-----|
| **CDP 提取** | Chrome 已登录 | ⚠️ 需 Chrome | ✅ 可靠 | `extract_token.py` 已实现 |
| **URL params 解密** | 获取 params+ttl | ❌ 需 Cloudflare 绕过 | ✅ 可靠 | `decrypt_params.py` 已实现 |
| **localStorage 缓存** | 已保存的 token | ✅ 自动 | ⚠️ 24h 过期 | `auth_cache.json` |

### 4.2 推荐方案

```
本地开发 (有 Chrome):
  Chrome 登录 → CDP 提取 token → WS 连接
  
无界面 Linux 部署:
  [待解决] 获取 params+ttl 的问题
  → decrypt_params.py 解密 → WS 连接
```

### 4.3 token 有效期

JWT token 中包含 `exp` 字段（Unix 时间戳），到期后需要重新获取。当前 token:
```json
{
  "exp": 1782621708,  // 约 24 小时后过期
  ...
}
```

---

## 5. WS URL 构造

### 5.1 格式

```python
host = backend_domain.split(":")[0]  # 如 "1j2cdazl.com"
port = backend_domain.split(":")[1]  if ":" in backend_domain else "18026"

ws_url = (
    f"wss://wsproxy.{host}:{port}/"
    f"?playerId={player_id}"
    f"&jwtToken={token}"
    f"&deviceId={device_id}"
    f"&deviceType=2&platform=6"
)
```

### 5.2 可用 backendDomain

从解密结果中提取（逗号分隔的列表，按优先级排列）:
- `1j2cdazl.com:21027`
- `64vlwlq.com:18026`
- `2umtefe.com:19026`

`deviceType=2` 固定值。`deviceId` 从 `localStorage.fixedDeviceId` 获取。

### 5.3 直连测试结果

| 端点 | 工具 | 结果 | 原因 |
|:----|:----|:----|:-----|
| `wsproxy.*:18026/` | curl_cffi | HTTP 500 | Cloudflare TLS 指纹检测 |
| `pc.lisxdc.com:2083/ws` | websockets/curl_cffi | HTTP 404 | 端点不存在 |
| `wsproxy.*:18026/` | Chrome CDP | ✅ 可用 | Chrome 自带 TLS 握手 |

---

## 6. 剩余问题

### 6.1 核心卡点

**如何在无界面 Linux 上获取 params+ttl？**

params+ttl 来自重定向到 `pc.lisxdc.com:2083/egret/hall?params=...` 的 URL。要获取这个 URL，需要先访问主站并完成 Cloudflare 挑战。

### 6.2 可能的方向

| 方向 | 难度 | 说明 |
|:----|:----|:-----|
| **CDP 桥接** | ⭐ 低 | 依赖 Chrome，无法全自动 |
| **Playwright/Chromium** | ⭐⭐ 中 | 用 Chromium 自动化登录，可 headless|
| **curl_cffi 绕过 Cloudflare** | ⭐⭐⭐ 高 | 尝试不同 TLS 指纹/header |
| **反向代理** | ⭐⭐ 中 | 用已过 Cloudflare 的节点转发 |
| **缓存 token + 定期手动刷新** | ⭐ 低 | 实用但不优雅 |

### 6.3 已确认不可用的路径

- ❌ 备份 WS 端点 `pc.lisxdc.com:2083/ws` 返回 404
- ❌ WASM 逆向 —— 当前版本 params 解密不使用 WASM
- ❌ GZIP 解压 —— 解密后直接是 JSON，无需解压
- ❌ CryptoJS 变量名称冲突 —— 修复后颜色分类正常

---

> **相关脚本：**
> - `scripts/extract_token.py` — 从 Chrome CDP 提取 token
> - `scripts/decrypt_params.py` — AES-ECB 解密 params
> - `scripts/extract_analysis_url.py` — AnalysisUrlUtils 提取尝试
>
> **更新日期:** 2026-06-27
