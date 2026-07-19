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
| WS 代理 | `wsproxy.{domain}:{port}` | ✅ 直连已打通（2026-07-17，需完整签名后缀，见 §9） |
| WS 备用 | `pc.lisxdc.com:2083/ws` | ❌ 返回 HTTP 404（已失效） |
| CDN | `vadp.nz318.com` | ✅ 实际使用 `vadp.irwrek.com` |

### 1.2 桌台状态码

| gameStatus | 含义 |
|-----------|------|
| 1 | 等待中 |
| 2 | **下注中**（可进） |
| 3 | 已封盘 |
| 4 | 开牌中 |

### 1.3 游戏类型（2026-07-19 逆向大厅前端 JS 校准）

权威来源：大厅资源 `egret/js/assets-*.js` 中的枚举 `It`（gameTypeId 数值）
与 `_gameNameMap`（id→官方中文名）。与网页大厅显示的 8 个分类逐一对上：

| gameTypeId | 官方名称 | 前端枚举名 |
|-----------:|---------|-----------|
| 2001 | 经典百家乐 | BACCARAT |
| 2002 | 极速百家乐 | BACCARAT_FAST |
| 2003 | 竞咪百家乐 | BACCARAT_BID |
| 2004 | 包桌百家乐 | BACCARAT_VIP |
| 2005 | 共咪百家乐 | BACCARAT_REVEAL |
| 2030 | 主播百家乐 | BACCARAT_ZHUBO |
| 2034 | 闪电百家乐 | LIGHTNING_BACC |
| 2038 | 电投百家乐 | BACCARAT_DT |

> 交叉验证：玩家设置接口实测的"游戏类型过滤"值 `2002,2001,2030,2034,2003,2005,2004,2038`
> 与网页分类栏顺序（极速/经典/主播/闪电/竞咪/共咪/包桌/电投）完全一致。

其余已知 id：2006 龙虎、2007 轮盘、2008 骰宝、2009 牛牛、2010 炸金花、
2011 三公、2012 21点、2013 多台、2014 高额百家乐、2015 斗牛、
2016 保险百家乐、2018 百家乐大赛、2020 番摊、2027 劲舞百家乐。
代码内映射见 `hdata/client.py::_GAME_TYPE_NAMES`。

⚠️ 注意：服务端大厅快照 10052 **不下发玩法名称**（只有 gameTypeId），
名称表是前端本地资源；官方玩法名+桌名的服务端来源是 10053，见 §12.4。

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

### 2.6 二进制 schema 帧（codecFlag=1，10053/10089 等）

部分大厅/游戏帧的 `data` 字段不是 JSON，而是**自定义二进制 schema 编码**
（帧级 `codecFlag=true` 时启用；schema key 格式 `{protocolId}_{serviceTypeId}`，
大厅 serviceTypeId=7）。逆向自前端 JS（assets-*.js 的 U3/Z3/Y3/J3/W3/K3/Q3/X3），
Python 解码器：`hdata/protocol/schemacodec.py`，schema 配置：
`hdata/protocol/_schema_data.py`（前端 H3 常量原样移植，含 10053_7/10089_7/
10073_7/10075_7/301_2/302_2 共 6 个协议）。

**载荷封装**：`data` 为标准 base64 → 字节流三段式：

```
varint bits_len | varint pool_len | varint body_len
bits[bits_len]   位段：strategy=BIT 的标量字段（MSB-first 连读）
pool[pool_len]   常量池：varint n_str + n_str×string + varint n_num + n_num×signedVarNumber
body[body_len]   主体段
```

**schema 读取（每个消息/子消息递归）**：

1. 先读 `ceil(非BIT标量字段数/8)` 字节**存在掩码**（MSB-first，BIT 标量字段不占位）；
2. 按 schema 声明顺序遍历字段：
   - BIT 标量（strategy=1 且类型 INT/NUMBER/BOOLEAN）→ 位段读 `bit` 位；
   - 其余字段掩码位为 1 → 主体段读值。

**字段类型 S3**：INT=1 BOOLEAN=2 NUMBER=3 STRING=4 MESSAGE=5 ARRAY=6 MAP=7
**策略 B3**：BODY=0（直接读值）/ BIT=1（位段）/ CONST_POOL=2（varint 索引查常量池）
**原语**：varint=LEB128；signedVarInt/Number=zigzag；string=varint 长度+UTF-8；
double=8 字节小端；Map 缺省 value 为动态类型（1 字节类型标记 + 值）。

