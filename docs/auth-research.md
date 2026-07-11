# 乐鱼认证与 Token 获取研究（2026-06-28 最终版）

## 整体认证流程

```
纯 HTTP 登录:
  fetch_captcha() → jfbym solve() → generate_w() → verify → captcha_output
       ✅               ✅              ✅           ✅     → validateGeeCheckV2  ⏳ 待对接

获取 JWT（纯 HTTP）:
  POST /game/api/v1/venue/launch (X-API-XXX 签名)
  → 返回加密 URL (params + ttl)
  → AES-ECB(key=ttl+"AES") 解密 → JWT

WebSocket 连接:
  wss://wsproxy.{host}:{port}/?playerId=...&jwtToken=...&deviceType=2&platform=6
```

## Token 获取方案状态

| 方案 | 依赖 | 状态 |
|:----|:----|:-----|
| CDP 提取 session | Chrome 一次性 | ✅ |
| BrowserAct stealth 提取 | BrowserAct CLI | ✅ |
| 纯 HTTP 登录（验证码） | jfbym | ✅ verify 已通，待对接 validate |
| 纯 HTTP JWT 刷新 | session 缓存 | ✅ `token_manager.get_token()` |

## GeeTest 验证码加密（已突破）

详见 `captcha-research.md`。

- w = hex(AES-CBC) + hex(RSA-1024)（单次加密，1568 chars）
- RSA 公钥与标准 GeeTest 一致
- e_obj 包含全部标准字段（含 `ZAhG: "MwHu"`）

## X-API-XXX 签名

签名表 AES-CBC 解密（key: `ZFRYCMdFYGf0i5HgO0oWvFV0terUABU0`, IV: `CbE3P3t1lY34Ns8F`）。新域名签名值为空——需要从浏览器网络请求中捕获真实签名，手动注入 session 缓存。

**当前 session 可用签名**（从 BrowserAct 捕获）：
- `/game/api`: `60358732c589e34b1211d173273e480d969f457adaa7cca735466145bb336634`
- `/site/api`: `f756f9fa09856322a815c9b5ec2cbb7cdafa3979e65d9339f783b2dc8963aa08`

`token_manager.py` 已支持 `signatures` 字段作为 uuidToBase64 解密失败的兜底方案。

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
