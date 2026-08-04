# HData 架构文档

> 状态:持续更新(结构变化时同步修订)
> 更新时间：2026-08-05(重构后)。本文件描述 2026-08 大重构后的真实结构，是理解代码的入口。
> 配套阅读：`docs/GLOSSARY.md`(术语)、`docs/INCIDENTS.md`(事故知识库)、`AGENTS.md`(开发约定)。

## 一、包定位

`hdata` 是对某亚洲真人视讯百家乐平台(代号 leyu)做**数据采集**的 Python SDK。核心能力：

1. **登录**：多级降级链(缓存 → HTTP API → 浏览器自动化 → HTTP 验证码打码)
2. **反爬对抗**：wasm 动态签名、设备指纹、打码平台、schema 协议热更新
3. **采集**：扫描全部桌台、进单桌/多桌/全平台多台订阅、解析牌局事件
4. **稳定运行**：代理池、踢出重进、静默判死看门狗、分片重建、断线重连

对外不暴露 leyu 字眼，可打包为 pyd/so 供外部项目调用。

## 二、模块总览

```
hdata/
├── __init__.py       公共入口(聚合导出 GameClient / get_login 等，见 __all__)
├── client/           对外门面 + WS 传输层 + 会话状态机(公共 API 主要来源)
│   ├── __init__.py   GameClient 门面 + 全部 re-export
│   ├── _shared.py    协议常量 + 共享叶子函数(打破包内回环用)
│   ├── transport.py  _WSConnection：WS 连接/登录帧/心跳/看门狗
│   ├── tables.py     TableSession / MultiTableSession / TableMonitor / MultiplaySession
│   └── gateway.py    _gateway_request：HTTP 网关 HMAC 加签
├── auth/             登录/会话/token/签名/打码
│   ├── session.py    参数管理层：域名解析、get_login 编排、WS 配置
│   ├── login_orchestrator.py  L0-L4 降级链实现(原 TokenManager 逻辑)
│   ├── token_manager.py      TokenManager 薄门面(委托 orchestrator)
│   ├── captcha_client.py     HTTP 验证码登录(委托 http_login 完整流水线)
│   ├── http_login.py         纯 HTTP 登录完整流水线(打码/重试/域切换)
│   ├── browser_login.py      Playwright 浏览器登录(人工/headless)
│   ├── headers.py            API 请求头(签名链 wasm→手动表→解密)
│   ├── sign_table.py         uuidToBase64 签名表解密叶子模块
│   ├── domain.py             域名解析(唯一实现)
│   ├── api_sign.py           wasm 动态签名
│   ├── captcha_solver.py     打码平台抽象 + Jfbym/Geepass 实现
│   └── ...                   指纹/参数/埋点等
├── protocol/          协议编解码
│   ├── codec.py       帧编解码(协议号/常量/签名消息构造)
│   ├── schemacodec.py schema 热更新解码器 + 运行时配置
│   ├── _schema_data.py schema 基线(2402 行数据已外置 schema_data.json)
│   └── roadpaper.py   路纸解码
├── capture/           CDP DOM 采集(连 Chrome 调试端口)
│   ├── dom_extractor.py  DOM 提取(JS 资产外置于 js/)
│   ├── dom_parser.py     DOM 数据解析
│   └── cdp_bridge.py     CDPSession(Chrome DevTools Protocol)
├── proxy.py           ProxyPool 代理池(探活/均衡/死亡复活)
├── sources/           DataSource 入口(CDPSource)
├── adapters/          适配层(接 htools DataSource 接口)
└── types/             统一数据结构(TypedDict)
```

## 三、数据流(采集主线)

```
GameClient.login() / get_login()
    → auth.session 编排:缓存命中 → HTTP API 刷新 → 浏览器 → HTTP 验证码
    → 产出 session dict {game_token, game_player_id, game_backend, ...}

GameClient.get_tables()
    → _WSConnection(WS 连 wsproxy) → 发送 10089 大厅订阅
    → schemacodec 解码 schema 帧 → 桌台列表

GameClient.enter_table(tid) / enter_tables([...]) / monitor_tables(...)
    → TableSession / MultiTableSession / TableMonitor 状态机
    → 持续接收 103/104/106/107 牌局帧 → 解码 → events() 产出事件 dict
```

**关键机制**：
- WS URL 由 `auth.session.build_ws_config` 唯一构造(`wss://wsproxy.{backend}/?playerId=..&jwtToken=..&deviceId=..`)
- 帧格式：`AES-128-CBC(gzip(JSON))` 密文，key=iv=`ED7AA06BD8628B55`
- schema 协议热更新：登录响应 `protocolCodecConfig` 或 10115 推送 → `schemacodec.update_schema_config`

## 四、依赖方向(重要约束)

```
hdata.__init__ → client → {transport, tables, gateway} → _shared → protocol → (paths)
                    └→ auth.session → {headers, sign_table, domain, params, fingerprint} → 叶子
```

- **叶子模块**(无环，可任意 import)：`protocol/*`、`auth/headers.py`、`auth/sign_table.py`、`auth/domain.py`、`auth/params.py`、`types/`、`paths.py`
- **已标注的编排回环**(6 处，函数内 import 以避死锁，见代码注释 `# 编排回环`)：`session ↔ login_orchestrator ↔ http_login/browser_login`
- **规则**：新代码不得在叶子模块 import 上层模块；不得新增函数内懒 import 来绕环——若遇环，先抽叶子再连线

## 五、公共 API 契约(hdata.__all__)

```python
from hdata import (
    GameClient,        # 门面：login/get_tables/enter_table/enter_tables/monitor_tables
    get_login,         # 登录(自动降级)
    TableSession,      # 单桌会话
    MultiTableSession, # 多桌聚合会话
    TableMonitor,      # 多账号分片监控
    TableInfo,         # 桌台信息
    LoginError,        # 登录错误
    road_streak,       # 路纸连胜计算
    GOOD_ROAD_NAMES,   # 路纸类型名
)
```

## 六、逆向工程包维护纪律

本包是**事故驱动 + 服务器端约束**的逆向工程包，维护纪律特殊：

1. **补丁是资产**：踩坑换来的逻辑不可随意删除；补丁必须带注释(触发场景/时间/证据/失效条件)
2. **live 链路只包壳不重写**：`session._get_login_inner`、`schemacodec` 热更新、`_gateway_request` HMAC、`_WSConnection` 心跳/看门狗为禁忌区——只允许机械搬移/收敛拷贝，不重写内部
3. **行为锁定**：任何结构性改动前先写特征测试(参考 `tests/test_characterization_client.py`)
4. **逐差异决策**：合并重复实现时逐个决策(D1/D2/...)，未知差异保留不猜
5. **测试基线**：`285 passed + 11 skipped + 1 xfailed`(见 AGENTS.md)
