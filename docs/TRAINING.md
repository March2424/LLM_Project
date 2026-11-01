# 上下文选择器训练协议

更新：2026-11-01。状态：设计与提示词已更新，teacher 蒸馏、自采样、轨迹导出和 GPU 训练尚未实现。详细参数及论文分析见 [LOONG_RL_PLAN.md](LOONG_RL_PLAN.md)。

## 当前路线：Teacher 蒸馏 → pi_SFT 自采样 → DPO

用户明确：翻译始终由外部 LLM 完成，训练只覆盖选择器。下面的两阶段路线替代先混合 teacher、固定基线和未训练学生动作，再同时导出 SFT/DPO 的旧方案。

```text
训练侧源文＋冻结候选／记忆
  → 闭源 Teacher：Observe → 自然语言分析 → 选择 ID
  → 外部 Teacher：基于实际选中证据生成合成译文
  → 筛选示范；通用学生 prompt → Teacher 分析＋动作
  → LoRA SFT → dev 选模并冻结 pi_SFT
  → pi_SFT 自行观察候选，采样 2–4 条分析＋动作
  → 同一固定外部 Translator 分别翻译＋独立离线评分
  → 同状态学生动作的 chosen/rejected → 在 pi_SFT 上做 LoRA DPO
```

## 1. Teacher 蒸馏阶段

- 使用 [teacher 选择模板](../prompts/selection/teacher_context_selector.txt)，类别软提示只用于 teacher 输入；没有配额，不要求选齐五类。
- Teacher 用显式自然语言说明当前翻译需求、候选实际内容及取舍，再输出 selected_ids。分析不能仅罗列 ID，不引用池外证据、人工参考或未来历史。
- 验证动作并按原有 Context Builder 生成实际输入，再用 [teacher 译文模板](../prompts/selection/teacher_translation.txt) 独立调用外部模型。两次调用可共用闭源后端，但保留各自 task、模型、prompt 和 context hash；译文调用只使用当前源文、源侧元数据、硬项和实际选中证据，不使用选择分析或未选候选。
- 将生成译文标为 teacher_translation／synthetic，不称人工金标准。其用途是检查示范质量与审计，不是选择器 SFT 输出、不进入学生 prompt，也不直接成为正式评测参考。
- Teacher 模型大或格式通过不等于示范高质量。程序检查动作、来源、格式及硬规则；有合格训练参考时离线评分辅助筛选，并少量抽查分析/证据与译文。当前不接运行时 Reviewer，不自动宣称语义审核通过。
- SFT 数据为通用学生 prompt＋相同冻结 observation → Teacher 的 rationale＋selected_ids，不再要求 teacher 在混合四方案中获胜。筛选、失败与拒绝原因均保留，不能为凑数编造分析。

## 2. pi_SFT 自采样与 DPO

- SFT 完成后在 dev 选定 checkpoint，冻结为 pi_SFT。它同时是首轮自采样策略、DPO 初始化和 reference；需要精确记录权重、adapter、tokenizer 与 chat template。
- 对同一状态使用 [通用选择模板](../prompts/selection/context_selector.txt)，无 teacher 类别指导，自行采样最多 2–4 次完整分析＋动作；按 selected_ids 集合去重。相同 ID、仅分析措辞不同不构造质量 pair；不足两个有效不同动作则记录并跳过，不无限重采。
- 对各动作分别调用同一冻结外部翻译器，独立 scorer 只在允许的训练侧读取人工参考。先过滤非法动作/硬违规，再按下游翻译质量和既定成本规则选择一对；差异不可靠时不强行排序。
- 主 DPO 的 chosen/rejected 都来自同一冻结 pi_SFT 的实际生成，不把 teacher／基线动作混入主偏好对，也不复用 SFT 前的 teacher 偏好数据。Teacher 或基线保留为诊断对照。
- DPO 中完整 rationale＋selected_ids 都参与 log probability；reference 必须是冻结 SFT，不能错误退回原始基座。完成本轮数据采样后再训练，先不做边更新边采样或多轮迭代。
- 同一 train 状态允许同时用于 SFT 和 DPO，但两个阶段的生成与反馈分开保存；dev/test 不参与任一阶段训练。缺合格人工参考时不直接用 teacher 合成译文替代主 DPO 质量信号；另行合成参考实验须单列。