已知协议：

| key | 含义 |
|-----|------|
| 10089_7 | 大厅桌台 id 全集（hallGameTable：tableId/gameStatus/置顶排序等） |
| 10053_7 | 分页桌台元数据（gameTableMap：tableName/gameTypeName/gameCasinoName/dealerName/bootNo/videoUrl 等 80 字段） |
| 10073_7 / 10075_7 | 桌台限红缓存 / 版本映射 |
| 301_2 / 302_2 | 游戏内桌台缓存 / 好路桌台（serviceTypeId=2） |

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

## 9. WebSocket 连接（2026-07-17 静态分析 + 实测验证 ✅）

### 9.1 WS URL 格式（已与浏览器逐字节对齐）

游戏前端（egret release js）的拼接规则：

```js
// index release js:
socketServer = `wss://wsproxy.${params.backendDomainUrl.trim()}`   // backend 自带端口！
// assets release js initServerUrl:
url = socketServer + "/?playerId=" + playerId + "&jwtToken=" + jwtToken
      + "&deviceId=" + getDeviceID()
      + STATIC_KEY_PREFIX + KEY_VERSION
```

即：

```
wss://wsproxy.{backendDomainUrl}/
  ?playerId={player_id}
  &jwtToken={game_token}
  &deviceId={device_id}
  &platformId=1&applicationId=5&version=v1.0.5
```

关键点（全部实测过）：

| 要素 | 规则 | 缺失后果 |
|:----|:----|:--------|
| host | `wsproxy.` + backendDomainUrl **原样**（含端口，如 `6pwn4i.com:4999`） | 写死端口 18026 → TCP 超时/RST |
| `deviceId` | `{13位ms时间戳}{6位随机}-{8位随机}`（`Date.now()+Math.floor(1e5*(9*Math.random()+1))` + `"-"` + `Math.floor(1e7*(9*Math.random()+1))`） | 旧版无 deviceId → 拒连 |
| `platformId=1&applicationId=5&version=v1.0.5` | `Q9.STATIC_KEY_PREFIX + Q9.KEY_VERSION` 常量 | **缺失 → 握手 HTTP 500** |
| ~~`deviceType=2&platform=6`~~ | 旧文档写法，浏览器实际不发 | 多余但无害 |

> 注意：HTTP 500 是 wsproxy 对"签名后缀缺失/参数不全"的统一拒绝，**不是** Cloudflare 指纹拦截
> （curl_cffi chrome 指纹、原生 websockets 均能连通，只要 URL 完整）。

### 9.2 协议帧格式（发送 + 接收双向已验证）

整帧为二进制密文，**无明文协议头**（旧的 `[0x04][3B len][2B msg_id]` 假设已废弃）：

```
发送: wire = AES-128-CBC( gzip( JSON.stringify(msg) ), key=iv="ED7AA06BD8628B55", PKCS7 )
接收: msg  = JSON ← gunzip ← AES-128-CBC 解密(同 key/iv)
```

发送消息结构（`p3.send`，含签名）：

```json
{
  "jsonData": "{\"id\":10000,\"param\":\"{...}\"}",   // 内层 param 也是 JSON 字符串
  "nonce": 123456789,                                  // Math.round(Math.random()*2^31)
  "protocolId": 10000,
  "gameTypeId": 2013,
  "sign": "Base64(HmacSHA1(jsonData+nonce+timestamp, KEY))",
  "timestamp": 1784356780704,
  "playerId": 105452510,
  "tableId": 0,
  "serviceTypeId": 7
}
```

关键协议号 / 枚举（assets release js）：

| 名称 | 值 | 说明 |
|:----|:--|:----|
| `Fs.Login` | 10000 | 登录请求/响应 |
| 登录失败踢出 | 10026 | kickType=2 = token 失效 |
| `Ot.HALL` | 7 | serviceTypeId 大厅 |
| `Ot.GAME` | 3 | serviceTypeId 游戏 |
| `_t.EGRET2_PC` | 15 | deviceType（PC 网页端） |

登录请求体（`_sendLogin`）：

```json
{"jwtToken": "...", "deviceType": 15, "deviceId": "...",
 "timeZoneArea": "Asia/Shanghai", "offsetMinutes": 480,
 "protocolCodecConfig": {}, "version": "1.1.1"}
