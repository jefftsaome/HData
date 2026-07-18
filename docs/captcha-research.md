# 验证码逆向研究记录

> 平台: 乐鱼 (leyu.me) → botion GeeTest v4 (点选文字)
> captcha_id: `eaffad4f65a38a259ae369faf0c2f1a3`

## 流程概览

```
fetch_captcha() → load API (JSONP) → 获取lot_number/payload/pow_detail
                                 ↓
                            geepass/jfbym → 识别坐标
                                 ↓
                          generate_w() → RSA + AES-CBC 签名
                                 ↓
                          verify API → lot_number + w → result
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `bcaptcha.botion.com/load` | JSONP | 获取验证码挑战 |
| `bcaptcha.botion.com/verify` | JSONP | 提交验证结果 |

## 研究成果

### 1. W 参数结构 (已匹配)

真实SDK生成的w参数经20+次对比确认：
- **总长度**: 1216 hex (608 bytes) — 每次一致
- **AES段**: 960 hex (480 bytes) — AES-128-CBC加密的e_obj JSON
- **RSA段**: 256 hex (128 bytes) — RSA-1024加密的random_key
- **AES IV**: random_key的前16字节
- **AES Key**: random_key的后16字节
- **PKCS7填充**: 真实SDK e_obj原文约476 bytes

### 2. 我们的实现状态

`geetest_signer.py`:
- ✅ RSA-1024 PKCS1v1.5: GeekedTest公钥 (n=0xC1E3934D...)
- ✅ AES-128-CBC: 使用random_key + IV
- ✅ PoW: md5(pow_msg) → pow_sign
- ✅ lot_parser: 从lot_number动态生成
- ✅ W总长度匹配: 1216 hex
- ⚠️ e_obj JSON比真实SDK少约76 bytes

### 3. EKAI / ZAhG

- botion使用 `EKAI: "y7R8"` (非标准GeeTest的 `ZAhG: "MwHu"`)
- 来源: 从 `window.__BOTION__.ctStore` 提取
- 该字段名为 `lot_parser` key的动态部分

### 4. 打码平台对比

| 平台 | API | type | 速度 | again_tag |
|------|-----|------|------|-----------|
| geepass | api.geepass.cn | 30104 | ~0.2s | 无 |
| jfbym | api.jfbym.com | 31111 | ~0.5s | 常有=2 |

geepass (30104) 返回边界框 `[[x1,y1,x2,y2],...]`，需转中心点 `[(x1+x2)//2, (y1+y2)//2]`。

### 5. verify API 测试结果

50+ 次测试，使用geepass和jfbym坐标 + 我们的w参数：
- 结果: 始终 `result=fail, fail_count=1`
- 服务器成功解密w (status=success)，但坐标判定失败
- 根因: e_obj JSON字段差异，不是坐标精度问题

### 6. Hook 方法实验

| 方法 | 结果 |
|------|------|
| JSON.stringify patch | ❌ SDK不使用原生JSON.stringify |
| crypto.subtle.encrypt patch | ❌ SDK使用自实现AES (非Web Crypto API) |
| XHR open/send patch | ❌ SDK使用JSONP `<script>` 标签 |
| fetch patch | ❌ SDK不使用fetch |
| appendChild patch | ✅ 成功拦截verify URL，捕获w参数 |
| route拦截 injection | ❌ URL pattern匹配不稳定 |
| CDP Network 抓包 | ✅ 可捕获完整verify URL参数 |

### 7. 未解决: e_obj 字段差异

- 真实SDK e_obj: ~476 bytes
- 我们的 e_obj: ~400 bytes  
- 差异: 76 bytes

botion SDK (704KB混淆，strins XOR编码) 无法通过静态分析确定e_obj的具体字段。
无sourcemap。代码中所有字符串通过`udBgW.$_An` XOR解密器动态解码。

### 8. e_obj 已知字段

```json
{
    "pow_msg": "...md5 hash...",      // PoW消息
    "pow_sign": "...md5 hash...",     // PoW签名  
    "<lot_parser_key>": {             // 动态key名 (如"f20a")
        "<subkey>": "<lot_res>"       // 如"40f7":"d299"
    },
    "EKAI": "y7R8",                   // botion特有,非标准ZAhG
    "biht": "1426265548",
    "em": {                           // 环境检测 (字段不确定)
        "cp": 0,
        "ek": "11"
    },
    "gee_guard": {"roe": {...}},      // 守卫字段
    "geetest": "captcha",
    "lang": "zh",
    "lot_number": "...",
    "userresponse": [[x,y],[x,y],[x,y]],  // 点选坐标
    "passtime": 1500-3500             // 点击耗时ms
}
```

可能缺少的botion特定字段: `device_id`, `ep`, `rp`, `hw` 等。

## 相关文件

- `data/sdk_flow_captured.json` — 人工登录时SDK全链路数据 (参考值)
- `scripts/capture_real_w.py` — 浏览器辅助获取真实w参数
- `scripts/compare_coords.py` — 坐标对比分析
- `hdata/auth/geetest_signer.py` — 我们的w参数生成实现
