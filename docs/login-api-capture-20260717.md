# 乐鱼登录接口实测梳理（2026-07-17 浏览器人工登录抓包）

> 数据来源：Chrome 人工登录两次，WebBridge 网络抓包 + 页面 fetch/XHR 拦截器。
> 原始数据：`data/reqlog_full.json`（46 条完整请求头/请求体/响应）、
> `data/load_response_full.json`、`data/verify_url_full.txt`、`data/verify_response_full.json`、
> `data/localstorage_dump.json`。

## 一、完整调用时序

```
点击「登录」
 ① POST /site/api/v1/user/member/kaptchcate     ← 验证码预注册（每次弹验证码前必调）
 ② GET  bcaptcha.botion.com/load                ← GeeTest 获取挑战（JSONP）
    GET  static.botion.com/{imgs}               ← 背景图 300×200
    GET  static.botion.com/{ques[0..2]}         ← 3 张参考字图
 用户点选坐标 + 确认
 ③ GET  bcaptcha.botion.com/verify              ← GeeTest 校验（JSONP，带 w 参数）
 ④ POST /site/api/v1/user/member/validateGeeCheckV2  ← 站点校验验证码结果
 ⑤ POST /site/api/v1/user/login                 ← 登录，拿 X-API-TOKEN
 ⑥ POST /site/api/v1/user/member/info           ← 用户资料
 ⑦ POST /site/api/v1/user/member/jwt            ← 站点 JWT（含 uuid）
 ⑧ POST /game/api/v1/venue/launch               ← 进场馆时调用，拿游戏 URL/token
```

注意：**每次弹出验证码都会重新调一次 kaptchcate + load**（第一次登录抓到了两轮）。

## 二、公共请求头（所有 /site/api、/act/api、/game/api 请求）

| Header | 值 | 说明 |
|:--|:--|:--|
| X-API-CLIENT | `web` | 固定 |
| X-API-VERSION | `2.0.0` | 固定 |
| X-API-SITE | `2001` | 固定（站点 ID） |
| X-API-UUID | `80C61520-841F-4583-8AAD-65A2DAD2BB8E` | 设备 UUID，来自 `localStorage._uuid`（首次访问生成，持久不变） |
| X-API-TOKEN | 96 hex | 登录成功前的请求也带（旧 token 或空）；登录后换新值 |
| X-API-XXX | 64 hex | **每请求动态签名，每次都不同**（详见第四节） |
| X-API-FINGER | 32 hex | **仅 login 请求携带**，浏览器/设备指纹（本次实测：`99c36b1529f2c9959a5d4aae2e19769f`） |

## 三、各接口明细

### ① kaptchcate — 验证码预注册

```
POST /site/api/v1/user/member/kaptchcate
Body: {"kType": 4}
Resp: {"data": {}, "message": "成功", "status_code": 6022}
```
- `kType:4` = GeeTest v4。`status_code:6022` 即成功。
- **http_login_v2.py 目前缺这一步，纯 HTTP 流程需补上。**

### ② GeeTest load — 获取挑战（JSONP）

```
GET https://bcaptcha.botion.com/load
  ?captcha_id=eaffad4f65a38a259ae369faf0c2f1a3   ← 固定
  &challenge=<UUID>                              ← 每次随机
  &client_type=web&lang=zh
  &callback=botion_<毫秒时间戳>
```

返回关键字段及作用：

| 字段 | 作用 |
|:--|:--|
| `lot_number` | 挑战批次 ID，贯穿 verify → validateGeeCheckV2 → login.codeId |
| `payload` | 加密挑战数据，verify 时原样带回 |
| `process_token` | 服务端会话 token，verify 时原样带回 |
| `imgs` / `ques` | 背景图 / 3 张字图路径，完整地址 `https://static.botion.com/{path}` |
| `pow_detail` | PoW 参数（md5, bits=0），生成 w 用 |
| `pt` / `payload_protocol` | verify 时原样带回 |

### ③ GeeTest verify — 提交校验（JSONP）

```
GET https://bcaptcha.botion.com/verify
  ?callback=botion_<ts>&captcha_id=...&client_type=web
  &lot_number=<load 返回>&payload=<load 返回>&process_token=<load 返回>
  &payload_protocol=1&pt=1
  &w=<1216 hex chars = 608 bytes>
```

**w 参数加密（实测确认 1216 hex，与 captcha-flow.md 最新结论一致；旧文档的 1568 已过时）：**
```
w = hex(AES-CBC(e_obj_JSON, key=16字节随机, IV=全零))   ← 480 bytes (960 hex)
  + hex(RSA-1024(随机key))                               ← 128 bytes (256 hex)
e_obj 含: pow_msg/pow_sign(PoW-MD5)、userresponse(点击坐标)、
          passtime、lot_number、em(环境指纹)、gee_guard、biht、ep、lang 等
```

