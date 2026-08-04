# HData 文档索引

> 更新时间：2026-08-05。本文是 docs/ 的入口：新接手者从这里选文档。
> 配套：`AGENTS.md`(开发约定)、`PROJECT.md`(跨仓库总览)。

## 快速导航

| 你想做什么 | 读什么 |
|---|---|
| 接手这个包 / 理解结构 | `ARCHITECTURE.md`(分层)+ `GLOSSARY.md`(术语) |
| 遇到事故 / 想删补丁 | `INCIDENTS.md`(踩坑记录) |
| 理解加解密 / 协议帧 | `protocol/加解密逻辑全景.md` → `protocol/leyu-protocol-complete.md` |
| 验证码失效排查 | `protocol/极验4验证码破解与排查.md` |
| 接入新平台 | `protocol/平台接入机制.md` |
| 对外调用方 | `delivery/对外接口文档.md` |
| 打包 pyd/so | `delivery/打包说明.md` |

## 目录结构

```
docs/
├── README.md              本索引
├── ARCHITECTURE.md        架构分层 + 数据流 + 依赖约束 + 公共 API(维护必读)
├── GLOSSARY.md            术语表(协议号/机制/内部词)
├── INCIDENTS.md           事故知识库(现象/根因/补丁/失效条件)
├── protocol/              逆向协议研究(与代码强相关,维护参考)
│   ├── 平台接入机制.md        平台无关设计 + 接入新平台指南【核心】
│   ├── 加解密逻辑全景.md      四层加解密 + 代码位置全解【核心】
│   ├── 极验4验证码破解与排查.md 验证码破解链路 + 失效排查【核心】
│   ├── 客户端行为复刻规范.md   一比一复刻官方客户端网络行为基准
│   ├── leyu-protocol-complete.md  协议报文完整目录(harvester 源)
│   ├── auth-research.md     认证与 Token 获取研究
│   ├── captcha-flow.md      验证码验证流程拆解
│   ├── login-api-capture-20260717.md  登录接口抓包实测
│   └── schema.sql           采集库表结构(SQLite)
├── delivery/              对外交付文档
│   ├── 对外接口文档.md       面向外部调用方的 API 契约
│   └── 打包说明.md           pyd/so 编译打包
└── superpowers/           SDD 计划/规格(实施记录,非阅读文档)
```

## 变更历史

- **2026-08-05**: 重组 docs。协议研究归入 `protocol/`,对外文档归入 `delivery/`;
  平台/数据统计分析报告(断龙/连胜/边界试探等 18 篇 + 2 CSV)迁至 `HSys/analysis/`;
  删除被取代的过时过程记录(captcha-research / gct4-analysis / robustness-analysis)。
  新增本索引。
