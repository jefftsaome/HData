# 验证码验证流程完整拆解

## 总体架构

```
浏览器中的 GeeTest SDK                        我们的 Python 代码
═══════════════════════                       ═══════════════════
1. 弹窗显示验证码
2. 等待用户点击                                提取验证码图片 (DOM)
3. 接收点击坐标                                jfbym 识别坐标
4. 生成 w 参数                                CDP Input 点击坐标
5. POST botion.com/verify                     ← SDK 自动完成
6. POST kaptchcate                            ← SDK 自动完成
7. POST validateGeeCheckV2                    ← SDK 自动完成
8. POST login                                 ← SDK 自动完成
9. localStorage 写入 token                     等待 token 出现 → 提取
```

**关键：我们只管点击坐标，SDK 负责所有后端验证请求。**

## 各步骤详解

### 1. GeeTest Load — 获取挑战数据

SDK 调用：
```
GET https://bcaptcha.botion.com/load
  ?captcha_id=eaffad4f65a38a259ae369faf0c2f1a3    ← 固定值
  &challenge=<UUID>                                   ← 随机 UUID
  &client_type=web
  &risk_type=word                                     ← 文字点选
  &lang=zh-cn
  &callback=geetest_<timestamp>
```

返回 (JSONP)：
```json
{
  "status": "success",
  "data": {
    "lot_number": "8b774660f642430b8bb691f2f07982c9",  ← 挑战批次 ID
    "payload": "AgFD8gWUUuHFx...",                      ← 加密挑战数据
    "process_token": "79820a4f518b...",                  ← 服务端会话 token
    "imgs": "captcha_v4/botion_policy/.../xxx.jpg",     ← 背景图路径
    "ques": ["nerualpic/.../a.png", ".../b.png", ".../c.png"],  ← 参考字图
    "pow_detail": {"hashfunc":"md5","version":"1","bits":0,"datetime":"..."},
    "captcha_type": "word",
    "pt": "1",
    "payload_protocol": "1"
  }
}
```

完整 URL 格式：
- 背景图：`https://static.botion.com/{imgs}`
- 参考字图：`https://static.botion.com/{ques[i]}`

### 2. jfbym 识别 — 获取坐标

我们用 DOM 提取的背景图和参考字图（与 SDK 显示的是同一个挑战），发给 jfbym：

```
POST http://api.jfbym.com/api/YmServer/customApi
{
  "token": "xxx",
  "type": "31111",
  "image": "<base64 背景图 JPG, 300x200>",
  "image_label1": "<base64 参考字图 1 PNG, 64x65>",
  "image_label2": "<base64 参考字图 2 PNG, 64x65>",
  "image_label3": "<base64 参考字图 3 PNG, 64x65>",
  "extra": "je4_click"
}
```

返回：
```json
{
  "code": 10000,
  "data": {
    "data": "74,124|235,132|176,65"    ← 坐标字符串
  }
}
```

解析为二维数组：`[[74,124], [235,132], [176,65]]`

坐标是基于原始背景图 (300×200) 的像素位置。

### 3. 坐标点击 — CDP Input 事件

在浏览器中，`botion_click` 元素显示了经过 CSS 缩放的背景图。jfbym 坐标需要缩放到实际显示尺寸后，通过 CDP `Input.dispatchMouseEvent` 发送原始鼠标事件。

```
缩放: scale_x = botion_click.width / 300
      scale_y = botion_click.height / 200
页面坐标: x = botion_click.x + jfbym_x * scale_x
          y = botion_click.y + jfbym_y * scale_y

CDP 事件序列:
  Input.dispatchMouseEvent { type: "mouseMoved",  x, y }
  Input.dispatchMouseEvent { type: "mousePressed", x, y, button: "left" }
  Input.dispatchMouseEvent { type: "mouseReleased", x, y, button: "left" }
```

必须用 raw CDP Input 事件，Playwright 的 `page.mouse.click()` 会被 GeeTest SDK 检测为自动化。

### 4. w 参数生成（浏览器 SDK 内部）

SDK 接收到点击后，内部生成 w 参数。这是 GeeTest v4 的核心加密：

```
e_obj = {
    "pow_msg": "1|0|md5|<datetime>|<captcha_id>|<lot_number>||<rand>",
    "pow_sign": "<md5(pow_msg)>",                    ← PoW 工作量证明
    "<lot_parser_key>": {"<subkey>": "<value>"},     ← 动态 key，从 lot_number 提取
    "userresponse": [[x1,y1],[x2,y2],[x3,y3]],       ← 点击坐标
    "passtime": 600-1200,                             ← 随机时间 ms
    "lot_number": "...",
    "device_id": "",
    "em": {"cp":0,"ek":"11","nt":0,"ph":0,"sc":0,"si":0,"wd":1},  ← 环境指纹
    "gee_guard": {"auh":"3","aup":"3","cdc":"3","egp":"3","res":"3","rew":"3","sep":"3","snh":"3"},
    "ZAhG": "MwHu",                                  ← 标准 GeeTest _lib 键值对
    "biht": "1426265548",
    "ep": "123",
    "geetest": "captcha",
    "lang": "zh"
}

random_key = 16字节随机 hex 串

w = hex(AES-CBC(e_obj_json, random_key, zero_IV))    ← 前半段 656 bytes (1312 hex)
  + hex(RSA-1024(random_key))                         ← 后半段 128 bytes (256 hex)
  = 1568 hex chars
```

