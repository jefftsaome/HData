# AGENTS.md — 给 AI/新手的交接文件

> 本文件是任何 agent(或新接手人员)接手本仓库**必须先读**的约定。简短、可执行。
> 文档索引见 `docs/README.md`;结构见 `docs/ARCHITECTURE.md`;术语见 `docs/GLOSSARY.md`;事故库见 `docs/INCIDENTS.md`;协议研究见 `docs/protocol/`。

## 文档命名规范(必须遵守)

- **活文档**(持续维护):文件名不带日期,头部标注 `> 状态:持续更新`。结构/接口变化时修订。
- **结论文档**(带时间性):文件名带日期后缀 `-YYYYMMDD`,头部标注 `> 结论截至:YYYY-MM-DD`。
  逆向结论有保质期——平台更新后按日期判断结论是否仍适用。
- 新写文档先定性质:是会随代码演进的活文档,还是某时刻的逆向结论快照?据此命名。

## 这是什么

对某亚洲真人视讯百家乐平台(代号 leyu)的数据采集 Python SDK。支持：登录(多级降级)、扫描全部桌台、采集单桌/多桌/全平台牌局数据、代理池、采集状态自愈。

## 环境与命令

```powershell
# 工作目录
cd D:\my-code-repo\myown\HData
# 解释器(uv 管理的 venv，无 pip)
& .venv\Scripts\python.exe ...
# 跑测试(必须全绿才可提交)
& .venv\Scripts\python.exe -m pytest -q
# 冒烟(公共入口可用)
& .venv\Scripts\python.exe -c "import hdata; print(hdata.__all__)"
```

## 测试基线(必须保持)

- `285 passed + 11 skipped + 1 xfailed`
- 那个 `xfailed` 是 `tests/test_http_captcha_login.py::test_session_http_login_failure_does_not_log_exception_secrets`(已知预存在泄漏，已标 xfail，**不要尝试"修好"它**，那是历史遗留问题)
- 新增/修改代码必须跑全套；结构性改动前先写特征测试锁定行为

## 铁律(违反即打回)

1. **行为不变**：重构/清理不得改变对外签名、返回结构、补丁分支语义
2. **live 链路只包壳不重写**：`auth/session.py:_get_login_inner`、`auth/login_orchestrator.py`(L0-L4)、`protocol/schemacodec.py` 热更新、`client/gateway.py` HMAC、`client/transport.py` 心跳/看门狗——只允许机械搬移，不重写内部
3. **不新增函数内懒 import 绕环**：若遇 import 环，先抽叶子模块再连线；叶子(protocol/auth.headers/auth.sign_table/auth.domain/auth.params)不得 import 上层
4. **补丁带注释**：踩坑逻辑必须写清触发场景/时间/证据；新补丁记入 `docs/INCIDENTS.md`
5. **不批量加 noqa**：lint 未追 0 是有意为之(见下)，不要靠 noqa 硬塞

## 当前技术债(有意保留，不要"顺手清")

- **剩余 ~105 个 ruff lint 错误**：`ruff check hdata tests` 可查。仅修自动项(`--fix`)；手动项不在本任务范围
- **时区偏移 3 处语义不等**：`http_login._local_tz_offset`(DST 动态) vs `client/_shared.py`+`protocol/codec.py`(静态)。**禁止合并**，会改变 DST 行为
- **编排回环 6 处**：`session ↔ login_orchestrator ↔ http_login/browser_login` 的函数内 import(注释已标 `# 编排回环`)
- **无 mypy**：类型标注不完全，未配置 mypy，不在本阶段范围

## 工作流建议

1. 新任务先读 `docs/ARCHITECTURE.md` 对应层;文档索引见 `docs/README.md`
2. 改代码前跑全套确认基线绿
3. 小步提交，commit message 中文描述式(参考 git log)
4. 每改一处跑 `import hdata` 冒烟 + 相关测试
5. 遇到不认识的协议号/机制查 `docs/GLOSSARY.md`;遇事故查 `docs/INCIDENTS.md`;协议细节查 `docs/protocol/`