成功返回：
```json
{"status":"success","data":{
  "lot_number":"...","result":"success","fail_count":0,
  "seccode":{
    "captcha_id":"eaffad4f...",
    "lot_number":"184eb471...",
    "pass_token":"c45c9e8e...64hex",
    "gen_time":"1784288167",            ← unix 秒（字符串）
    "captcha_output":"Wp7goInd...512 base64url chars = 384 bytes 二进制"
  },
  "score":"4"}}
```
- **`captcha_output` 由 verify 响应直接返回**（seccode 内），不需要本地生成。
  本次实测 512 base64url = **384 bytes**（旧文档记 312 bytes，长度以实际返回为准）。
- `pass_token` 64 hex；`gen_time` unix 秒。三件套原样交给 validateGeeCheckV2。

### ④ validateGeeCheckV2 — 站点校验验证码

```
POST /site/api/v1/user/member/validateGeeCheckV2
Body: {"validate_way":1, "lot_number":"4a7982bd...", 
       "captcha_output":"<verify 返回>", "gen_time":"1784288167", "pass_token":"c45c9e8e..."}
Resp: {"data":{"result":"success","reason":"",
        "captcha_args":{"used_type":"word","user_ip":"219.76.134.210",
          "lot_number":"...","scene":"注册","referer":"...",
          "model_probability":0,"web_simulator":0,"ip_overtime":0}},
       "message":"成功","status_code":6000}
```
- `captcha_args` 是 GeeTest 风控回传详情（IP、模拟器判定、模型概率），仅参考。
- `status_code:6000` + `result:success` 才算通过。

### ⑤ login — 登录

```
POST /site/api/v1/user/login
Headers: + X-API-FINGER: 99c36b1529f2c9959a5d4aae2e19769f   ← 仅此接口带
Body: {"name":"lidongsen1",
       "password":"188fb201eff8db830ed26601f6bff11c",   ← MD5(明文) 32hex 小写
       "Kaptchcate":0,                                   ← 0=验证码已校验过
       "codeId":"4a7982bdbdb34784983fc01c75b048d4"}      ← = lot_number
Resp: {"data":{"registerInvitationActivityPopType":"",
        "token":"f422a57e...96hex","userId":"35865137"},
       "message":"登录成功","status_code":6000}
```
- **密码加密 = 纯 MD5**，已实测验证 `MD5("lds19830413") = 188fb201...`。
- 返回 `token` 即 **X-API-TOKEN**（96 hex），写入 `localStorage["X-API-TOKEN"]`。
- token 结构观察：前 64 hex 同账号恒定，后 32 hex 每次登录变化（疑似 32B 账号标识 + 16B 会话）。

### ⑥ member/info — 用户资料

```
POST /site/api/v1/user/member/info   (无 body)
Resp.data: {id, name, avatar, centerMoney, user_ency, inviteCode, ...}
```
- `user_ency` = `localStorage.userEncrypt`（32 hex），用户加密标识。

### ⑦ member/jwt — 站点 JWT

```
POST /site/api/v1/user/member/jwt    (无 body)
Resp.data: "eyJhbGciOi...JWT"
```
JWT payload 解出：`{id, name, nickName, vip, uuid, isAgent, createAt, exp, iss}`
- `uuid` 与 `localStorage._uuid` 一致；`exp` 约 +7 天；`iss` 含前端 API 实例号。

### ⑧ venue/launch — 进场馆（X-API-XXX 签名）

```
POST /game/api/v1/venue/launch
Resp.data: {url:"https://api.wnbtmel.com?token=<40hex>",
            h5Url:"https://app-h5.realcpf.com?token=...&api=<base64>&sessionId=...",
            activityUrl, resource}
```
- 返回游戏后端地址 + 40 hex 游戏 token，供 WS/HTTP 直连使用。

## 四、X-API-XXX 签名 —— 已逆向并验证通过（2026-07-17 更新）

**旧假设（auth-research.md）：签名是按 path 固定的静态表。实测不成立。**

同一端点、相同 body，多次请求的 X-API-XXX 全部不同：

```
/site/api/v1/configuration/vvPretty   9dc4878f... / 79e7625d... / 0049b51d...
/site/api/v1/sec/Hf6dtBdmHx           b19bce03... / 362ed037... / 3d484a01...
/site/api/v1/advertising/queryNoticeList (body 完全相同) 4ee8821b... / 2ccf5254...
```

### 生成机制（已定位）

