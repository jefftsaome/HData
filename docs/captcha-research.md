# GeeTest v4 验证码逆向研究（2026-06-28 最终版）

## ✅ 纯 HTTP 登录链路已打通

```
fetch_captcha() → jfbym solve() → generate_w() → verify → captcha_output
     纯 HTTP         纯 HTTP         纯 HTTP       ✅      纯 HTTP
```

## w 参数加密（已确认）

```
w = hex(AES-CBC(full_e_obj, 16byte_random_key, zero_IV)) + hex(RSA-1024(random_key))
     └─────────────── 656 bytes (1312 hex) ──────────────┘   └── 128 bytes (256 hex) ──┘
     = 1568 hex chars total
```

**参数**：
- RSA: 1024-bit，PKCS1v1.5，**单次**加密（与标准 GeeTest/GeekedTest 一致）
- AES: CBC 模式，零 IV（`b"0000000000000000"`），PKCS7 填充，16 字节随机 key
- RSA 公钥与标准 GeeTest 相同（来自 GeekedTest 项目，bcaptcha.js 反混淆表条目 [305] 确认）

## 正确 e_obj 结构（~645 字节）

```json
{
  "pow_msg": "1|0|md5|<datetime>|<captcha_id>|<lot>||<rand_hex>",
  "pow_sign": "<md5(pow_msg)>",
  "<lot_parser_key>": {"<subkey>": {"<lot_substr>": "<lot_res>"}},
  "ZAhG": "MwHu",
  "biht": "1426265548",
  "device_id": "",
  "em": {"cp":0,"ek":"11","nt":0,"ph":0,"sc":0,"si":0,"wd":1},
  "gee_guard": {"roe":{"auh":"3","aup":"3","cdc":"3","egp":"3","res":"3","rew":"3","sep":"3","snh":"3"}},
  "ep": "123",
  "geetest": "captcha",
  "lang": "zh",
  "lot_number": "<from load API>",
  "userresponse": [[x1,y1],[x2,y2],[x3,y3]],
  "passtime": 600-1200
}
```

**关键点**：
- `userresponse` 必须是二维数组 `[[x,y],...]`，不是字符串
- `ZAhG: "MwHu"` 是标准 GeeTest 的动态键值对（来自 `window._lib`）
- botion 不使用自己的 `_lib` 值（`EKAI: "y7R8"`），而是用标准 GeeTest 的 `ZAhG: "MwHu"`
- **所有标准字段都必须包含**，精简版 e_obj 会导致 `-50000`/`-50002` 错误

## 纯 HTTP 登录流程状态

| 步骤 | 状态 | 模块 |
|------|------|------|
| GeeTest load | ✅ | `captcha.fetch_captcha()` |
| jfbym 坐标识别 | ✅ | `captcha.solve()` type=31111, extra="je4_click" |
| w 参数生成 | ✅ | `geetest_signer.generate_w()` |
| GeeTest verify | ✅ | 返回 captcha_output |
| validateGeeCheckV2 | ⏳ | 待对接 |
| 登录 POST | ⏳ | 待对接 |

## RSA 公钥（1024-bit）

```
n = 0x00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81
e = 0x10001
```

来源：GeekedTest 项目，与标准 GeeTest 完全一致。

## 错误码

| 错误码 | 含义 |
|--------|------|
| `-50000` | 加密/解密失败（w 格式错误） |
| `-50002` | 参数解密错误（e_obj 字段缺失或格式不对） |
| `success` | 验证码通过 |

## 验证码类型

乐鱼使用 **GeeTest v4 "文字点选"（word click）**：
- 背景图（300×200 JPG）：江城正君体中文字符
- 3 张参考字图（64×65 RGBA PNG）
- captcha_id: `eaffad4f65a38a259ae369faf0c2f1a3`
- 域名: `bcaptcha.botion.com`

## 代码文件

| 文件 | 用途 |
|------|------|
| `hdt/auth/captcha.py` | fetch_captcha + solve (type=31111) |
| `hdt/auth/geetest_signer.py` | generate_w (AES+RSA) |
| `hdt/auth/http_login.py` | 纯 HTTP 登录流程 |

---

> **更新日期:** 2026-06-28
> **状态:** 验证码加密完全突破，verify 成功
