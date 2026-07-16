# 无浏览器登录验证码参数修复设计

## 背景

当前无浏览器登录接口只接收一个 `captcha_token`，`http_login_v2._solve_captcha()` 却先把它交给 geepass，再把同一个值交给 jfbym。两个平台使用不同 token，因此当前实现必然至少向一个平台发送错误凭据。现有测试中的验证码识别已经成功过，随后失败集中在 GeeTest `verify` 阶段；该问题与 token 路由缺陷相互独立。

本次修改首先消除平台 token 的歧义，再为 `verify` 参数生成建立可复现的诊断和回归边界。不会把“打码平台返回坐标”误判为“验证码验证成功”。

## 目标

- geepass 和 jfbym 使用各自独立的 token。
- 支持只配置一个平台，也支持 geepass 优先、jfbym 降级。
- 保留旧 `captcha_token` 调用的兼容性，并明确将其解释为 jfbym token。
- 日志能区分识别、`generate_w`、`verify`、站点校验和登录阶段。
- 敏感 token、密码、图片 Base64 和完整认证响应不得写入日志或异常。
- 使用固定样本验证 `e_obj` 的字段、类型、序列化和加密前长度，缩小 `verify result=fail` 的定位范围。

## 非目标

- 不修改浏览器登录流程。
- 不恢复或提交工作区中与本问题无关的文件。
- 不保证仅靠 token 路由修改就能让 `verify` 成功。
- 不把随机生成的完整 `w` 与固定字符串直接比较。

## 方案

### 公共接口

在 `hdata.auth.api.get_login()`、`hdata.auth.session.get_login()` 和 `hdata.auth.http_login_v2.login()` 增加：

- `geepass_token: str = ""`
- `jfbym_token: str = ""`

旧的 `captcha_token` 暂时保留。兼容规则为：如果没有显式提供 `jfbym_token`，则把 `captcha_token` 作为 jfbym token；它不再被猜测为 geepass token。显式参数优先于兼容参数，函数参数优先于环境变量。

环境变量分别使用 `GEEPASS_TOKEN` 和 `JFBYM_TOKEN`。`CAPTCHA_TOKEN` 仅作为旧 jfbym 配置的兼容回退。任何代码路径都不得将同一个兼容 token 自动复用于两个平台。

### Solver 链

登录层根据已配置 token 创建有序 Solver 列表：

1. 配置了 `geepass_token` 时加入 `GeepassSolver`。
2. 配置了 `jfbym_token` 时加入 `JfbymSolver`。
3. 列表为空时跳过 HTTP 验证码登录，并返回明确的配置错误。

`_solve_captcha()` 接收 Solver 列表并依次调用。某个平台失败时记录脱敏的结构化错误并尝试下一项；成功时返回 `CaptchaSolution`，包括平台名对应的元信息。所有平台均失败时抛出汇总异常，但异常中不包含 token 或请求图片。

同一挑战首次 `verify` 失败后，不再用相同 Solver 链重复识别同一图片。外层重试重新获取挑战，再重新识别，避免重复计费和复用已失败的挑战状态。

### Verify 参数诊断边界

将 `generate_w()` 中的纯数据构造提取成独立函数，例如 `build_e_obj(load_data, captcha_id, coords, *, passtime, pow_nonce)`。生产路径仍生成随机 `passtime` 和 PoW nonce；测试路径注入固定值。

回归测试不解密完整 `w`，而是直接验证加密前对象：

- `userresponse` 必须是整数二维数组，顺序与平台结果一致。
- `lot_number`、PoW 字段和动态 lot mapping 必须来自同一次挑战。
- JSON 使用紧凑分隔符，字段类型稳定。
- 固定样本的 JSON 字节长度和字段集合与已捕获的成功客户端样本一致。

若仓库没有可确认成功的客户端样本，则测试只固定当前已确认的协议约束，并将样本差异作为诊断输出，不凭猜测更改 `e_obj` 字段。

`_verify_captcha()` 解析 JSONP 后返回明确结果对象或带阶段信息的异常。`result=fail`、JSONP 解析错误、缺少 `seccode` 和网络错误必须可区分。

## 数据流

1. API 层解析显式参数、兼容参数和环境变量。
2. 登录层创建平台与 token 一一对应的 Solver 链。
3. 获取新挑战并交给 Solver 链识别。
4. 用识别坐标和同一挑战构造 `e_obj` 与 `w`。
5. 提交 `verify`；只有取得完整 `seccode` 才进入站点校验。
6. 站点校验成功后调用登录接口。

每层只返回下一层所需的数据，失败时保留阶段和平台信息，不携带秘密值。

## 错误处理

- 无可用 Solver：配置错误，不发起验证码请求。
- 单个平台失败：记录平台、错误类别和响应 code，继续降级。
- 所有平台失败：抛出汇总识别错误。
- `generate_w` 输入非法：在网络请求前失败，并指出字段名。
- `verify result=fail`：记录 `fail_count`、挑战标识的短前缀以及非敏感的 `e_obj` 结构摘要。
- 站点校验或登录失败：保留站点状态码和脱敏消息，不回退到错误的平台 token。

## 测试策略

先写失败测试，再修改生产代码：

- geepass token 只进入 `GeepassSolver`。
- jfbym token 只进入 `JfbymSolver`。
- 双 token 时按 geepass → jfbym 顺序降级。
- 旧 `captcha_token` 只映射为 jfbym。
- 显式 token 覆盖兼容参数和环境变量。
- 所有异常和日志均不包含 token。
- Solver 成功但 `verify` 失败时，整体登录仍失败且阶段明确。
- 固定 `load_data`、坐标、`passtime` 和 PoW nonce 时，`e_obj` 结构和 JSON 输出稳定。
- 非法坐标、缺少 PoW 字段和跨挑战数据在发送网络请求前被拒绝。

测试使用注入的 Solver 和 HTTP 传输替身，不调用真实打码平台，也不消耗测试 token。完成后运行目标测试和完整测试套件。

## 兼容与迁移

现有只传 `captcha_token` 的调用继续工作，但其含义固定为 jfbym。CLI 增加 `--geepass-token` 与 `--jfbym-token`；旧 `--captcha-token` 或现有等价入口保留为 jfbym 别名，并给出弃用提示。文档示例只引用环境变量名，不出现真实 token。

## 完成标准

- 两个平台的 token 不会交叉发送。
- 单平台和双平台调用都有自动化测试。
- `verify` 失败能准确归因，不会被识别阶段吞掉。
- `e_obj` 可在固定输入下独立回归测试。
- 目标测试与完整测试套件通过，且未提交无关工作区改动。