AES key：`random_key`（每次随机）
RSA 公钥：1024-bit，从 GeekedTest / bcaptcha.js 提取的全局统一密钥
```
n = 0x00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74...
e = 0x10001
```

### 5. Verify — 提交验证码

SDK 调用：
```
GET https://bcaptcha.botion.com/verify
  ?callback=botion_<timestamp>
  &captcha_id=eaffad4f65a38a259ae369faf0c2f1a3
  &client_type=web
  &lot_number=<从 load 获取>
  &payload=<从 load 获取>
  &process_token=<从 load 获取>
  &payload_protocol=1
  &pt=1
  &w=<生成的 w 参数, 1568 hex chars>
```

返回 (JSONP)：
```json
botion_xxx({
  "status": "success",
  "data": {
    "seccode": "...",
    "lot_number": "...",
    "score": "...",
    ...其他字段...
  }
})
```

**captcha_output：** verify 返回的 data 中某个字段（或整个结果）的编码/加密形式，312 bytes URL-safe base64 编码二进制数据。**这是 SDK 内部生成的值，无法在纯 Python 中复现。** 这就是纯 HTTP 方案卡住的地方。

### 6. kaptchcate — 验证码预注册

SDK 调用（浏览器内）：
```
POST https://www.<domain>.vip:<port>/site/api/v1/user/member/kaptchcate
Headers: X-API-CLIENT: web, X-API-SITE: 2001, X-API-VERSION: 2.0.0, X-API-UUID: <UUID>
Body: {"kType": 4}
```

返回：
```json
{"data": {}, "message": "成功", "status_code": 6022}
```

### 7. validateGeeCheckV2 — 验证码校验

SDK 调用（浏览器内）：
```
POST https://www.<domain>.vip:<port>/site/api/v1/user/member/validateGeeCheckV2
Headers: X-API-UUID: <UUID>, ...
Body: {
    "validate_way": 1,
    "lot_number": "daae8be5966e42f5bbb9554c500b31b0",
    "captcha_output": "<312 bytes base64 加密二进制>",
    "gen_time": "1782661577",
    "pass_token": "355cd404845e..."
}
```

返回：
```json
{
  "data": {
    "result": "success",
    "reason": "",
    "captcha_args": {
      "used_type": "word",
      "user_ip": "...",
      "lot_number": "...",
      "scene": "注册",
      "referer": "...",
      "model_probability": 0,
      "web_simulator": 0,
      "ip_overtime": 0
    }
  },
  "message": "成功",
  "status_code": 6000
}
```

### 8. Login — 登录

SDK 调用（浏览器内）：
```
POST https://www.<domain>.vip:<port>/site/api/v1/user/login
Body: {
    "name": "lidongsen1",
    "password": "<MD5>",
    "Kaptchcate": 0,                                ← 0 表示"已验证过"
    "codeId": "daae8be5966e42f5bbb9554c500b31b0"    ← lot_number，关联验证码
}
```

返回：
```json
{
  "data": {
    "token": "f422a57ebbef2b4150c60e3de71d19067b84347d8bebd3950ece8167c4be...",
    "userId": "35865137"
  },
  "message": "登录成功",
  "status_code": 6000
}
```

返回的 token 写入 `localStorage.X-API-TOKEN`。

## 数据流总结

```
输入                                 处理                        输出
═══════════════════════════════════════════════════════════════════════
验证码图片 (DOM)              → jfbym 31111 API           → 坐标 "x1,y1|x2,y2|x3,y3"
坐标 + botion_click 位置     → 缩放 + CDP Input           → 浏览器 SDK 收到点击
SDK 内部                     → PoW + AES-CBC + RSA-1024   → w 参数 (1568 hex)
w + load 参数                → botion.com/verify          → captcha_output (312b 加密)
captcha_output + lot_number  → validateGeeCheckV2         → {"result": "success"}
验证通过 + 表单               → login API                  → X-API-TOKEN → localStorage
```

## 关键结论

1. **captcha_output 只能在浏览器中生成**。它是 SDK 内部对 verify 响应的加密结果。312 bytes 二进制，非 UTF-8，纯 Python 无法复现。

2. **我们的角色是"代点"**。不需要自己调 verify、kaptchcate、validateGeeCheckV2——浏览器的 GeeTest SDK 全包了。我们只需要把坐标点对。

3. **CDP Input 是唯一可靠的点击方式**。Playwright 的 page.mouse.click() 被 SDK 检测为自动化。

4. **登录请求中 Kaptchcate=0** 表示"验证码已在 validateGeeCheckV2 中校验过，login 不需要再次校验"。codeId=lot_number 是关联验证码批次的 key。
