# 乐鱼 (Leyu) 平台协议完整文档

> **状态:** 基于 harvester 项目原始分析 + hdt 项目实际验证  
> **原始文档:** `harvester/docs/LEYU_PROTOCOL_COMPLETE.md` (1280 行)  
> **hdt 验证:** 2026-06-27 确认协议解密、params 解密、CDP 采集均可用  
> **验证数据:** AES decrypt 532/532 帧 (100%)，Sign 验证 20/20 (100%)

---

## 目录

1. [架构总览](#1-架构总览)
2. [加密与解密](#2-加密与解密)
3. [消息帧结构](#3-消息帧结构)
4. [消息类型完整目录](#4-消息类型完整目录)
5. [握手与进桌序列](#5-握手与进桌序列)
6. [关键消息格式详解](#6-关键消息格式详解)
7. [Sign 算法](#7-sign-算法)
8. [授权与 token 获取](#8-授权与-token-获取)
9. [WebSocket 连接](#9-websocket-连接)
10. [hdt 迁移状态](#10-hdt-迁移状态)
11. [附录](#11-附录)

---

## 1. 架构总览

```
用户浏览器
  │
  ├── 主站: www.ll18mm.vip:6506 / www.nhfspi.vip:4697 / www.rzhsir.vip:9037
  │      └── 登录、大厅、游戏入口（Cloudflare 防护）
  │
  ├── 游戏 iframe: pc.lisxdc.com:2083
  │      ├── /egret/hall?params=...               ← 游戏大厅
  │      ├── /egret/game/{type}/{id}?params=...    ← 具体桌台
  │      └── WebSocket (wss://wsproxy.*:*)
  │
  └── CDN: vadp.irwrek.com / vadp.nz318.com (静态资源)
```

### 1.1 关键域名

| 用途 | 原始记录 | hdt 验证结果 |
|------|---------|-------------|
| 主站 | `nhfspi.vip:4697` / `rzhsir.vip:9037` | ✅ 实际已验证 `ll18mm.vip:6506` 可用 |
| 游戏大厅 | `pc.lisxdc.com:2083/egret/hall` | ✅ 已验证可访问 |
| 游戏桌台 | `pc.lisxdc.com:2083/egret/game/{gameType}/{tableId}` | ✅ 已验证 |
| WS 代理 | `wsproxy.{domain}:{port}` | ❌ curl_cffi 返回 HTTP 500（Cloudflare） |
| WS 备用 | `pc.lisxdc.com:2083/ws` | ❌ 返回 HTTP 404（已失效） |
| CDN | `vadp.nz318.com` | ✅ 实际使用 `vadp.irwrek.com` |

### 1.2 桌台状态码

| gameStatus | 含义 |
|-----------|------|
| 1 | 等待中 |
| 2 | **下注中**（可进） |
| 3 | 已封盘 |
| 4 | 开牌中 |

### 1.3 游戏类型

| gameTypeId | 类型 |
|-----------|------|
| 2001 | 迷你百家乐 |
| 2002 | 普通百家乐 |
| 2003 | 线下百家乐 |
| 2004 | 高级百家乐 |
| 2013 | 测试牌桌 |

### 1.4 桌台命名规则

从 DOM 提取的 `tableName` 格式为 `"{玩法}{编号}"`，如：
- `极速百家乐B28` → 玩法=`极速百家乐`, 编号=`B28`
- `经典百家乐A01` → 玩法=`经典百家乐`, 编号=`A01`
- `龙争虎斗 01` → 玩法=`龙争虎斗`, 编号=`01`

编号提取正则: `[A-Z]+\d+$`（如 "B28"、"A01"、"U11"）

---

## 2. 加密与解密

### 2.1 WS 帧密钥

```python
AES_KEY = b"ED7AA06BD8628B55"  # 16 字节 ASCII，不是 Hex！
SIGN_KEY = AES_KEY              # sign 密钥 = AES 密钥
```

> ⚠️ 最容易搞错：密钥是 ASCII 字符串 `"ED7AA06BD8628B55"`  
> 即字节 `[0x45, 0x44, 0x37, 0x41, 0x41, 0x30, 0x36, 0x42, 0x44, 0x38, 0x36, 0x32, 0x38, 0x42, 0x35, 0x35]`  
> 不是 Hex 解码的 8 字节！

**hdt 迁移:** `hdt/protocol/decoder.py` — `AES_KEY`, `decode_frame()`

### 2.2 接收方向（服务端 → 客户端）

```
raw WS bytes → base64.encode() → base64.decode()
  → AES-CBC(KEY, IV=KEY).decrypt()
  → unpad PKCS7
  → GZIP.decompress()
  → JSON (外层)
  → json.loads(jsonData) → JSON (消息头)
  → json.loads(data) → JSON (游戏数据)
```

```python
# hdt/protocol/decoder.py
def decode_frame(raw: bytes) -> dict | None:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    import gzip, json

    # AES-CBC(IV=KEY)
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_KEY))
    decryptor = cipher.decryptor()
    padded = decryptor.update(raw) + decryptor.finalize()
    data = padded[:-padded[-1]]  # PKCS7 unpadding

    # GZIP 解压
    decompressed = gzip.decompress(data)
    return json.loads(decompressed.decode("utf-8"))
```

### 2.3 发送方向（客户端 → 服务端）

```
JSON.stringify(msg) → GZIP.compress()
  → AES-CBC(KEY, IV=KEY).encrypt()
  → PKCS7 填充至 16B 对齐
  → base64.parse() → Uint8Array
  → WS.send()
```

> **关键发现:** 发送和接收使用**完全相同的加密方式**（IV=KEY）。

### 2.4 验证结果（原始 harvester 项目）

| 指标 | 结果 |
|------|------|
| 解密帧数 | 532/532 (100%) |
| 解码耗时 | ~2.5ms/帧 |
| Sign 验证 | 20/20 (100%) |

### 2.5 params 参数解密（hdt 新发现）

**原始文档推测** AnalysisUrlUtils 在 WASM 中、无法直接提取。**hdt 验证发现**当前版本使用纯 JS（无 WASM），算法为 AES-ECB：

```python
# hdt/scripts/decrypt_params.py
def decrypt_params(params_b64: str, ttl: str) -> dict:
    """解密乐鱼 params 参数。"""
    key = (ttl + "AES").encode("ascii")        # 如 "1782535308601AES"
    ct = base64.b64decode(params_b64)           # base64 → 密文
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    padded = cipher.decryptor().update(ct) + cipher.decryptor().finalize()
    data = padded[: -padded[-1]]               # PKCS7 去填充
    return json.loads(data)                     # 直接 JSON，无 GZIP
```

| 参数 | 值 |
|:----|:----|
| 算法 | AES-128-ECB（无 IV） |
| 密钥 | `ttl + "AES"`，如 `"1782535308601AES"` |
| 编码 | 标准 Base64 |
| 填充 | PKCS7 |
| 输出 | 纯 JSON（无压缩） |

---

## 3. 消息帧结构

### 3.1 三层嵌套

每条 WS 帧解密后是一个三层嵌套 JSON：

```
Layer 1 (外层):
{
  "jsonData": "{\"id\":303,\"data\":\"{...}\"}",
  "nonce": 1745175716,
  "protocolId": 303,
  "gameTypeId": 2001,
  "sign": "dsUjrh1FqEE7t3Sy2xNMmnYkvMw=",
  "timestamp": "1780997461578",
  "playerId": 105452510,
  "tableId": 2718,
  "serviceTypeId": 3,
  "messageId": 0,
  "deviceType": 15,
  "success": true
}

Layer 2 (jsonData 解析后):
{
  "id": 303,              ← 消息 id（对应 msgId）
  "data": "{...}"         ← 游戏数据（JSON 字符串）
}

Layer 3 (data 解析后):
{
  "cardResult": [...],
  "tableId": 2718,
  ...
}
```

### 3.2 二进制帧格式

WS 帧前的二进制头部（6 字节）：

```
[0x04][3B payload_len][2B msg_id][payload]
  │      │              │            └── AES 密文
  │      │              └── 消息 ID（如 401, 303）
  │      └── payload 长度（大端序，3 字节）
  └── 固定标识 0x04
```

**hdt 迁移:** `hdt/capture/direct_client.py` — `WsClient._process_raw()`

---

## 4. 消息类型完整目录

| msgId | 方向 | 名称 | 用途 | hdt 状态 |
|:-----|:----|:-----|:----|:--------|
| 3 | C→S | 心跳 | ping/pong | `protocol_config.py` ✅ |
| 106 | S→C | 游戏结果 | 最终结算 + 完整牌序 | 未迁移 |
| 108 | S→C | 保险结算 | 保险赔付 | 未迁移 |
| 160 | C→S | 换桌请求 | 切换桌台 | `protocol_config.py` ✅ |
| 161 | S→C | 路纸更新 | 路纸增量更新 | 未迁移（CDP 用 Canvas 替代） |
| 217 | S→C | 倒计时 | 倒计时同步 | 未迁移 |
| 218 | S→C | 侧投注结算 | 庄对/闲对等 | 未迁移 |
| 224 | S→C | 发牌通知 | 卡牌一张张发出 | 未迁移 |
| 301 | C→S | 心跳请求 | 客户端心跳 | `protocol_config.py` ✅ |
| 302 | S→C | 游戏状态 | 游戏阶段状态 | `protocol_config.py` ✅ |
| 303 | S→C | 基础数据 | 结算 + 路纸 + 投注 | 未迁移（CDP 用 DOM 替代） |
| 401 | S→C | 进桌响应 | 登录验证 + 分配 tableId | `protocol_config.py` ✅ |
| 10000 | C→S | 登录认证 | 玩家登录 | `protocol_config.py` ✅ |
| 10027 | S→C | 登录响应 | 认证结果 | 未迁移 |
| 10030 | C→S | 玩家信息 | 获取玩家配置 | 未迁移 |
| 10050 | C→S | 大厅列表 | 大厅桌台总览 | 未迁移 |
| 10052 | S→C | 大厅响应 | 桌台列表 | `protocol_config.py` ✅ |
| 10053 | S→C | 配置下发 | 游戏配置 | 未迁移 |
| 10070 | S→C | 旧版协议 | 二进制 protobuf 格式 | 未迁移（P2） |
| 10071 | S→C | 路纸查询响应 | 完整路纸数据 | `protocol_config.py` ✅ |
| 10075 | C→S | 路纸查询请求 | 请求完整路纸 | `protocol_config.py` ✅ |
| 21001 | S→C | 牌桌历史 | 历史数据 | 未迁移 |

### 4.1 协议 ID（protocolId）

| protocolId | 用途 |
|-----------|------|
| 10052 | 大厅列表 |
| 10070 | 旧版协议 |
| 10071 | 路纸查询 |
| 10075 | 路纸查询 |
| 20001 | 玩家数据 |
| 21001 | 牌桌历史 |
| 21002 | 牌桌详情 |

---

## 5. 握手与进桌序列

```
CDP 桥接模式            直连模式（curl_cffi）
  │                        │
  │                        1. WebSocket 连接 (wss://wsproxy.*)
  │                        │   ← Cloudflare 拦截 (HTTP 500) ❌
  │                        2. msg 10000: 登录认证
  │                        3. ← msg 10027: 登录响应
  │                        4. msg 10030: 玩家信息
  │                        5. ← msg 10053: 配置下发 (×5)
  │                        6. msg 10075: 路纸查询
  │                        7. ← msg 10071: 路纸数据
  │                        8. msg 401: 进桌请求
  │                        9. ← msg 401: 进桌成功
  │                        10. ← msg 302: 游戏状态
  │                        11. ← msg 303: 结算数据（循环）
  │                        12. msg 301: 心跳（每 11s）
```

### 5.1 登录认证 (msg 10000)

```json
{
  "id": 10000,
  "data": {
    "playerId": 105452510,
    "jwtToken": "eyJhbGciOiJIUzI1NiJ9...",
    "deviceType": 15,
    "deviceId": "1782535310058674277-11335781",
    "identity": 0,
    "vipMode": 0,
    "gameTypeId": 2001,
    "platform": 6
  }
}
```

### 5.2 进桌请求 (msg 401)

```json
{
  "id": 401,
  "data": {
    "tableId": 2718,
    "deviceType": 15,
    "deviceId": "...",
    "identity": 0,
    "vipMode": 0,
    "joinTableMode": 1
  }
}
```

---

## 6. 关键消息格式详解

### 6.1 msg 303 — 牌桌基础数据（结算）

```json
{
  "id": 303,
  "data": {
    "tableId": 2718,
    "roundId": 456354030,
    "cardResult": [{"owner": 0, "result": "4"}],
    "playerPoints": [0, 5],
    "bankerPoints": [0, 4],
    "playerScore": 7,
    "bankerScore": 1,
    "roadPaper": {"bigRoad": {"list": ["B","P","B","B"]}},
    "betTotal": {"allTotal": 39100, "allCount": 196},
    "gameStatus": 4,
    "countdown": 15
  }
}
```

### 6.2 msg 161 — 路纸更新

```json
{
  "id": 161,
  "data": {
    "tableId": 2718,
    "result": "banker",
    "position": {"row": 2, "col": 3},
    "roadPaper": {"bigRoad": {"list": [...]}}
  }
}
```

### 6.3 msg 217 — 倒计时

```json
{
  "id": 217,
  "data": {
    "tableId": 2718,
    "countdown": 9,
    "gameStatus": 2
  }
}
```

### 6.4 msg 224 — 发牌通知

```json
{
  "id": 224,
  "data": {
    "tableId": 2718,
    "roundId": 456354030,
    "cards": [
      {"owner": 0, "point": 37},
      {"owner": 1, "point": 24}
    ]
  }
}
```

---

## 7. Sign 算法

### 7.1 校验规则

```python
import hmac, hashlib, base64

SIGN_KEY = b"ED7AA06BD8628B55"

def verify_sign(outer: dict) -> bool:
    """验证服务端消息的 sign。"""
    sign = outer.get("sign", "")
    if not sign:
        return False

    # 签名字段固定顺序
    sign_data = ":".join([
        str(outer.get("nonce", "")),
        str(outer.get("protocolId", "")),
        str(outer.get("gameTypeId", "")),
        str(outer.get("tableId", "")),
        str(outer.get("playerId", "")),
        str(outer.get("jsonData", "")),
    ])

    expected = base64.b64encode(
        hmac.new(SIGN_KEY, sign_data.encode(), hashlib.sha1).digest()
    ).decode()

    return sign == expected
```

### 7.2 验证结果

原始项目验证 20/20 (100%) 全部通过。

---

## 8. 授权与 token 获取

### 8.1 完整认证链路

```
用户输入账号密码
  │
  主站登录（Cloudflare + 验证码）
  │   生成加密 params + ttl + signature
  │
  URL: pc.lisxdc.com/egret/hall?params=&ttl=&signature=
  │   页面内 inline script 解码 params
  │   Key = ttl + "AES"
  │   算法 = AES-ECB + PKCS7
  │   输出 = JSON（纯文本，无 GZIP）
  │
  window.urlParams = {
    "playerId": 105452510,
    "token": "eyJhbGciOiJIUzI1NiJ9...",
    "backendDomainUrl": "1j2cdazl.com:21027",
    "backendDomainUrlList": "1j2cdazl.com:21027,64vlwlq.com:18026,...",
    ...
  }
  │
  localStorage.setItem("token", urlParams.token)
  localStorage.setItem("fixedDeviceId", "1782535310058674277-11335781")
```

### 8.2 token 获取方式对比

| 方式 | 工具 | 是否可行 | 说明 |
|:----|:-----|:--------|:-----|
| CDP 读 localStorage | `CDPSession + evaluate` | ✅ 已验证 | 需 Chrome 已登录 |
| CDP 读 window.urlParams | `CDPSession + evaluate` | ✅ 已验证 | 需 Chrome 在游戏页面 |
| params 解密 | Python AES-ECB | ✅ 已验证 | 需先获取 params+ttl |
| Playwright 自动登录 | Playwright + stealth | ⚠️ 验证码被拒 | Cloudflare 反自动化检测 |

### 8.3 token 有效期

JWT 中的 `exp` 字段（Unix 时间戳），约 24 小时。

---

## 9. WebSocket 连接

### 9.1 WS URL 格式

```
wss://wsproxy.{host}:{port}/
  ?playerId={player_id}
  &jwtToken={token}
  &deviceId={device_id}
  &deviceType=2&platform=6
```

### 9.2 可用 backendDomain（来自解密 params）

| 域名 | 端口 | 测试结果 |
|:----|:----|:--------|
| `1j2cdazl.com` | 21027 | ❌ curl_cffi HTTP 500 |
| `64vlwlq.com` | 18026 | ❌ curl_cffi HTTP 500 |
| `2umtefe.com` | 19026 | ❌ curl_cffi HTTP 500 |
| `pc.lisxdc.com` | 2083 (备份 WS) | ❌ HTTP 404 |

### 9.3 连接方式

| 方式 | 工具 | 状态 | 说明 |
|:----|:-----|:----|:-----|
| 直连 | curl_cffi + chrome124 | ❌ Cloudflare 拦截 | `wsproxy.*` 全部返回 500 |
| 直连 | websockets | ❌ TLS 握手失败 | 未伪装指纹 |
| CDP 桥接 | CDP WebSocket Frame 拦截 | ✅ harvester 已实现 | 需 Chrome，截取浏览器 WS 帧 |
| CDP 桥接 | `DataHandle.decryptWsData` | ✅ harvester 已实现 | 同 WS 密钥解密 |

### 9.4 Cloudflare 绕过尝试

| 方法 | 结果 |
|:----|:----|
| curl_cffi chrome124 指纹 | ❌ HTTP 500 |
| curl_cffi chrome131 指纹 | ❌ HTTP 500 |
| 备份端点 pc.lisxdc.com:2083/ws | ❌ HTTP 404 |
| Playwright + anti-detection | ⚠️ 验证码被拒 |

---

## 10. hdt 迁移状态

### 10.1 已迁移模块

| 模块 | 文件 | 状态 |
|:----|:----|:----|
| WS 帧解码 | `protocol/decoder.py` | ✅ 已迁移 + 测试 |
| 协议配置 | `protocol/protocol_config.py` | ✅ 已迁移 |
| 牌局追踪 | `protocol/round_tracker.py` | ✅ 已迁移 |
| WS 客户端 | `capture/direct_client.py` | ✅ 已迁移（websockets 版） |
| CDP 桥接 | `capture/cdp_bridge.py` | ✅ 已迁移 |
| DOM 提取 | `capture/dom_extractor.py` | ✅ 已迁移 + 大路 Canvas 分析 |
| DOM 解析 | `capture/dom_parser.py` | ✅ 已迁移 |
| 浏览器管理 | `auth/chrome_manager.py` | ✅ 已迁移 |
| JWT 管理 | `auth/auth_manager.py` | ✅ 已迁移 |
| WSSource | `sources/leyu_ws.py` | ⚠️ 占位桩，直连不可用 |
| 帧编码/发送 | — | ❌ 未迁移 |
| 消息路由 | — | ❌ 未迁移（CDP 替代） |
| 协议握手 | — | ❌ 未迁移 |
| 心跳 | — | ❌ 未迁移 |
| CDP 桥接模式 | — | ❌ 未迁移（harvester 已有） |

### 10.2 迁移优先级

| 优先级 | 模块 | 原因 |
|:------|:----|:-----|
| P0 | CDP 桥接模式 | 唯一可用的 WS 数据源路径 |
| P1 | 帧编码 + 心跳 | 发送方向的基础能力 |
| P2 | 消息路由 | WS 协议消息解析 |
| P3 | 完整 WS 源 | 认证 + 进桌 + 消息循环 |

### 10.3 测试状态

| 测试文件 | 数量 | 说明 |
|:--------|:----|:-----|
| `tests/test_dom.py` | 19 | DOM 提取/解析/大路分析单元测试 |
| `tests/test_adapter.py` | 15 | MarketTick 语义化映射测试 |
| `tests/test_decoder.py` | 3 | AES 帧解码测试 |
| `tests/test_round_tracker.py` | 6 | 牌局追踪测试 |
| `tests/test_sources.py` | 4 | CDP/WS Source 接口测试 |
| `tests/test_integration.py` | 12 | 4 基础 + 8 游戏页面集成测试 |

---

## 11. 附录

### 11.1 关键脚本

| 脚本 | 用途 | 用法 |
|:----|:----|:----|
| `scripts/debug_cards.py` | 查看游戏页面当前卡牌 | `uv run python scripts/debug_cards.py` |
| `scripts/extract_token.py` | 从 Chrome CDP 提取 token | `uv run python scripts/extract_token.py` |
| `scripts/decrypt_params.py` | AES-ECB 解密 params | `uv run python scripts/decrypt_params.py` |

### 11.2 已知限制

- WS 直连因 Cloudflare 不可行
- 备份 WS 端点已失效 (404)
- CDP 桥接模式需 Chrome 运行
- Playwright 自动登录被反自动化检测拦截

### 11.3 外部依赖

| 依赖 | 用途 | 版本 |
|:----|:----|:----|
| `cryptography` | AES 加解密 | >=48.0.0 |
| `websockets` | CDP/WS 通信 | >=16.0 |
| `curl_cffi` | TLS 指纹伪装 | >=0.15.0 |
| `aiohttp` | HTTP 请求 | 最新 |
| `aiohappyeyeballs` | 网络连接 | 自动安装 |

---

> **更新历史:**
> - 2026-06-16: 原始 harvester 文档（3000+ 帧验证）
> - 2026-06-25: 首次 params 解密尝试（AES-CBC IV=KEY 假设，失败）
> - 2026-06-27: 修正为 AES-ECB（ttl+AES 密钥），解密成功
> - 2026-06-27: 验证所有 WS 直连端点不可用
> - 2026-06-27: 合并原始文档 + hdt 验证到本文档
