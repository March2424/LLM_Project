# 上下文选择器训练协议

更新：2026-11-02。状态：Teacher 采样/候选导出与进度入口已实现，服务器已有旧协议付费采样；pi_SFT 自采样、偏好评分与 GPU 训练尚未实现。详细参数及论文分析见 [LOONG_RL_PLAN.md](LOONG_RL_PLAN.md)。

## 当前路线：Teacher 蒸馏 → pi_SFT 自采样 → DPO

用户明确：翻译始终由外部 LLM 完成，训练只覆盖选择器。下面的两阶段路线替代先混合 teacher、固定基线和未训练学生动作，再同时导出 SFT/DPO 的旧方案。

```text
训练侧源文＋冻结候选／记忆
  → 闭源 Teacher：Observe → 自然语言分析 → 选择 ID
  → 独立固定基线翻译：仅更新本运行记忆，不作为当前SFT输入/标签
  → 筛选示范；通用学生 prompt → Teacher 分析＋动作
  → LoRA SFT 固定 2 epoch → 冻结第 2 轮 pi_SFT
  → pi_SFT 自行观察候选，采样 2–4 条分析＋动作
  → 同一冻结状态下，外部Translator分别翻译＋独立离线评分
  → 固定基线仅写回一次记忆，继续下一条（不混写M个分支）
  → 同状态学生动作的 chosen/rejected → 在 pi_SFT 上做 LoRA DPO
```

## 1. Teacher 蒸馏阶段

- 使用 [teacher 选择模板](../prompts/selection/teacher_context_selector.txt)，类别软提示只用于 teacher 输入；没有配额，不要求选齐五类。
- Teacher 用显式自然语言说明当前翻译需求、候选实际内容及取舍，再输出 selected_ids。分析不能仅罗列 ID，不引用池外证据、人工参考或未来历史。
- 验证动作并保存选中上下文正文、来源、版本和 ContextPack；Teacher 任务不生成最终译文，不再调用 teacher_translation。
- 独立外部翻译仅用于当前运行的记忆推进：先冻结选择状态，再按固定基线翻译当前资源，记录未审译例并更新术语/摘要/角色。当前译文和记忆更新状态不进入本条 SFT label；前序译例仍可作为后续任务的候选记忆。
- 选择结构合法与记忆写回分别记录。有效选择不因后续翻译或记忆更新失败而从 SFT 候选中删除；筛选目前是程序级，不能声称已完成语义质量验收。Reviewer 继续关闭。
- SFT 数据为通用学生 prompt＋相同冻结 observation → Teacher 的 rationale＋selected_ids，不再要求 teacher 在混合四方案中获胜。筛选、失败与拒绝原因均保留，不能为凑数编造分析。

## 2. pi_SFT 自采样与 DPO

- SFT 固定训练 2 个 epoch，预先约定第 2 轮最终 checkpoint 为 pi_SFT，不使用 dev 或 test 选模。它同时是首轮自采样策略、DPO 初始化和 reference；需要精确记录权重、adapter、tokenizer 与 chat template。
- 对同一状态使用 [通用选择模板](../prompts/selection/context_selector.txt)，无 teacher 类别指导，自行采样最多 2–4 次完整分析＋动作；按 selected_ids 集合去重。相同 ID、仅分析措辞不同不构造质量 pair；不足两个有效不同动作则记录并跳过，不无限重采。
- DPO阶段每款游戏建立独立train运行，只以固定基线处理约10条原文冷启动一次，之后跨文件/资源组持续推进；不接入Teacher阶段的最终记忆。固定快照回放仅用于单列诊断。每个状态先冻结候选，再尝试M=2–4条动作，记录实际有效不同动作数；不足2个不构造pair。
- 外部翻译器输入当前原文、源侧元数据、固定硬项及该动作选中的证据正文，不接收rationale作翻译证据。M个分支都只读同一历史；它们不写入共享记忆。比较后固定基线只写回一次，且不受参考分/偏好结果影响；基线失败保留缺口，不用高分分支代替。
- 对各动作分别调用同一冻结外部翻译器，独立 scorer 只在允许的训练侧读取人工参考。先过滤非法动作/硬违规，再按下游翻译质量和既定成本规则选择一对；差异不可靠时不强行排序。
- 主 DPO 的 chosen/rejected 都来自同一冻结 pi_SFT 的实际生成，不把 teacher／基线动作混入主偏好对，也不复用 SFT 前的 teacher 偏好数据。Teacher 或基线保留为诊断对照。
- DPO 中完整 rationale＋selected_ids 都参与 log probability；reference 必须是冻结 SFT，不能错误退回原始基座。完成本轮数据采样后再训练，先不做边更新边采样或多轮迭代。
- 同一 train 状态允许同时用于 SFT 和 DPO，但两个阶段的生成与反馈分开保存；test/external_test 不参与任一阶段训练。DPO 阶段仍需外部翻译及训练侧质量反馈；它与本轮 SFT 的“翻译只更新记忆”用途分开。

