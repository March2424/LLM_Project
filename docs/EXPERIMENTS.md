# 实验登记与结果索引

状态：已登记 Flare 小样本真实翻译流程验证；尚无正式质量对照或训练结果。

**作用**：回答“谁用哪版代码、数据、模型和配置运行了什么，产物在哪里，结论是否经过验证”。

后续每次记录：实验 ID、执行者、日期、Issue、Git 提交与工作区差异、数据/模型/策略/提示词版本、配置路径、种子、设备、预算、产物路径、主指标、完整率、成本、异常与解释。

区分计划、运行中、完成、失败和作废结果。大规模逐条输出保存在实验产物目录，本页只索引；不填未经运行的示例分数，不覆盖失败实验以保留最好结果。评分口径引用 [EVALUATION.md](EVALUATION.md)。

## flare-translation-pilot-2026-11-01

- 执行：Codex，应用户明确请求；2026-11-01，main / a521ecc 基础上有未提交工作区改动，未提交或推送。提示词、输入与配置 SHA256 记录在各阶段 config.json；回归额外记录代码 SHA256。不是干净提交上的正式复现实验。
- 数据：Flare 提取图 graph 快照；24 条不同源文、其中 8 条单独回归。源清单与目的见 [首轮样本](../examples/flare_translation_pilot.json)、[回归样本](../examples/flare_translation_followup.json)。不读取参考，未构造 train/dev/test 划分。
- 模型与策略：DeepSeek API deepseek-flash，非思考、temperature=0；同后端翻译/审校/记忆审查。固定随机种子 20261101，每条随机选五项软证据，硬项另计，1024 软 token；无 4B 推理。STM=3/LTM=2，试跑 summary_step=2，正式默认不改。
- 配置：[首轮](../configs/flare.translation_pilot.json)、[回归](../configs/flare.translation_followup.json)。设备为本机 CPU/SQLite＋远程 API，无 GPU 训练。合计上限 1 USD，回归金额上限扣除首轮已用金额。
- 完成：24＋8 次尝试均生成真实译文，所有状态保留。首轮 11 正常完成/6 检查拒绝/7 译文提交后记忆更新失败；回归 6 正常/2 检查拒绝/0 记忆解析失败。
- 成本：107 API 调用，返回 usage 共 59,101 token；峰时配置费率保守折算 0.0208416 USD，非实际账单；无未知 usage 预留或付费自动重试。
- 验证：32 次输入核对、随机五项、1024 预算和占位符检查通过；两个运行隔离检查通过；五类记忆可重读，术语/译例实际进入过后续翻译输入。相关 21 项离线记忆/工作流测试通过，HTML 记录数与 JavaScript 语法检查通过。
- 异常：首轮 fenced JSON 导致术语解析失败，已支持单层完整 JSON 围栏；审校曾凭空假定术语表，已改提示词。Guill 英文保留被批准成硬词条后阻止音译、Grisbon/The Warden 一致性和审校过严待人工看。角色/摘要本轮未被选入翻译输入，角色笔记还可能被图资料挤出池。
- 产物：本地忽略目录 `outputs/flare_translation_pilot_2026-11-01/`。index.html/ACCEPTANCE.md 是人工入口，两阶段各有 review.html、cases.json、calls.jsonl、run.sqlite；task_summary.json 为合计结果。
- 结论：小样本真实流程已执行，结构/写回/隔离有证据；人工核验、正式指标、翻译收益与 SFT/DPO 均待完成。不能把自动通过当人工通过或独立 Judge 质量结论。复现与边界见 [TRANSLATION_PILOT.md](TRANSLATION_PILOT.md)。

## flare-memory-trial-2026-11-01

- 目的/授权：用户明确暂时不需要 Reviewer，观察记忆生成、持久化与后续读取。独立试跑库，不改原提取库/旧试跑，未输入参考译文。
- 数据/配置：[20 条相关样本](../examples/flare_memory_trial.json)、[memory_trial 配置](../configs/flare.memory_trial.json)。技能/人物名称先于说明和台词，seed=20261101，随机五项、1024 软 token，summary_step=2。require_reviewer=false，无 4B/训练。
- 后端：deepseek-flash，temperature=0、关闭思考；源图、输入、prompt/tokenizer/code SHA256 保留在 config.json；本地未提交工作区，无新提交/推送。
- 完成：20/20 记录成功，无单条记忆更新失败；当前可读译例 20、软术语条目 45、摘要 2、角色笔记 3。STM/LTM 共用译例。所有记录 verification=unreviewed；check.passed=false，没有伪造 Reviewer 通过。
- 持久化：独立只读连接检查 70 个返回引用全部落库，均带未审标记。逐条 memory_before、结束后重读与另一运行隔离已保存。35 项相关离线测试通过；20 条实际输入/随机五项/预算核对与 HTML 行数/JS 语法检查通过。
- 使用：实际输入条目为原文 81、关系 9、译例 9、术语 1；9/20 条翻译使用过记忆。摘要/角色在结束后候选池可见，本次随机动作未消费，不声称全部类别有翻译收益。
- 费用：38 API 调用（翻译 20、术语提取 11、摘要两侧各 2、角色 3），usage 16,305 token，按配置峰时费率保守估算 0.0059517 USD，小于 0.5 USD 上限；不是服务账单。
- 异常：生成/写回已经全部完成后，最终落库核对遇列表/元组拼接错误。修正脚本并只重做本地后处理，追加费用为 0；postprocess_notes.json 保留原因，原调用/译文未修改。
- 产物：`outputs/flare_memory_trial_2026-11-01/` 的 MEMORY_REPORT.md、review.html、memory_inventory.json、persistence.json、cases.json、calls.jsonl、run.sqlite、summary.json。
- 局限：未审试跑只证明生成、存储和读取链路；软提取含一般短语、英文原样和占位符，不等于 45 个高价值术语。语义质量、译名一致性和 DPO 数据有效性仍待后续工作。