```

登录成功响应（protocolId=10000, status=1）后，服务器会陆续推大厅数据
（10028 活动、10011 公告、10040 活动列表等）。

### 9.3 连接方式

| 方式 | 工具 | 状态 | 说明 |
|:----|:-----|:----|:-----|
| **直连** | websockets（原生） | ✅ **已打通** | URL 完整即可，无需 TLS 指纹伪装 |
| 直连 | curl_cffi + chrome 指纹 | ✅ 可用 | 同样需要完整 URL |
| CDP 桥接 | CDP WebSocket Frame 拦截 | ✅ harvester 已实现 | 需 Chrome，截取浏览器 WS 帧 |

冒烟脚本：`scripts/smoke_ws_login.py`（刷新 token → 连接 → Login → 验证响应），
已实现 **PASS**（收到 10000 登录成功 + 大厅推送共 4 帧）。

### 9.4 代码位置

| 功能 | 位置 |
|:----|:----|
| WS URL 构造 | `hdata/auth/session.py::build_ws_config()`（含 `WS_STATIC_KEY_SUFFIX`、`generate_device_id()`） |
| 帧编解码/签名/登录构造 | `hdata/protocol/codec.py`（`encode_frame`/`decode_frame`/`build_message`/`build_login_msg`） |
| WS 客户端 | `hdata/capture/direct_client.py::WSClient` |
| 进桌冒烟 | `scripts/smoke_ws_table.py`（login→10052快照→进桌→牌局帧，PASS） |

### 9.5 进桌与桌台级协议（2026-07-18 实测 ✅）

**进桌完整时序**（`scripts/smoke_ws_table.py` 已验证）：

```
Login(10000) → 大厅推送
  → 发 TABLE_LIST_ALL(10089){labelTypeId:1}
  → 收 10052 (gameTableMap 快照，含每桌 gameTypeId/gameStatus/roadPaper/roundNo...)
  → 发 NEW_INTER_GAME(401) 进桌
  → 收 401 响应(gameTableInfo 全量) + 持续牌局帧
```

**关键协议号**：

| 协议号 | 名称 | 方向 | 说明 |
|------:|:-----|:----|:-----|
| 10089 | TABLE_LIST_ALL | 请求 | `{labelTypeId:1}`，serviceTypeId=7(HALL) |
| 10052 | 大厅桌台快照 | 推送 | `gameTableMap{tableId:{gameTypeId,gameStatus,roadPaper,...}}` |
| 401 | NEW_INTER_GAME | 请求/响应 | 普通百家乐进桌，serviceTypeId=3(GAME) |
| 101 | INTER_GAME | 请求/响应 | VIP/竞价等(notForceExitArr)进桌 |
| 102 | OUT_GAME | 请求 | 离桌 |
| **123** | **KICK_OUT_GAME** | 推送 | **桌台级踢出（连续5局未投注触发，实测 ~240s 被踢 1 次）** |
| 10026 | KICK_NOTICE | 推送 | 会话级踢出（token 失效，需重新登录） |
| 116 | ROAD_PAPER | 推送 | 路纸数据（bigRoad 等 base64 位图） |
| 110 | 桌台动态 | 推送 | roundId/在线人数/投注额/奖池 |
| 104 | 局状态 | 推送 | roundNo/countdownEndTime/bootIndex |
| 106/107 | 牌局事件 | 推送 | 发牌/结算等 |
| 160/161 | 路纸更新 | 推送 | 增量路纸 |

**进桌请求体**（401，普通百家乐 gameTypeId=2001）：

```json
{"tableId": 2751, "gameTypeId": 2001, "identity": 1, "joinTableMode": 2,
 "gameCasinoId": 0, "deviceType": 15, "deviceId": "..."}