## 3. 分析与动作格式

Teacher、学生、SFT、DPO 和部署统一输出 rationale 在前、selected_ids 在后的 JSON。分析通常 1–3 句，以自然语言展开关键名称/译法、事实条件或表达依据及取舍；ID 仅辅助定位，不机械复制全池。SFT 对分析与动作的所有 completion tokens 计算 loss；DPO 两侧保留各自分析，不替换或屏蔽。

Teacher 类别指导是仅用于采样的输入，实际生成的分析是监督输出，二者不能混淆。执行器只将 ID 对应证据送翻译器，分析不写入事实记忆。不按类别覆盖、分析篇幅或模仿 teacher 程度加分。

首版使用 JSON 中的显式分析，当前 Teacher 输出预留 512 tokens；原生 <think> 如另行采用，需冻结 chat template、分隔符与 loss 范围，并验证预处理保留思考 tokens。Qwen3-8B 是候选，48GB 显存评估见 [详细方案 §6.1.1](LOONG_RL_PLAN.md#611-qwen3-8b-与-48gb-显存可行性待实测)。

## 4. 状态、记忆与隔离

- 每个游戏/划分/采样阶段只有一个持续记忆run，起始约10条原文冷启动一次；切换文件、资源组或文本类型均不重置。资源组只决定数据划分与溯源，不是记忆边界。游戏整体样本不足时才缩短冷启动；摘要节流单独冻结，五类记忆允许缺失。
- 每条目标先冻结 ReadView、图/知识快照、候选正文及顺序、预算和版本。比较后只将预先固定基线结果写回一次，不把多动作全部写回，也不按参考分选 winner 推进历史。
- 当前跳过 Reviewer，生成译例和派生记忆保留 unreviewed，不伪造检查通过。独立基线译文只用于本运行的记忆更新，记录固定生成来源；不另行生成 Teacher 译文。
- 候选池按图/源资料、术语/角色、STM/LTM、摘要四组轮流装填；用户允许这两种合组，不要求每个细类至少一条。观察仍保留五类memory_kind，STM/LTM共用去重译例；最终选择没有类别配额。没有类别不得补造。当前统一为16项候选、0–8项软选择、1024软token；八项是上限，不要求无关或超预算证据凑数。硬项另计。
- 本轮不设 dev，原 dev 资源组并入 train，test/external_test 按资源组与近重复隔离。参考、双语查看表和含译文的后台不能进入观察或在线记忆；当前翻译仅存于独立记忆审计，测试参考只作最终离线评测。

## 5. 实现与阶段验收

1. CPU 分组划分草案、冷启动/序列与 128 目标计划已实现，选择观察补齐 memory_kind 标签，见 [CPU_PREPARATION.md](CPU_PREPARATION.md)。已生成无dev草案并执行train源ID边界；真实运行逐条冻结历史。分组和参考质量仍需人工复核。
2. Teacher 选择／独立记忆翻译、断点恢复、进度和质量记录入口已实现；已有旧协议付费试采样，升级后继续128目标pilot，再冻结SFT扩量规模和费用。
3. 导出 SFT 数据并验证分析 tokens 的 loss mask；固定训练 2 轮并导出第 2 轮冻结 pi_SFT。
4. 使用 pi_SFT 小样本试采样，验证动作多样性、偏好有效率、外部翻译成本，再扩量生成主 DPO 数据。
5. 分别进行真实长样本的 SFT/DPO 最小训练步与显存记录，冻结依赖、参数、reference 和完整数据清单后正式训练；不将旧时间估算当作已测资源承诺。
6. 比较同规模原始选择器、SFT、SFT＋DPO 与固定基线，固定快照和独立记忆的顺序运行分开报告。DPO 无收益保留 SFT，不预设新路线优于旧方案。

Teacher示范与记忆审计保存于state.json/每游戏SQLite/calls.sqlite，SFT通过export显式投影；student_rollouts与preference_pairs入口尚未实现。保留模型、prompt、状态、动作、实际上下文、评分、拒绝与费用版本；训练器只读取明确投影的 prompt/completion 或 prompt/chosen/rejected。上述仍为离线偏好路线，不标为在线 GRPO/PPO。

## 2026-11-01：取消独立 dev，固定训练计划

用户明确为节省下游 API 判断成本取消 dev。本轮目标为约 2,000 条有效 Teacher SFT 轨迹、2 个 epoch，使用第 2 轮最终模型；不进行开发集翻译、checkpoint 比较、自动 early stopping 或 best-model 加载。程序验证与训练 loss 日志保留，训练侧 pilot 可用于排错和在正式采样前冻结参数；测试集不用于调参或回头选模型。原 dev 组并入 train，原 test/external_test 资源组保持不变。

取消 dev 不取消 Teacher 示范筛选、DPO 的训练侧下游翻译评分或最终离线测试，也不意味着会自动调用 API 处理新增 train 全量资源。单卡 48GB 的 LLaMA-Factory 配置、数据注册及预检见 [LLAMAFACTORY_SETUP.md](LLAMAFACTORY_SETUP.md)。


Teacher 采样实现与剩余质量边界见 [TEACHER_TRAJECTORIES.md](TEACHER_TRAJECTORIES.md)。源侧 train ID 白名单、固定基线历史、持久化调用、候选导出已实现；服务器已生成部分真实冷启动和旧协议选择；新协议采样未启动，语义筛选后的高质量示范仍待验收。

## 2026-11-02 设计审查对齐

当前采样 schema=2：Teacher 只选择上下文，最多8项；翻译保留且只推进记忆。旧版多余 teacher_translation 和5项上限已修正。旧运行通过 migrate 复制升级，保留费用/成功响应与已有记忆；已开始任务维持原冻结预算以复用响应，后续任务采用8项上限。旧协议轨迹不混入新协议默认SFT导出。

export --dataset-dir 可直接写入注册的 LLaMA-Factory 数据文件，但保留 program_only 未审语义；训练前用 check_training_setup.py --verify-tokenization 在CPU验证完整样本长度与分析监督范围，禁止静默截断。GPU训练、pi_SFT自采样与独立偏好评分仍是后续工作，不将现有配置写成已完成。


## 2026-11-02：每款游戏只冷启动一次

原按资源组创建run的解释已撤销。当前memory_policy=per_game_split_stage_v1：Teacher阶段每游戏/train一个run，先10条冷启动，再依稳定源侧处理顺序生成所抽取的目标，独立翻译持续更新同一记忆。DPO阶段同样每游戏/train一个独立run、一次10条冷启动；M个动作比较后仅固定基线写回一次。测试和各对照方法使用各自独立run，训练目标记忆不进入测试。分支/人物/条件的读取作用域仍检查，共用run不代表所有条目无条件适用。

新CPU计划outputs/cpu_preparation_2026-11-02_per_game为两款训练游戏、128目标、20冷启动，共148步。旧资源组计划和多run历史保留审计，不能直接拼成当前连续记忆轨迹；新采样/默认导出入口会拒绝旧记忆协议。


## 2026-11-02：角色/摘要候选覆盖补充

当前2000条改为保留原128条＋同原生角色/兼容条件块、同原生对象主题/兼容条件块＋其余多类型样本。元数据身份来自源provenance，旧观察不回填、不合并跨文件同名人物。角色先积累2条不同原文，主题先积累5条译例，之后观察其笔记/摘要；这些是正常目标，不是多次冷启动。候选实际可见数量与选中数量分开记录，不强制Teacher选某类别。计划/边界见 [MEMORY_COVERAGE_SAMPLING.md](MEMORY_COVERAGE_SAMPLING.md)。SFT允许这些源侧目标无人工参考；DPO参考式反馈仍必须筛选合格训练参考。


## 2026-11-03：多领域代码交付约定

本轮混合计划包含游戏旧示范、新闻双向和GuoFeng TEST_2 Zh→En；后者明确为seen适配，参考仍仅离线评测。公开文档复用无序资源模式，源图按project一次提取，阶段／方法运行记忆独立。代码准备已接入；按用户最后要求停止执行，不启动新建图、采样或混合SFT。完整范围与已知限制见[多领域SFT设计](SFT_MULTIDOMAIN_DESIGN.md)，执行停止状态见[交接](SFT_MULTIDOMAIN_HANDOFF.md)。
