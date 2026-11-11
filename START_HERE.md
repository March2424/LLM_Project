# 游戏翻译自主闭环：从这里开始

当前流程已改为 **自动翻译 → 自动审查 → 按需修复／重选资料 → 自动复审 → 保存译文和记忆**。运行中不再要求人工通过、不提供直接手填译文入口。

翻译、修复和审查均调用已配置的 **DeepSeek Flash**；独立调用承担不同职责。密钥仅保存在本机被 Git 忽略的 `.env` 中，不能提交。

## 在 VS Code 中运行

1. 打开整个 `LLM_Project` 文件夹。
2. “运行和调试”中选择 **0 · 一键自主翻译（推荐）**，按 F5。
3. 程序自动使用 `.venv`，创建带时间戳的新目录并处理三个示例。终端显示每条结果，结束后给出报告路径。

也可以直接运行根目录的 `run_workflow.py`；没有菜单和审阅输入。每次一键运行创建新目录，不覆盖旧试跑。

分步运行：先用第 1 个配置初始化，再用第 2 个配置自动处理或恢复。第 3 个配置查看本次自主审查试跑，不调用模型。

## 使用终端

```bash
source .venv/bin/activate
python -m localization_agent.workflow.cli init --directory outputs/my-auto-run
python -m localization_agent.workflow.cli run --directory outputs/my-auto-run
python -m localization_agent.workflow.cli status --directory outputs/my-auto-run
```

`init` 的目录必须不存在。中断后对同一目录再次 `run` 即可继续；已完成、失败或未解决的条目会保留状态，不自动重发整条任务。模型调用需要网络和可用 API 额度，整个运行默认最多 30 次模型调用。

旧人工运行只支持 `status` 查看；不能把旧草稿的人工结论直接用作新策略的自动审查结果。旧 `review`、`interactive` 命令及手填译文参数已移除。

## 自动判断的含义

- **accept**：当前译文合格，且占位符、硬术语等技术检查通过，自动提交。
- **repair**：源文及已有资料足以纠正明确错误，模型根据具体错误修改，再次审查。
- **reselect**：当前资料不足或具有误导性，在已检索候选池中重新选择，生成新草稿并再次审查。
- **stop**：关键资料缺失或冲突，无法给出有依据的修正，记录未解决并处理下一条。

默认最多修复两次、重选一次；重复译文、未变化的资料、无效审查输出或预算耗尽都不会自动当作通过。合理同义词、首次译名和个人风格偏好不会被要求反复修改。

初次资料选择仍采用已有固定策略；后续重选是基于提示的决策，**没有训练或启用 RL 选择器**。重选限于当前快照的候选池，不会联网搜游戏攻略或编造背景。

## 查看结果与设计

- [自主运行报告](outputs/deepseek-autonomous-v2/report.html)
- [完整草稿、决定、上下文及调用记录](outputs/deepseek-autonomous-v2/report.json)
- [译文表格](outputs/deepseek-autonomous-v2/translations.csv)
- [自主判断依据与研究资料](docs/AUTONOMOUS_REVIEW.md)
- [工作流与接口说明](docs/LANGGRAPH_WORKFLOW.md)

模型自动通过属于运行时审查结果，不等于独立人工验收，也不是正式翻译质量对照。UI 实际显示、剧情分支及完整游戏体验仍需要独立 LQA。