```

**5局未投注踢出应对**（用户实测规则 + 本框架验证）：

- 纯监听不下注时，服务器约每 ~4-5 局发 `KICK_OUT_GAME(123)` 踢出桌台；
- **会话不断**：123 只踢桌台，WS 连接与登录态保持；
- 策略：**收 123 → 立即重发 401 进同一张桌**（`smoke_ws_table.py` 已实现并验证，重进后牌局帧继续）；
- 与 10026 区分：10026 是 token 级踢出，必须走 `refresh_game_session` 重新登录。

### 9.6 域名动态化（2026-07-18 加固 ✅）

域名/端口是**动态资源**（小时~天级轮换），只有入口种子（leyu.com / leyu.me）稳定。
解析链路与加固点（`hdata/auth/domain.py`）：

```
入口种子(leyu.com/.me) ──code.js/mappings──▶ 主站域名(www.xxx.vip:port)
      │                                          │ venue/launch
      │                                          ▼
      │                                    游戏后端(6pwn4i.com:4999 + 备用列表)
      │                                          │ wsproxy.{backend}
      ▼                                          ▼
   DomainCache(TTL 30min) ──探活失败──▶ invalidate → 重新解析
```

- `DomainCache` 加 **TTL（30 分钟）**，过期自动重解析；
- `resolve_domain(validate=True)`：缓存命中后**先探活**，死了自动 invalidate 再解析；
- 入口站适配两种映射格式：旧版 `mappings.set(...)` 与新版 `/code.js` 的 `lypcurls='...'`（PC 端）；
- 自签证书降级：`_fetch`/`probe_domain` 自动跳过证书校验；
- 游戏后端/WS 地址**每次进游戏由 venue/launch 重新下发**，本就动态，无需缓存。

### 9.7 路纸位图解码（2026-07-18 实测 ✅）

`roadPaper` 里各路（bigRoad / beatPlateRoad / bigEyeBoy / smallRoad / cockroachPig 等 21 个键）
都是 **base64 位图**，编码规则（对应 JS `parseBaccaratSingleBootRoadPaper` + `_i` 位读取器）：

```
base64 解码 → 字节按 MSB-first 拼成位串 → 游标顺序读位
头部:  version = read(8) + 1
       n = read(8) * read(8)          # 单元格总数
每格:  flag = read(1)
       大路(BIG_ROAD): flag=1 → result=read(2), tieNumber=read(4)
       珠盘(MAIN_ROAD): flag=1 → result=read(2), pair=read(2)
分列:  每 6 格一列
```

结果枚举（Pa）：`0=闲(P)` `1=庄(B)` `2=和(T)` `3=庄六(B6)`

已实现（`hdata/protocol/roadpaper.py`）：

| 函数 | 用途 |
|:----|:----|
| `BitReader` | MSB-first 位读取器（对齐 JS `_i`） |
| `decode_big_road(b64)` | 大路 → 列网格 + 平坦序列 |
| `decode_bead_plate(b64)` | 珠盘 → 列网格 + 平坦序列（B/P/T/B6） |
| `decode_road_paper(dict)` | 整包 roadPaper 批量解码（未知键跳过） |

`RoundTracker.feed_road_paper(table_id, road_paper)`：解码珠盘序列并**幂等增量**
同步进追踪器（B6 归一为 B），实测多桌正确还原 18~64 局/靴。
测试：`tests/test_roadpaper.py`（6 例，含真实快照回归）。

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
| WSSource | `sources/leyu_ws.py` | ✅ 直连可用（2026-07-17 打通） |
| 帧编码/发送 | `protocol/codec.py` | ✅ 已实现 + 实测 |
| 协议握手 | `protocol/codec.py::build_login_msg` | ✅ 已实现 + 实测 |
| 消息路由 | — | ❌ 未迁移（CDP 替代） |
| 心跳 | — | ⚠️ 占位（协议号 1，未实测） |
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

### 11.2 已知限制（2026-07-18 更新）

- ~~WS 直连因 Cloudflare 不可行~~ — **已推翻**：`wss://wsproxy.{backend}/?playerId=..&jwtToken=..&deviceId=..&platformId=1&applicationId=5&version=v1.0.5` 可直连，现为**主数据通道**（见 §12）
- 备份 WS 端点已失效 (404)
- CDP 桥接模式需 Chrome 运行（仅作调试用）
- Playwright 自动登录被反自动化检测拦截（纯 HTTP 打码登录已替代）

---

## 12. 2026-07 新研究发现归档

### 12.1 WS 直连已打通（取代 CDP 桥接成为主通道）

