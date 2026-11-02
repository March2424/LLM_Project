# 轻量存储、名称匹配与选择器输入

更新：2026-11-01。已实现代码、离线契约测试及真实源库诊断；随后完成 [Flare 小样本真实翻译闭环](TRANSLATION_PILOT.md)，采用随机五项而非 4B 选择器。人工核验待完成，尚未进行 4B 推理、训练或质量收益对照。

## 1. 固定本轮方案

每款游戏继续使用已有 SQLite：源记录、实体/关系、图快照、批准记忆和各运行的译例共用版本管理。运行时从数据库建立内存索引，后续查询复用；不新增 Neo4j、向量库或文件存储协议。JSONL/CSV 继续用于导出、查看和复现。原始提取数据库不因本轮诊断被改写。

不做别名表、人工身份映射或全图实体合并。唯一的名称处理是 `casefold()` 和合并连续空白，再按“类型＋处理后的完整名称”查找；正文检索使用词边界。同一键对应多个节点时汇总证据，按 occurrence ID＋源版本去重，合并命中路径。文本相同但 ID 不同的出现位置仍分别保留。

例如 `Ember Ward` 与 `ember  ward` 可以共同提供检索候选，`Ember Shield` 不会自动成为它的别名。`Guill` 与 `Guill, Scribe of the Order` 也不因名称近似建立身份映射；现有图边和词法检索仍可能分别命中它们。名称匹配不证明同一实体，原有节点、版本和关系不变。

## 2. 召回与预算

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| max_entity_seeds | 3 | 最多三个名称入口组；每组可命中多个独立节点 |
| max_entity_hops | 1 | 实体间最多一跳，可正向/反向查找；展示保留原关系方向 |
| max_neighbors | 6 | 每节点各类邻接查找的上限；定义优先，描述优先于裸名称 |
| max_hops / max_nodes | 4 / 40 | 单次图探索总深度和节点上限 |
| max_candidates | 16 | 软候选池上限；硬项另计 |
| max_selected | 8 | 当前选择器输出0–8条软候选；硬项另计；旧运行保留其冻结参数 |
| max_soft_tokens | 1024 | 选中软上下文序列化后的预算，包含短 ID、记录引用、类别和正文 |
| max_characters | 6000 | 全部上下文包的附加字符上限，硬项超限返回 budget_conflict |

流程是当前 occurrence → 直接图边/完整名称 → 同名节点的定义、提及及一跳关系 → 词法补充 → 五类记忆联查。词法使用源词倒排索引和 IDF 加权重叠，不引入 embedding；图探索和词法候选共用总节点预算。无实体时仍能执行词法回退；返回空候选也合法。实体名称只负责定位，不单独占一个软候选位。

候选池优先保留定义、关系及图关联原文，再补词法结果；源资料、术语/角色、STM/LTM、摘要四组轮流装填；用户明确允许后两种合组。每项仍分别标注memory_kind；合组内某细类可被截断，不保证五类各占一位。最终选择无类别配额。排序是固定启发式基线，尚未证明是最优策略。截断写入 `pool.unresolved`。

**翻译器为闭源LLM，当前学生选择器为Qwen3-8B。暂不扩大1024。** 这不是翻译器或选择器的总窗口：当前原文、硬术语、系统指令、选择器看到的完整 16 项观察及输出预留另计。短定义通常能容纳多条；真实收益应由后续翻译闭环验证。长候选整项跳过、不截断证据。日志记录未选 ID、单项 token、超大候选和期望证据是否被最终选入，便于区分池缺失、排序/八项限制和 token 限制。