## 3. 分析与动作格式

Teacher、学生、SFT、DPO 和部署统一输出 rationale 在前、selected_ids 在后的 JSON。分析通常 1–3 句，以自然语言展开关键名称/译法、事实条件或表达依据及取舍；ID 仅辅助定位，不机械复制全池。SFT 对分析与动作的所有 completion tokens 计算 loss；DPO 两侧保留各自分析，不替换或屏蔽。

Teacher 类别指导是仅用于采样的输入，实际生成的分析是监督输出，二者不能混淆。执行器只将 ID 对应证据送翻译器，分析不写入事实记忆。不按类别覆盖、分析篇幅或模仿 teacher 程度加分。

首版使用 JSON 中的显式分析，输出预留 256 tokens 只是试采样起点；原生 <think> 如另行采用，需冻结 chat template、分隔符与 loss 范围，并验证预处理保留思考 tokens。Qwen3-8B 是候选，48GB 显存评估见 [详细方案 §6.1.1](LOONG_RL_PLAN.md#611-qwen3-8b-与-48gb-显存可行性待实测)。

## 4. 状态、记忆与隔离

- 独立序列先处理约 10 条相关源资源建立冷启动，再正式导出目标状态；不是每条轨迹重新冷启动。短组不强凑，摘要节流单独冻结，五类记忆允许缺失。
- 每条目标先冻结 ReadView、图/知识快照、候选正文及顺序、预算和版本。比较后只将预先固定基线结果写回一次，不把多动作全部写回，也不按参考分选 winner 推进历史。
- 当前跳过 Reviewer，生成译例和派生记忆保留 unreviewed，不伪造检查通过。Teacher 合成译文文件不直接导入 seed TM；若某调用同时承担基线写回，在评分前固定并记录来源。
- 候选池要保留相关五类记忆的可见机会，STM/LTM 显示召回来源但共用去重译例。没有类别不得补造。现有 16 项候选、0–5 项动作、1024 软 token 本次不改；放宽需在正式采样前统一冻结。
- train/dev/test 按资源组与近重复划分。参考、双语查看表和含译文的后台不能进入观察或在线记忆；Teacher 合成译文单独保存，测试参考只作最终离线评测。

## 5. 实现与阶段验收（待完成）

1. 分组划分、源侧可见范围、冷启动与序列清单、冻结状态导出，补齐五类记忆来源标签。
2. Teacher 选择／独立译文调用、断点恢复与质量记录；先训练侧约 128 状态试采样，再冻结 SFT 规模和费用。
3. 导出 SFT 数据并验证分析 tokens 的 loss mask；训练、dev 选择与导出冻结 pi_SFT。
4. 使用 pi_SFT 小样本试采样，验证动作多样性、偏好有效率、外部翻译成本，再扩量生成主 DPO 数据。
5. 分别进行真实长样本的 SFT/DPO 最小训练步与显存记录，冻结依赖、参数、reference 和完整数据清单后正式训练；不将旧时间估算当作已测资源承诺。
6. 比较同规模原始选择器、SFT、SFT＋DPO 与固定基线，固定快照和独立记忆的顺序运行分开报告。DPO 无收益保留 SFT，不预设新路线优于旧方案。

逻辑产物分为 teacher_demonstrations、teacher_translations、sft_dataset、student_rollouts 和 preference_pairs，尚未实现对应文件协议或入口。保留模型、prompt、状态、动作、实际上下文、评分、拒绝与费用版本；训练器只读取明确投影的 prompt/completion 或 prompt/chosen/rejected。上述仍为离线偏好路线，不标为在线 GRPO/PPO。