- 连接 URL：`wss://wsproxy.{backend(含端口)}/?playerId=..&jwtToken=..&deviceId=..&platformId=1&applicationId=5&version=v1.0.5`，query 参数缺一不可（缺后缀握手返回 500）
- 登录帧：protocolId=10000 (Fs.Login)，serviceTypeId=7 (HALL)；响应 status==1 为成功
- 全流程冒烟脚本：`scripts/client_e2e_smoke.py`（login → 桌台列表 → 进桌 → 事件流）

### 12.2 game_token 的 jti 会话语义（重要）

- 服务端按 JWT 的 **jti** 做会话管理：**同一账号同时只允许一条游戏连接**
- 浏览器/旧连接持有的 token，被新登录或服务端刷新后**即刻作废**（旧连接收 10026 踢出）
- 因此：**每次新建 WS 连接前必须先调 `refresh_game_session` 刷新 token**，
  不可信任缓存里的旧 token。`hdata/client.py` 的 `_WSConnection.on_before_connect` 钩子已内置此行为
- token 本身**非一次性**：有效期内可多次使用（冒烟脚本多次复用验证），但必须是服务端当前认可的那张

### 12.3 域名动态轮换的应对（已落地于 `hdata/auth/domain.py`）

- 种子站固定（leyu.com / leyu.me），实际域名与端口**动态轮换**（小时/天级）
- 解析方式：种子站 `/code.js` 中的 `lypcurls='...'` 为 PC 端真实域名列表
- 加固策略：TTL 30 分钟缓存 + 探活 + 失效自动重解析；调用方只提供种子站

### 12.4 桌台数据的两个层级

- **大厅快照**（10052 推送的 gameTableMap）：有 gameTypeId/gameStatus/bootNo/roadPaper/在线人数/goodRoadPoints，但**没有 tableName/gameTypeName**
- **桌名与官方玩法名**（10053 = TABLE_LIST_LIMIT）：前端流程为先发 10089 `{labelTypeId:1}`
  拿到桌台 id 全集，再分页发 10053 `{groupId:7(ALL_GAME), tableIds:[...], allFlag:0}`，
  响应 gameTableMap 每张桌含 tableName/gameTypeName/physicsTableNo/gameCasinoName 等
  （JS 中 schema id `10053_7`）。10089/10053 的载荷是**自定义二进制 schema 编码**
  （非 JSON），解码器已实现：`hdata/protocol/schemacodec.py`（格式见 §2.6），
  `get_tables()` 自动补拉，大厅层即可拿到官方桌名/玩法名
- **进桌快照**（401 响应的 gameTableInfo）：60+ 字段，含 tableName/dealerName/roundNo/cardResult/限额等，样例存 `.cache/gametableinfo.json`
- 进桌协议号选择：普通百家乐用 401 (NEW_INTER_GAME)；VIP/竞价等（2003/2004/2014/2020）用 101 (INTER_GAME)

### 12.5 踢出机制（实测）

| protocolId | 级别 | 含义 | 处理 |
|:-----------|:-----|:-----|:-----|
| 123 | 桌台级 | 连续 5 局未投注被踢出该桌 | 自动重进即可，会话不受影响 |
| 10026 | 会话级 | token 失效/被顶替 | 必须 `refresh_game_session` 后重登 |

### 12.6 对外公共 API（`hdata/client.py`）

- `GameClient.login() → get_tables() → enter_table()` 三步门面（平台中性命名），端到端已冒烟通过
- 接口契约文档：`docs/对外接口文档.md`；打包说明：`docs/打包说明.md`；机制总览：`docs/平台接入机制.md`

### 12.7 路纸筛选设置的请求机制（2026-07-18 浏览器嗅探确认）

**结论：保存走 HTTP，过滤在前端，服务端推送内容不受设置影响。**

嗅探方法：CDP Network 域同时抓 HTTP 与 WS 帧（`scripts/sniff_filter_setting.py`），
人工在大厅操作路纸筛选设置。注意：大厅游戏逻辑在 **iframe**
（`https://pc.{资源域名}/egret/hall?params=...`）里，必须盯 iframe target，
外层页面只有 `{"msgId":0,"msgData":{}}` 文本心跳。

设置操作时的流量：