`LocalTokenCounter` 从调用方提供的 `tokenizer.json` 计算文本 token，固定文件 SHA256 并关闭内置截断/填充。1024 对这个固定预算 tokenizer 严格执行。闭源服务如提供适配的 tokenizer 应优先使用；不可取得时声明采用哪个代理 tokenizer，固定对照口径，不能称为该服务的精确计费 token。实际 API 成本仍根据服务 usage 记录；例如 [DeepSeek 官方说明](https://api-docs.deepseek.com/quick_start/token_usage/) 也区分离线计算与 API 返回用量。当前诊断使用 Qwen2.5 的公开 tokenizer，只下载分词文件，无模型权重；不代表实际翻译器或 4B 选择器已确定为该模型。真实闭环前需确认服务/模型、预算 tokenizer 和费用配置。

## 3. 条件与记忆边界

项目、语言方向、快照、源版本、branch/phase 仍检查；已知分支与未知分支也不默认兼容。源侧软参考不再要求整段 `requirements` 完全相等，原生 context 随正文展示，并提示不假定这些条件在当前记录成立。技能两个消耗版本可以作为含义参考，不能合成一个无条件效果事实。

批准术语、风格、摘要和已验证译例继续按原有严格作用域读取；软参考的放宽不改变批准规则的适用性。摘要写回只接收满足原有条件检查的来源。源图中的 program_only/inferred 联系只是参考，不自动成为批准术语、永久人物设定或 TM。翻译与审校 prompt 已加上这个边界。

| 记忆 | 冷启动与更新 |
| --- | --- |
| 术语 | 项目预置规则默认硬项；本运行自动名称为 mandatory=false 的软建议 |
| 角色 | 仅在明确 speaker_id 下读写批准笔记；同名扩展不共享人物身份 |
| 摘要 | 仍需本运行已验证译例；按明确 topic_id 更新，无译例时为空 |
| STM | 本运行最近相关已验证译例，默认 3 条；不是 CSV 前三行 |
| LTM | 本运行更广范围相关译例，默认 2 条，与 STM 去重 |

speaker/topic 在源数据未提供明确值时，才从当前直接、适用、有效的唯一链接派生；已有明确身份不被图派生值覆盖，多对象歧义时不按 ID 任取一个。多对象的源证据仍可召回。跨节点角色/摘要可能分散，这是暂不做身份映射的代价；先依赖相关 occurrence 和词法读取译例，不凭同名把条件性设定合并。

## 4. 选择器协议与实际输入

`GraphRetriever.selector_observation(pool)` 给出当前原文、派生元数据、pool_hash、预算、固定硬上下文以及 `c01…c16` 的软候选列表。每项为可读正文并附单项 token；单项用量相加不等于组合用量，最终以组合序列化后计数。

完整观察也保留原生 context/text_type、已知条件和语言方向，不只给派生的 speaker/topic。[选择器提示模板](../prompts/selection/context_selector.txt) 输出简短自然语言 `rationale` 和 `selected_ids`；理由与动作一起参与训练，执行器只使用 ID，详见 [训练协议](TRAINING.md)。当前源记录的 ID/版本与元数据单独送翻译/审校，并绑定在 manifest.current_source_context；不挤占 1024 软证据预算。

`select_context(pool, ('c02','c05'))` 只接受该池短 ID，按池顺序规范化，固定保留全部硬项。空动作合法；重复、池外、超过八项或超预算的动作直接报错，不能偷偷裁掉动作中的一项。训练/采样方必须把动作和 observation 的 pool_hash、ReadView、图快照绑定。当前已实现Teacher采样、冷启动记忆更新与SFT候选导出；开源学生自采样、DPO评分/配对及在线选择器服务尚未实现。

`ContextPack.model_context` 是实际给翻译器和审校器的同一份正文，包含短 ID 与原始记录引用/版本；manifest 保留完整来源、关系路径、记忆版本、未选 ID、预算与 tokenizer 标识，并与正文共同生成 context_hash。`tokens` 为全上下文计数，`soft_tokens` 为软部分计数；它们都不含系统提示词、当前原文和输出。提交前重建输入并比对，过期验证不能复用。

当前语义检查统一交 Agent Reviewer，本地只保存译文草稿和格式提示；Reviewer 通过后写正式 TM，并独立程序准入各类软记忆，不再逐项调用 LLM 审查。名称类型记录直接保存整条源译对，自动术语不产生硬冲突，详见 [简化记忆流程](REVIEWER_MEMORY_FLOW.md)。

## 5. 真实源库诊断与复现

安装轻量依赖，不需要 torch：

```bash
python3 -m pip install --target artifacts/retrieval_deps -r configs/requirements-retrieval.txt
```

本次本地 tokenizer 路径为 `artifacts/retrieval_tokenizer/tokenizer.json`；该忽略目录不随 Git 分发，来源固定在同目录 manifest：Qwen/Qwen2.5-7B-Instruct，revision `a09a35458c702b33eeacc393d103063234e8bc28`，SHA256 `c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539`。接入实际模型时使用相应的已冻结预算文件，不能沿用名称暗示其适配另一模型。

```bash
PYTHONPATH=artifacts/retrieval_deps:src python3 scripts/validate_game_retrieval.py \
  --db outputs/flare_full_2026-10-31/graph.sqlite --project flare \
  --tokenizer artifacts/retrieval_tokenizer/tokenizer.json \
  --cases examples/flare_retrieval_cases.json --sample 40 \
  --output outputs/retrieval_validation_new/flare
```

脚本只读打开原库，备份到内存后创建诊断运行；不读取独立参考，不调用模型，不向正式库写译文。输出 summary.json、完整 cases.jsonl 和 REPORT.md。期望证据来自少量人工检查的原文案例，不拿现有图自动生成的关系充当语义金标准；其余均匀抽样只做结构、预算和性能检查。它不输出翻译精度、收益或训练结果。当前结果与剩余任务见 [PRE_RL_ALIGNMENT.md](PRE_RL_ALIGNMENT.md)。


2026-11-02：当前Teacher/SFT协议上限统一为8项，保留1024软token；上文历史随机五项试跑的产物与结果不重写。Teacher不生成最终译文，外部翻译只用于记忆推进，见 [TEACHER_TRAJECTORIES.md](TEACHER_TRAJECTORIES.md)。
