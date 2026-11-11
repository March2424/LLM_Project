# LangGraph 本机试跑与审阅记录

日期：2026-11-02，Asia/Hong_Kong。此页为 M5 小样本流程验证，不是翻译质量对照、独立人工验收或 RL 结果。

## 环境与输入

- 基础提交：`b80de11`，本地分支 `feat/langgraph-review-loop`；未提交、推送或合并。
- Python 3.12.13，独立 `.venv`；LangGraph 1.2.19、SQLite checkpoint 3.1.3、tokenizers 0.22.1。
- 真实模型：本机 Ollama `llama3.2:1b`，temperature=0；既有本地权重，无新模型下载，无付费 API。
- 输入：[examples/workflow_flare.csv](../examples/workflow_flare.csv)，从仓库 Flare 四列源侧数据摘取三条，保留原 ID、原文、类型和原生条件。没有读取独立中文参考或旧中文查看表。
- 初始实体边为空，使用源侧词法回退和运行内记忆；没有运行三款游戏的完整实体提取。
- 软预算继续采用固定 Qwen tokenizer 1024 token，最多 5 项。它是代理预算计数，不是 Llama 的精确计费 tokenizer。

## 审阅与最终状态

| 原文 | 最终译文 | 状态 |
| --- | --- | --- |
| Cleave | 顺劈斩 | committed |
| Attack all enemies in a wide arc. | 以大范围弧形攻击所有敌人。 | committed_with_warnings |
| Buy Price: %d %s | 购买价格：%d %s | committed_with_warnings |

全部审阅由本次 Codex 根据源文、文本用途、原生条件及实际上下文执行，身份保存为 **ai:Codex**。本次三条最终译文均由 Codex 的外部直接修订产生，`model_call=None`、`generation_origin=external_edit`；修订后作为新草稿再次明确确认，未冒充本地模型输出或人工通过。

1. **Cleave**：模型把提示词与元数据混入译名；Codex 拒绝并给出具体修复意见。模型修复后仍不合格，保留失败版本。Codex 提交“顺劈斩”作为新草稿，再次检查无额外效果、数值或元数据后确认。译名是本次运行的软建议，不是官方标准或项目强制术语。
2. **技能说明**：模型返回英文及元数据，并改变了复制的上下文译法，拒绝。Codex 修订为表中译文，保留攻击、所有敌人和宽阔弧形范围的含义，未额外补充数值。实际上下文含前条已提交译例，证明运行内记忆进入后续输入；不代表模型成功利用了它。
3. **购买价格**：模型混入英文说明，拒绝。Codex 修订 UI 文字，核对 `%d`、`%s` 原样保留且顺序一致后确认。

后两条译文提交后，模型术语提取未返回合法 JSON 数组。独立记忆更新失败以提示保留，不撤销译例或重复翻译。只有名称条目产生一个软术语建议；本轮没有明确主题／人物身份，不生成角色笔记或主题摘要。

## 实际运行结果

- 6 次真实本地模型调用：3 次翻译、1 次模型修复、2 次术语提取；调用记录合计 1,932 个 Ollama 返回的输入／输出 token。
- 3 条 AI 审阅后译例、1 条软术语已持久化。独立 SQLite 连接核对记录和 `ai:Codex` 身份。
- 每次审阅命令在独立 Python 进程中运行，证明退出后能从 SQLite checkpoint 恢复，不重做已有草稿。
- 原始失败草稿、修复意见、直接修订、再次确认、实际上下文和记忆更新警告全部保留。
- 现有全量提取 SQLite 与历史 24+8 条试跑产物不在 Git 中，本次没有读取或重评这些旧结果。

报告：[report.html](../outputs/langgraph-demo/report.html)、[report.json](../outputs/langgraph-demo/report.json)、[translations.csv](../outputs/langgraph-demo/translations.csv)。这些含运行数据的本地输出被 Git 忽略。

## 已执行验证

新增 7 项 LangGraph 离线检查全部通过；M2 记忆 11 项、自动建图集成 9 项、游戏记忆 4 项、轻量检索 5 项通过，合计 **36 项**。其中模型回复为显式模拟，仅用于机制验证。

另验证 editable 安装、依赖一致性、tokenizer 固定哈希、CLI 初始化／状态／恢复／审阅、Python 编译和 Git 空白检查。未为此任务安装其他游戏静态解析和正式指标依赖，也未声称运行整仓所有测试。

## 后续接入

1. 配置更适合中文游戏翻译的模型后，重新进行真实生成与审阅；本机 1B 模型此次全部初稿不合格。
2. 组员提供图数据库和快照后，通过 `--source-db` 使用副本接入实体图。
3. 自动 Reviewer 和 RL 策略准备好后，替换外部审阅／固定选择接口，保留当前编排与版本绑定。