- 前端 `_app` chunk：`(0,h.TC)(path前两段)` → 模块 87802 → **动态加载 chunk 2284（wasm-bindgen）→ `sign(path, "prod")`**。
- 算法在 **WebAssembly**（`/_next/static/wasm/729ede6e9048bb61.wasm`，wasm_api_sign）中，
  内部使用 `Date.now()` + `Math.random()`，故每请求唯一。
- wasm 加载时必须调用**空名导出**初始化（38464 模块 `r[""]()`），否则 sign 内部 unreachable。
- wasm chunk 加载失败时的兜底：AES-CBC 解密 `localStorage.uuidToBase64`
  （key `ZFRYCMdFYGf0i5HgO0oWvFV0terUABU0` / IV `CbE3P3t1lY34Ns8F`）取静态表 —— 即旧文档发现的表。

### 复现方案（已落地）

- `scripts/sign_wasm.cjs`：Node 直接运行官方 wasm（CLI: `node scripts/sign_wasm.cjs /site/api prod`）。
- `hdata/auth/api_sign.py`：Python 封装（`sign_path()` / `get_uuid()` / `common_headers()`）。
- ⚠️ Git Bash 调用需 `MSYS_NO_PATHCONV=1`，否则 `/site/api` 被转成 Windows 路径导致 wasm unreachable。

### 服务端校验强度（对照实验结论）

| 端点 | 真签名 | 假签名/无签名 |
|:--|:--:|:--:|
| /site/api kaptchcate | ✅ 6022 | ✅ 6022（不校验） |
| /game/api queryGameAppByType | ✅ 6000 正常数据 | ❌ **6003 非法请求** |

**敏感端点（/game/api、login 等）强制校验 X-API-XXX。Python 端带 wasm 签名请求已实测通过（6000）。**

## 四点五、X-API-FINGER —— 已逆向并验证通过

- 来源：模块 70559 `fm()`，fingerprintjs2（模块 68820）。
- 算法：`x64hash128(组件拼接, seed=31)`（MurmurHash3 x64 128，32 hex）。
- Win32/Win64 组件：`colorDepth + screenResolution + timezoneOffset + navigatorPlatform + touchSupport`，
  再拼 `preInfoData.ip`（服务端提供的出口 IP）。
- 实测真值（已入库 `data/finger_groundtruth.json`）：
  `"24" + "1920,1080" + "420" + "0,false,false" + "219.76.134.210"` → `99c36b1529f2c9959a5d4aae2e19769f` ✓ 与抓包一致
- Python 复刻：`hdata/auth/fingerprint.py`（`leyu_finger(ip, ...)`），已对拍一致。
- 仅 login 接口携带；IP 可从 validateGeeCheckV2 响应的 `captcha_args.user_ip` 获得。

## 五、对纯 HTTP 登录（http_login_v2.py）的修正 —— 已完成（2026-07-17）

1. ✅ **补 kaptchcate**：`_kaptchcate()` 在 fetch_captcha 前调用 `POST /user/member/kaptchcate {"kType":4}`。
2. ✅ **补全请求头**：`api_sign.common_headers()` 统一发出 X-API-CLIENT/VERSION/SITE/UUID/XXX；login 另带 X-API-FINGER。
3. ✅ **X-API-XXX 动态签名**：`scripts/sign_wasm.cjs` + `hdata/auth/api_sign.py`，Python 端实测通过 6000。
4. ✅ **captcha_output**：以 verify 实际返回为准（本次 384 bytes），代码未硬编码长度。
5. ✅ login body `{name, password:MD5, Kaptchcate:0, codeId:lot_number}` — 实测确认正确。
6. ✅ **X-API-FINGER**：`hdata/auth/fingerprint.py` 复刻 x64hash128，与浏览器真值对拍一致。

**纯 HTTP 登录剩余唯一卡点：GeeTest verify 的 w 参数**（e_obj 与浏览器 SDK 差 76 bytes，
见 captcha-flow.md 末尾）。verify 之后的全链路（validate → login → jwt）已与浏览器完全对齐。

## 六、localStorage 关键落盘项

| Key | 作用 |
|:--|:--|
| `X-API-TOKEN` | 登录 token（96 hex） |
| `_uuid` | 设备 UUID → X-API-UUID 头 |
| `uuidToBase64` | 签名密钥材料（AES-CBC 加密，可解） |
| `userEncrypt` | = member/info 的 user_ency |
| `YBTY` | 场馆会话（origin/requestId/token） |
| `s_f` | 加密 blob（疑似风控/指纹相关，待查） |

---
> 抓包时间：2026-07-17 04:31 / 04:36（两轮完整登录）
> 原始数据目录：`data/`（reqlog_full.json 等 5 个文件）
