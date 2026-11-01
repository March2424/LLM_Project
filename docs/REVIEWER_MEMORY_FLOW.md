# Reviewer 与记忆更新的简化接口

更新：2026-11-01。按用户要求，语义质量检查统一交给后续 Agent Reviewer。本地不再执行 `check_game_translation`、`review_memory`、`review_style` 三类 LLM 审查，不增加重试或拒绝链。

最新补充：用户随后明确要求暂时不需要 Reviewer，直接测试记忆生成和记录。已提供下面的未审试跑模式；默认正式接口仍可保留 Reviewer，当前记忆试跑配置显式关闭。

## 临时跳过 Reviewer

```python
session = GameSession(retriever, require_reviewer=False)
outcome = session.translate_one(occurrence, "trial-run", "graph")
```

该模式生成后立即保存本运行译例并独立更新术语、摘要和角色，供后续文本召回；无 Reviewer 调用、无模拟通过。译例的 check.passed 保持 false，译例及派生记忆 content.verification=unreviewed，状态为 recorded_unreviewed / recorded_unreviewed_with_warnings。来源和输入版本检查保留，格式扫描仅记提示。

底层复用现有 records 状态以支持运行内读取，数据库 status=approved 在此表示程序已记录，不表示语义已审核；查看 verification/check 即可区分。记录带 run_id，既有项目知识快照只收 run IS NULL 的记录，所以不会进入项目公共知识或另一运行。本次使用单独的 SQLite 副本，原提取库、旧试跑和正式运行均不改。

开关不会改变正式路径：require_reviewer 默认 true；正常提交仍需真实 Reviewer 回执。MemoryStore.commit_translation / GraphRetriever.commit 只在显式 allow_unreviewed=true 时允许未审写入，不能把 passed=false 当作正式检查通过。

真实试跑入口与上限见 [flare.memory_trial.json](../configs/flare.memory_trial.json)，样本见 [flare_memory_trial.json](../examples/flare_memory_trial.json)：20 条 Flare，名称优先于相关说明/台词，随机五项、1024 软 token、summary_step=2，本轮上限 0.5 USD。脚本额外导出 memory_inventory.json、memory_before/probes 和独立连接的 persistence.json，实际完成情况以新输出目录的报告为准。

本次已完成：20 条未审译例、45 个软术语条目、2 条摘要、3 条角色笔记，共 70 个当前可读记录；独立只读连接核对全部已保存。38 次 API 调用中没有 Reviewer，usage 16,305 token，保守费率估算 0.0059517 USD。单条生成/更新均无失败。五类记忆可重读，实际随机上下文使用了 9 项译例和 1 项术语；摘要/角色可召回但本轮未被随机选中，不据此声称质量收益。

产物为 `outputs/flare_memory_trial_2026-11-01/`：MEMORY_REPORT.md 展示逐条增长和全部记录，review.html 展示源译文、memory_before 与实际上下文，memory_inventory.json / run.sqlite 为数据，persistence.json 为落库核对。软条目较宽，含普通短语或占位符，数量不代表高价值术语数。汇总阶段有一次列表/元组拼接错误，已仅本地修正并重做落库核对；postprocess_notes.json 保留说明，未重发模型请求。

## 执行流程

生成译文 → 保存草稿和实际上下文 → Agent Reviewer → 写回译例、独立更新软记忆。

`GameSession.translate_one(...)` 只调用翻译器，返回 `review_pending` 和 `draft` 引用。已有占位符/硬术语程序检查只记录在 issues 与草稿 warnings，供 Reviewer 一次性查看，不在此处按措辞、音译或风格偏好否决译文。草稿持久化在已有 records 表中，保留来源、版本与实际输入；尚未通过 Reviewer 时不进入正式 TM。

Agent Reviewer 使用草稿中的 source、target、context_manifest 和 warnings，产生现有 `TranslationCheck`。随后调用：

```python
pending = session.translate_one(occurrence, "run", "graph")
draft = store.get(pending.draft)
# reviewer_check 由 Agent Reviewer 针对 draft 的源版本、译文和 context_hash 产生。
outcome = session.complete_review(pending.draft, reviewer_check)
```

`complete_review` 消费已有结论，不再调用另一个 Reviewer。通过后进入原有版本化提交；建议修改则返回 `needs_revision`，保留草稿。来源/输入版本变化仍要求使用当前上下文，防止错误绑定；这不是新增的语义检查。

完整 Agent Reviewer 编排仍待后续接入，本轮只提供上述接口。不伪造 Reviewer 通过，也不把当前命令行生成草稿写成已完成真实语义验收。现有试跑脚本现在生成待 Reviewer 草稿，历史 24＋8 条结果保持原样，不重新标记通过。

## 自动名称记忆是软参考

- `skill_name`、`character_name`、`item_name` 等名称类记录，Reviewer 通过后直接将整条源译对保存为候选，例如 `Cleave → 顺劈斩`、`Guill → 基尔`，无需再问模型是否属于专有名词。
- 其他文本的术语提取包含人物、技能、物品、地点、任务、组织和常用游戏术语，并提供 text_type。单项不在源译文中时跳过该项，保留其他有效项；额外输出字段不导致整批失败。原始输出仍在 model_calls 中可查。
- 自动名称显式 `mandatory=false`，只作为译法建议；`Guill → Guill` 也不会锁死后续音译。多个自动译法并存不触发硬冲突，不增加别名或实体归一化。
- 项目级预先批准术语默认仍为硬项；已有 run 内自动术语若没有 mandatory 字段，读取时也按软项处理，无需迁移旧库。调用方确有强制规则时可显式设置 mandatory=true。

候选池按源资料、术语/角色、译例、摘要轮流装填，避免软化后的名称/角色全部被图资料挤掉。仍使用 16 项软池、选择最多 5 项、1024 软 token，没有新增数量参数。

## 各类更新独立执行

Reviewer 通过后先保存正式译例，STM/LTM 继续共用这份记录；然后分别更新术语、摘要和角色笔记。某一步解析或服务失败，只记录该类别的提示，继续其他更新，不重新翻译。

摘要仍按主题与新增译例节流，角色仍需要明确人物身份；正常无新增不等于失败。不再对每条衍生记忆额外调用 LLM 批准，保留源译对定位、scope、版本和依赖检查后程序准入，自动派生内容标记 program_only，供后续 Reviewer 使用或修订，不把程序准入解释为独立语义确认。

| 状态 | 含义 |
| --- | --- |
| review_pending | 草稿已生成，等待 Agent Reviewer |
| needs_revision | Reviewer 建议修改，保留当前草稿 |
| committed | Reviewer 通过，译例和相应记忆更新完成 |
| committed_with_warnings | 译例已提交，部分记忆更新有提示；其他类别继续执行 |

人工核验页现在分别显示“译例已写回/草稿待审”和其他记忆更新，避免把没有新术语误读为没有保存译例。

本轮只执行相关离线接口验证，未新增付费调用或改写旧试跑结果。覆盖单次翻译/外部 Reviewer、格式提示不本地拒绝、Cleave/Guill 直接入软记忆，以及术语失败不影响摘要/角色更新。
