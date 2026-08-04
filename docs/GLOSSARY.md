# 术语表(GLOSSARY)

> 状态:持续更新(新增术语随时补充)
> 逆向工程包的口头知识集中地。遇到不认识的协议号/机制先查这里。
> 来源：代码注释、事故复盘、逆向结论。配套：`docs/ARCHITECTURE.md`、`docs/INCIDENTS.md`。

## 协议帧与编码

| 术语 | 含义 |
|---|---|
| **lefun 帧** | WS 传输的协议帧格式。帧 `data` 字段是 base64，AES-128-CBC(gzip(JSON)) 密文 |
| **AES_KEY** | `ED7AA06BD8628B55`(key=iv 相同)，协议帧加解密 |
| **codecFlag** | 帧是否携带 schema 二进制载荷(10053/10089 等"codec"帧) |
| **protocolId** | 帧协议号(pid)，如 3=心跳、103=新靴、104=新局、106=发牌、107=结算、160=状态、161=路纸 |
| **serviceTypeId** | 服务类型(如 7=大厅、2=多台) |
| **schema 热更新** | 服务器下发协议 schema 定义(`protocolCodecConfig`/10115 推送)，`schemacodec.update_schema_config` 动态替换解码器 |

## 协议号速查

| pid | 含义 |
|---|---|
| 3 | 心跳 |
| 10089 | 大厅桌台列表(分页元数据) |
| 10053 | 分页桌台元数据(schema 帧) |
| 10027 | GAME_LIST_SWITCH_TAB 大厅订阅 |
| 10052 | TABLE_DATA_UPDATE 桌台增量推送 |
| 10115 | 服务器推送 schema 配置 |
| 101/401 | 进桌(Ot.GAME) |
| 102 | 出桌/踢出 |
| 103/104/106/107 | 新靴/新局/发牌/结算 |
| 160/161 | 桌状态/路纸 |
| 301 | 多台订阅 |
| 10026 | 会话被踢 |
| 4/5/6/10/11 | 服务器控制帧(强制重连/多台重连/限流) |

## 协议常量与规则

| 术语 | 含义 |
|---|---|
| **WS_STATIC_KEY_SUFFIX** | WS URL 尾部固定参数 |
| **kaptchcate** | 浏览器每次弹验证码前必调的预注册接口 |
| **generate_w** | GeeTest 滑动验证码的轨迹加密参数 |
| **validateGeeCheckV2** | 验证码校验接口(返回 user_ip 用于 X-API-FINGER) |
| **jti 单消费** | game token 的 jti 只能被一条连接消费；建连成功后标记已消费，下条连接强制刷新 |
| **X-API-XXX** | API 请求签名头(wasm 动态签名或签名表) |
| **uuidToBase64** | 加密的签名表(AES-CBC)，解密得 {path: signature} |
| **X-API-FINGER** | 基于 user_ip 的指纹头 |

## 采集机制

| 术语 | 含义 |
|---|---|
| **静默判死** | 收帧静默窗口(120s)判定连接死亡并重建；空闲分片豁免(expect_traffic) |
| **expect_traffic** | 连接是否预期有下行流量；空闲分片(0 桌)为 False 豁免看门狗 |
| **分片(shard)** | 多账号采集时每账号一条 WS 连接，多连接组成 TableMonitor 的分片集合 |
| **踢出重进** | 观察号被 5 局不下注踢出(102)后自动重进 |
| **按代理出口分组限速** | 同出口 WS 建连串行 + 间隔(18s)，防 WAF 静默 |
| **MultiplaySession** | 多台模式(301 订阅)全平台桌台实时数据流；按数据帧判活 |
| **路纸(road)** | 百家乐开奖结果序列(B=庄/P=闲/T=和)，road_flat 是合并后的字符串 |
| **下注解锁** | 待验证假设：真实下注行为解锁平台数据通道 |

## 登录体系

| 术语 | 含义 |
|---|---|
| **L0-L4 降级链** | 缓存 → API 刷新 → 浏览器 headless → 纯 HTTP 验证码 → 人工登录 |
| **打码平台** | jfbym / geepass，付费识别 GeeTest 验证码坐标 |
| **GameBrowserLogin** | Playwright 浏览器自动化登录(持久化 profile) |
| **域名轮换** | 平台域名小时级轮换，需动态解析 + 缓存 TTL + 探活 |
| **ApiDeadError** | API 返回非 JSON(域名失效)的穿透异常，触发域名自愈 |

## 项目内部

| 术语 | 含义 |
|---|---|
| **htools** | 底层公共库(日志 loguru 封装、DataSource 接口)，HData 依赖 |
| **HSys** | 分析平台(依赖 hdata)，含 crawl-bot 采集程序 + FastAPI web + PostgreSQL |
| **MarketTick** | 采集输出的统一行情数据结构(htools.types) |
| **streak** | HSys 的长龙分析策略 |
| **device_id / DEVICE_TYPE_PC** | 设备标识与类型(PC=15)，登录/建连参数 |
