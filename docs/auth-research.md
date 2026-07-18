# 乐鱼认证与 Token 获取研究（2026-07-17 最终版：纯 HTTP 全链路打通）

## 整体认证流程

```
纯 HTTP 登录（已全通）:
  kaptchcate → fetch_captcha → geepass/jfbym solve → generate_w
  → verify ✅ → validateGeeCheckV2 ✅ → login ✅ → member/jwt ✅

获取 JWT（纯 HTTP）:
  POST /game/api/v1/venue/launch (wasm 动态 X-API-XXX 签名)
  → 返回加密 URL (params + ttl)
  → AES-ECB(key=ttl+"AES") 解密 → JWT ✅

WebSocket 连接（2026-07-17 已打通，见 leyu-protocol-complete.md §9）:
  wss://wsproxy.{backendDomainUrl}/?playerId=...&jwtToken=...&deviceId=...
    &platformId=1&applicationId=5&version=v1.0.5
  帧 = AES-128-CBC(gzip(JSON)), key=iv="ED7AA06BD8628B55"
  登录 = protocolId 10000 (Fs.Login), serviceTypeId 7 (Ot.HALL), deviceType 15
```

## Token 获取方案状态

| 方案 | 依赖 | 状态 |
|:----|:----|:-----|
| CDP 提取 session | Chrome 一次性 | ✅ |
| Playwright 手工辅助提取 | 本地浏览器 | ✅ |
| 纯 HTTP 登录（验证码） | geepass/jfbym | ✅ **全链路打通（2026-07-17）** |
| 纯 HTTP JWT 刷新 | session 缓存 | ✅ `refresh_game_token()`（wasm 动态签名） |

## 2026-07-17 三项突破

1. **X-API-XXX 动态签名**：算法在 wasm（`wasm_api_sign`）中，`sign(path前缀,"prod")` 每请求唯一。
   `scripts/sign_wasm.cjs`（Node 直跑官方 wasm）+ `hdata/auth/api_sign.py` 封装。
   服务端对 /game/api、login 等强制校验，假签名返回 6003。详见 `docs/login-api-capture-20260717.md`。
2. **X-API-FINGER**：fingerprintjs2 `x64hash128`（MurmurHash3 x64，seed=31），
   输入 = 色深+分辨率+时区+触摸+出口IP。`hdata/auth/fingerprint.py` 已对拍一致。
3. **GeeTest w / e_obj**：hook Math.random 爆破 AES 密钥解密真实 e_obj，修正字段集
   （删 biht/gee_guard，增 device_id/ep/nqfq/EKAI/em七键，userresponse 改 0-10000 归一化）。
   详见 `docs/captcha-flow.md` 附录。

## X-API-XXX 签名（旧静态表，仅作兜底）

签名表 AES-CBC 解密（key: `ZFRYCMdFYGf0i5HgO0oWvFV0terUABU0`, IV: `CbE3P3t1lY34Ns8F`）——
这是 wasm 加载失败时的浏览器兜底表，**动态 wasm 签名才是主路径**（见上）。

## 文件结构

```
hdt/auth/
  token_manager.py     # 多账号 TokenManager + CLI
  captcha.py           # GeeTest: fetch_captcha + solve (type=31111)
  geetest_signer.py    # w 参数生成（RSA-1024 + AES-CBC）
  http_login.py        # 纯 HTTP 登录流程
  chrome_manager.py    # Headless Chrome 进程管理

hdt/sources/
  leyu_ws.py           # WSSource: 纯 HTTP JWT → WS 直连
  leyu_cdp.py          # CDPSource: CDP DOM 轮询采集
```

## 剩余工作

1. **validateGeeCheckV2 对接** — 将 verify 成功后的 captcha_output 发送到 /site/api/v1/user/member/validateGeeCheckV2，获取 X-API-TOKEN
2. **WS 直连** — 测试 curl_cffi 连接 wsproxy 是否可用
3. **X-API-XXX 自动化** — 签名动态提取或再生

---

> **更新日期:** 2026-06-28
> **关键突破:** 验证码加密完全突破，verify 成功，纯 HTTP 登录链路打通
