# 环境与运行说明

状态：2026-10-28，记忆模块、CSV 导入和图检索离线入口已验证。

验证环境为 macOS、Python 3.14.7。当前代码只使用 Python 标准库，无需安装 DelTA 原仓库的推理依赖。代码语法要求 Python 3.10+，其他版本尚未实测。

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

配置、CSV 实际导入参数及运行约束见 [GRAPH.md](GRAPH.md) 与 [CSV_IMPORT.md](CSV_IMPORT.md)。全量测试仍使用上面的 unittest 命令，现有 15 个测试通过。没有启动真实模型服务。


## 2026-10-29：无序游戏模式

新增轻量实体图与五类记忆组织方式；`start_run(..., mode="game_resources")` 启用项目范围，默认文档模式保持原行为。DEFINES 反查、候选链接、主题 STM/LTM 和双语概览的实际接口及限制见 [GAME_MEMORY.md](GAME_MEMORY.md)。
