# 环境与运行说明

状态：2026-10-30，数据接入、记忆模块及图检索已有离线入口；完整 Agent、训练及正式评测入口尚未实现。

验证环境为 macOS、Python 3.14.7。当前代码只使用 Python 标准库，无需安装 DelTA 原仓库的推理依赖。M2/M3 代码语法要求 Python 3.10+；M1 数据适配器独立使用 Python 3.9+。运行合并后的全部检查建议使用 Python 3.10+，其他版本的实测范围以各模块记录为准。

在仓库根目录运行：

```bash
PYTHONPATH=src python3 scripts/demo_memory.py
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

演示数据库默认保存到 `artifacts/memory-demo.sqlite`，该目录已有 Git 忽略规则。重复运行创建新的示例项目和 run，不覆盖已有记录。数据库包含完整源译文和模型提示词，作为本地实验产物管理，不提交私有数据。

配置见 [configs/memory.json](../configs/memory.json)，模块可通过 `MemoryConfig.from_file()` 加载。演示脚本为快速展示合并过程，使用显式小窗口：STM=2、LTM top-k=1、summary_step=2；正式配置仍为 3、2、20。

默认词法检索无需模型；术语提取和摘要生成需注入 `ChatModel`。演示使用标记为 offline-demo 的模拟后端。真实后端尚未配置，接入签名、按任务路由和记录用量的要求见 [MEMORY.md](MEMORY.md)。

当前没有统一安装包、真实翻译工作流、训练或指标运行入口；不要把上述演示当作全系统启动命令。GPU、远程模型客户端、训练与 sCOMET/dCOMET 依赖在相应模块实现时分别补充。


图模块同样只依赖 Python 标准库，新增可运行入口：

```bash
PYTHONPATH=src python3 scripts/demo_graph.py
PYTHONPATH=src python3 scripts/import_graph_csv.py --help
```

配置、CSV 实际导入参数及运行约束见 [GRAPH.md](GRAPH.md) 与 [CSV_IMPORT.md](CSV_IMPORT.md)。全量测试仍使用上面的 unittest 命令；原 M2/M3 阶段记录为 15 个测试通过，合并后的结果见下方验证记录。没有启动真实模型服务。


## 2026-10-29：无序游戏模式

新增轻量实体图与五类记忆组织方式；`start_run(..., mode="game_resources")` 启用项目范围，默认文档模式保持原行为。DEFINES 反查、候选链接、主题 STM/LTM 和双语概览的实际接口及限制见 [GAME_MEMORY.md](GAME_MEMORY.md)。

## 已验证的数据入口（2026-10-30）

CSV 和 Flare 适配器使用 Python 3.9+ 标准库，不需要安装依赖或配置模型凭据。在仓库根目录设置 `PYTHONPATH=src` 即可运行 [README 的数据命令](../README.md#9-项目文档)。通用 CSV 示例已经执行，输出 5 条源侧、4 条参考、1 条缺译。

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

CSV 的 5 项检查始终离线运行。Flare 的 6 项集成检查只在 `artifacts/` 对应固定快照存在时运行，否则显示 skipped；不会在测试时下载语料。GitHub Actions 使用 Python 3.11 执行相同命令。CSV 导入目标需新建或为空；路径、UTF-8 编码、上下文模式和输入列的错误会明确报告。

Flare 下载是独立的可选联网步骤，默认保存到被忽略的 `artifacts/flare_full_v0.2/`；不提交下载内容和 `outputs/`。目前仍无完整 Agent、训练或评测启动入口。

处理后的 Flare 快照现已随仓库放在 [data/flare/v0.2](../data/flare/v0.2/README.md)，可直接读取或导入其 CSV，不需要联网。发布数据一致性检查始终离线运行，只有重新提取资源的 6 项集成检查需要可选原始快照。
## 合并验证（2026-10-30）

在本地执行 `PYTHONPATH=src python3 -m unittest discover -s tests -v`：发现 30 项，24 项通过，6 项 Flare 集成检查因未下载可选快照而跳过。未运行全量数据下载或真实模型。


自动建图与最小游戏翻译入口见 [AUTO_GRAPH.md](AUTO_GRAPH.md)，需注入提取/翻译与审查模型适配器。命令帮助及离线模拟链路已验证，未执行真实模型全量运行。
