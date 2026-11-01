# Flare 真实翻译闭环与人工核验

日期：2026-11-01。用户要求挑选一个游戏的一组文本，运行真实翻译闭环，并明确用随机五项替代尚未接入的选择器。

后续接口已按用户要求简化：当前 translate_one 保存待 Agent Reviewer 草稿，complete_review 接收其一次回执再提交，不再本地三重审查；自动名称变为软建议。见 [当前流程](REVIEWER_MEMORY_FLOW.md)。下文 24＋8 条是真实历史运行记录，保留当时的拒绝/失败状态；本轮没有用新规则重新调用 API 或改写这些产物。

## 固定设置

- Flare 24 条源侧文本，顺序和人工关注点见 [样本清单](../examples/flare_translation_pilot.json)：技能名称/描述、Guill 台词、人物/物品名、多人物任务、UI 与 `${…}`、printf 占位符。先选有联系的记录以观察记忆写回，不按 CSV 相邻关系推断剧情。
- 固定 DeepSeek API `deepseek-flash`，非思考、temperature=0；响应模型名写入结果，不声称服务别名永久固定版本。译文审校和记忆审查使用同一后端，不能充当独立 Judge。
- 从最多 16 项软池随机选最多五项，固定 seed=20261101，并按 occurrence ID 派生各条随机顺序。五项以外的硬术语始终保留。遇到预算不合的候选整项跳过并记录，不悄悄修改正文。
- 保留 1024 软 token；沿用已冻结公开预算 tokenizer，API usage 单独记录。候选、选择、当前原生 context、实际模型输入、模型响应、源/图/记忆版本均保留。
- STM=3、LTM=2；**本轮摘要间隔设为 2**，用于短样本观察更新，正式默认 20 未修改。没有合适 speaker/topic 或未达到条件时，相应记忆为空。
- 本轮独立上限 1 USD、180 调用、700,000 token；配置峰时缓存未命中输入 0.3 USD/M、输出 1.2 USD/M 为保守费率，不等于真实账单。[官方价格](https://api-docs.deepseek.com/quick_start/pricing/) 于 2026-11-01 核对。无自动 API 重试或自动修复失败译文。

配置见 [flare.translation_pilot.json](../configs/flare.translation_pilot.json)。1 USD 是本次小样本的执行上限，不是新增全量翻译授权。

## 执行方式

```bash
PYTHONPATH=artifacts/retrieval_deps:src python3 scripts/run_translation_pilot.py \
  --prepare --output outputs/flare_translation_pilot_new

PYTHONPATH=artifacts/retrieval_deps:src python3 scripts/run_translation_pilot.py \
  --output outputs/flare_translation_pilot_new
```

prepare 只读复制已有图数据库，建立独立试跑库与空知识快照，冻结样本、配置、源库/tokenizer/prompt SHA256；不发请求。运行阶段从已忽略的 `.env.extraction` 或环境变量读已有凭据，输出不写密钥。每条保存检查点、预算预留与状态；服务失败不会静默变成空译文。失败行继续保留，恢复不会自动重新付费翻译已完成行；提交后缺少行检查点时会停止并要求核查，避免重放已提交内容。

`GameSession.translate_one(..., selected_ids=...)` 可执行给定候选 ID；不传则保留原有固定基线。记忆审查输入只保留知识正文、scope、证据和相关已验证源译对，不把层层嵌套的旧审计 manifest 重新发给模型；完整审计仍存数据库。占位符检查包含 `${…}` 的美元符号和 printf `%%`。

## 如何人工核验

实际产物目录：`outputs/flare_translation_pilot_2026-11-01/`。

| 文件 | 用途 |
| --- | --- |
| review.html | 原文/译文并排、原生 context、实际选中证据、未选候选、自动审校及记忆写回；可填写通过/需修改/不通过、建议译文与备注，导出人工核验 JSON |
| REPORT.md / summary.json | 实际完成率、失败、费用、各类记忆写回/读取、输入一致性和隔离结果 |
| cases.json | 每条完整候选池、实际选择、ContextPack、模型调用元数据与结果 |
| calls.jsonl | 本次全部原始模型 prompt 和响应；不包含认证头 |
| run.sqlite | 独立试跑库，保留已验证译例、批准/草稿记忆及失败调用 |
| memory_probes.json / isolation.json | 运行结束后的记忆重读及另一运行隔离检查，无额外模型调用 |
| config.json / inputs.json / budget.json | 冻结配置、实际源输入、累计费用检查点 |

人工重点核对：原意与遗漏、专名/术语一致性、数值/条件/占位符、角色语气、证据是否有用或引入错误背景。页面初始全部标记待核验，自动通过不预填成人工通过；浏览器暂存需要导出 JSON 才能作为交付保存。

没有向任何模型、记忆、检索或页面输入参考译文；不将本轮小样本当作训练/测试划分、翻译收益比较、RL 结果或人工验收通过。后续若比较策略，仍需一致候选池、记忆快照、翻译环境及总预算。

实际运行结果以目录中的 REPORT.md/summary.json 为准；下轮可以先处理报告中的具体失败，再决定是否扩大或接入 4B 选择器。

## 本次实际结果

已完成首轮 24 条及其中 8 条独立回归，合计 32 次尝试、107 次真实 API 调用、59,101 usage token，峰时配置费率保守折算 0.0208416 USD，小于合计 1 USD。所有尝试均随机选了五项，最大软上下文 794/1024；两次运行的隔离与实际输入核对通过。

首轮：11 条译文/记忆流程正常完成，6 条未通过检查，7 条译文已提交但术语 JSON 围栏导致记忆更新失败。修复后 8 条回归：6 条正常完成、2 条检查未通过，无记忆解析失败。补充了标准围栏解析，并明确审校不能假定不存在的术语表、不能把正面观察当 defects。原历史失败不改写。

人工统一入口为本地产物 `index.html` 和 `ACCEPTANCE.md`，首轮与 followup 各有 review.html；发现包括 Guill 英文被批准成硬词条后阻止音译、Grisbon 多种音译、The Warden 译名不一致，以及审校可能过严。五类记忆均有后续读取记录，术语/译例实际进入过后续翻译；角色/摘要本轮未被随机策略送入模型，角色笔记还可能在池截断时被图候选挤掉。人工验收与真实质量收益尚未完成。