| 通道 | 观察 | 判定 |
|:-----|:-----|:-----|
| HTTP | `POST https://gateway.{backend}/game-http/player/updatePlayerSetting?t={毫秒时间戳}`，载荷为 96 字节加密体（6 次操作 6 条，前 64 base64 字符相同 = 同密钥同 IV + 相同明文头） | ✅ 设置保存接口 |
| WS 发送 | 仅 pid=3 心跳（`{clientTime, deviceType, deviceId}`），无任何业务帧 | 设置不走 WS |
| WS 接收 | 10052 大厅快照恒定 ~1.4s 一帧，设置前后节奏与内容无变化 | 服务端**不按设置过滤推送**，始终推全量 |

推论：

1. `updatePlayerSetting` 只是把筛选偏好**持久化到服务端**（换设备/刷新后同步）；
2. 实际的桌台过滤是**前端本地做**的——10052 推送里每张桌已带 roadPaper
   （全量帧）与状态字段，前端自己算"好路"再过滤显示；
3. **对 HData 的意义：无需复刻该接口**。`GameClient.get_tables()` 拿到全量
   桌台+路纸后，调用方本地筛选即可（`scripts/demo_road_monitor.py` 即此模式）；
4. ~~`updatePlayerSetting` 载荷加密未解~~ → **已逆向，见 §12.8**。

### 12.8 gateway HTTP 载荷加密（2026-07-18 逆向完成）

来源：大厅 iframe 页 `https://pc.{资源域名}/egret/hall` 内联的 `dataHandle` webpack bundle。

```js
// 前端原始实现（minified 还原）
dataHandle.encrypt = function(t, e) {
    if (typeof t !== "string") t = JSON.stringify(t);
    e = CryptoJS.enc.Utf8.parse(e);
    return aesEncrypt(zip(t), e);          // zip = pako.gzip
};
function aesEncrypt(t, e) {                // e 同时作 key 和 iv
    return CryptoJS.AES.encrypt(t, e, {iv: e, mode: CBC, padding: Pkcs7}).toString();
}
```

**算法**：`base64( AES-128-CBC( gzip(JSON), key=iv ) )`——与 WS 帧同结构，但**密钥不同**。

密钥按环境硬编码在 bundle 里（`{dev/test/.../release: ...}[ENV]`）：

| 环境 | key=iv（16 ASCII） |
|:-----|:-----|
| release（生产） | `015CCB80A680E129` |
| dev/training | `AA4194657AD89A56` |

> 注意 bundle 里还有另一组按 ENV 选的字符串（`probinpjms7rfm26` 等），
> 实测**不是** gateway 载荷密钥；正确的是 `015CCB80A680E129`（已用 6 条真实
> updatePlayerSetting 载荷验证）。

**updatePlayerSetting 解密实测**（玩家 105452510 操作路纸筛选）：

```json
{"playerId":105452510,"settingType":"4","settingObject":"23","deviceType":"6","value":"2,1"}
{"playerId":105452510,"settingType":"4","settingObject":"22","deviceType":"6","value":"2002,2001"}
{"playerId":105452510,"settingType":"4","settingObject":"22","deviceType":"6","value":"2002"}
{"playerId":105452510,"settingType":"4","settingObject":"22","deviceType":"6","value":"2001"}
{"playerId":105452510,"settingType":"4","settingObject":"23","deviceType":"6","value":"2,1,3,5,6,4,9,10,7,8,11"}
{"playerId":105452510,"settingType":"4","settingObject":"22","deviceType":"6","value":"2002,2001,2030,2034,2003,2005,2004,2038"}
```

字段语义：

| 字段 | 含义 |
|:-----|:-----|
| settingType | 设置大类，`"4"` = 大厅筛选 |
| settingObject | 子项：`"22"` = 游戏类型过滤（value 为 gameTypeId 列表，2001/2002/2030…）；`"23"` = **路纸类型过滤**（value 为好路类型 id 列表，如 `2,1` / `2,1,3,5,6,4,9,10,7,8,11`） |
| deviceType | `"6"` = PC 网页 |
| value | 选中的 id 列表，逗号分隔；路纸 id 与游戏内"好路"分类对应（长庄/长闲/单跳等） |

配套的读取接口：`GET https://gateway.{backend}/game-http/player/getPlayerSetting?playerId={id}`
（响应载荷同算法加密）。如需程序化改设置，用同算法加密 POST 即可。

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
> - 2026-07-18: WS 直连打通（推翻 Cloudflare 结论）；归档 jti 会话语义、域名轮换应对、
>   桌台数据层级、踢出机制；新增对外公共 API（hdata/client.py）与打包说明
