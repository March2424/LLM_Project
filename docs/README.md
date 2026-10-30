# 协作文档入口

此目录用于保存团队共同维护的开发与实验约定。数据接入、记忆、图检索及相关接口和环境说明已有实际内容；其他页面按任务逐步填写，标记“占位”的内容不代表协议已确定或实验已完成。

| 文档 | 作用 | 建议维护责任线 |
| --- | --- | --- |
| [GAME_MEMORY.md](GAME_MEMORY.md) | 无序游戏模式的已实现接口与离线演示 | A/B/C |
| [MODULAR_GAME_MEMORY_PLAN.md](MODULAR_GAME_MEMORY_PLAN.md) | 无序游戏文本的轻量实体图＋五类记忆设计（核心已实施） | A/B/C |
| [GRAPH.md](GRAPH.md) | 图类型、检索、M2 联动与清单 | A/B/C |
| [CSV_IMPORT.md](CSV_IMPORT.md) | 解包 CSV 与结构缺失处理 | A |
| [MEMORY.md](MEMORY.md) | 已实现 M2 的来源、架构、接口用法、差异与验证 | A，C/B/D 共同集成 |
| [TASKS.md](TASKS.md) | 里程碑、任务分工、依赖与验收 | 全组，D 汇总 |
| [INTERFACES.md](INTERFACES.md) | 公共数据结构、模块输入输出和兼容性 | 接口提供方与调用方共同维护 |
| [DATA.md](DATA.md) | 数据获取、划分、对齐、来源与可见范围 | A |
| [LOONG_RL_PLAN.md](LOONG_RL_PLAN.md) | Loong 论文分析与四卡48GB/32小时训练建议（未执行） | B/C/D |
| [TRAINING.md](TRAINING.md) | 上下文选择器采样、偏好、SFT/DPO 协议 | B |
| [EVALUATION.md](EVALUATION.md) | 指标定义、Judge、基线与统一评测口径 | D |
| [EXPERIMENTS.md](EXPERIMENTS.md) | 已执行实验的配置、产物、结果和失败记录 | 实验执行者，D 汇总 |
| [SETUP.md](SETUP.md) | 经验证的环境、依赖、模型服务和运行方式 | C 牵头，各模块补充 |
| [DECISIONS.md](DECISIONS.md) | 已确定的重要设计取舍及影响 | 决策涉及成员 |
| [HANDOFF.md](HANDOFF.md) | 跨成员、跨 Codex 会话的工作交接 | 当前任务执行者 |

根目录 [README](../README.md) 说明项目目标与架构，[AGENTS.md](../AGENTS.md) 指导编码助手，[CONTRIBUTING.md](../CONTRIBUTING.md) 预留人工协作流程。已有 [Research](../Research) 文档保留方案细节与调研来源。

填写顺序建议：先确认任务和公共接口，再冻结数据与训练/评测口径；环境配置、实验结果和交接信息随实际工作补充。正文只在一处维护，其他页面通过链接引用。

## 已补充的任务调研

- [文件解析需求](File%20Parsing%20Requirements.md)：原始任务说明与调研进展。
- [《极乐迪斯科》数据获取与统一格式草案](DISCO_ELYSIUM_DATA.md)：在线数据库、自行提取路径、双语关联键、图结构及实验输入建议；尚未实际解包或冻结数据协议。
- [可下载的中英游戏文本：来源与覆盖核查](GAME_DATA_SOURCES.md)：实际下载核对 Flare、CDDA 和 PoE 社区汉化，统计各类覆盖及缺口；尚未评审译文质量或冻结正式实验数据。
- [Flare 统一样例 v0.1](DATA.md#flare-统一样例-v01)：已实现的 47 条跨类型样例、输出文件和实际验证；字段定义见接口页。
- [Flare 全量本地化文本 v0.2](DATA.md#flare-全量本地化文本-v02)：简化四列查看格式、完整资源覆盖、3,800 条中英对照与 616 条缺失、后台来源及复现入口。
- [通用 CSV 导入](DATA.md#通用-csv-导入)：四列中文/英文表头、原文与参考分离、命令行/Python 接口、离线样例与检查。
