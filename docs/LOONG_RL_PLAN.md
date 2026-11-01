# Loong 启发的游戏本地化上下文选择训练方案

日期：2026-10-28。状态：**分析与实施建议，尚未实现训练或执行 GPU 实验**。

2026-11-01 对齐：用户确定闭源 LLM 作为固定翻译器，独立 4B 作为选择器；保留 1024 软上下文预算。存储、轻量召回及短 ID/严格动作接口已实现，真实闭环/训练待执行，见 [LIGHTWEIGHT_RETRIEVAL.md](LIGHTWEIGHT_RETRIEVAL.md)。后文论文事实不因工程路线变化而改变。

2026-11-01 轨迹设计更新：只在 teacher 生成候选动作时加入五类记忆的任务相关选择提示；SFT、DPO 和最终 Agent 使用同一份不含类别偏好的通用选择提示，不设置最终类别配额或类别覆盖奖励。新增 teacher 模板尚未接入采样器；本次不改变运行配置的 16 项候选池、0–5 项动作和 1024 软 token，也未执行轨迹采样或训练。具体见 §3.3、§5.1 与 §6。

2026-11-01 后续修订：用户要求将简短思考内容纳入轨迹并参与 SFT/DPO。输出协议现为 rationale＋selected_ids，理由 tokens 不屏蔽；这替代早期“只训练 ID”的设计。Qwen3-8B 为新增可选方案，见 §6.1.1；此前 4B 和时间表只是旧容量假设，8B 的显存及耗时需实测，不据此承诺 32 小时完成。

2026-11-01 最新路线（替代此前混合动作冷启动）：先闭源 teacher 选择示范蒸馏得到 pi_SFT，再由冻结 pi_SFT 自采样构建 DPO。用户明确学生只负责选择，teacher 合成译文不是学生标签或人工参考；详见 §3.3、§5、§6 与 [TRAINING.md](TRAINING.md)。此前 4B、混合四方案和 32 小时表不能直接作为新路线的执行配置。

资源目标：4 张每卡 48GB GPU，在最多 32 小时 GPU 实验窗口内完成一次小规模、可解释的选择器 SFT＋DPO 实验。32 小时包含建运行记忆、采样、评分、训练及有限评测；代码开发、数据授权、人工标注和大文件下载应提前完成。若要求从当前代码状态开始连开发也全部包含在 32 小时内，不能可靠承诺训练和质量评测都完成。

论文输入校验：`2026.emnlp-main.329.pdf`，27页，SHA-256 `4afe7a2f9db7d6a449edda38fab8eb0d87890c03d99d01ba775dc3b6fa077fbf`。本报告未启动模型服务、采样或训练；外部实现资料核对日期为2026-10-28。

## 1. 建议采用什么方案

**只训练 M4 上下文选择器：闭源 teacher 先生成 Observe-and-Act 显式分析与选择示范，外部 teacher 另行生成合成译文用于质量检查；用筛选后的分析＋动作做 LoRA SFT，得到 pi_SFT。随后冻结 pi_SFT，由它为同一状态自行采样 2–4 个选择方案，经固定外部翻译器和独立评分后，在 pi_SFT 上做 LoRA DPO。翻译能力不进入学生训练目标。**

训练问题是：面对同一个原文、图检索候选池、记忆快照和预算，选择哪些软证据，能让固定翻译器产出更好的译文？

这与 README 的 M4 路线一致。DPO 是离线偏好优化，本方案采用 Loong 的反馈与偏好学习思想，不应把结果标为在线 GRPO/PPO，也不能称为完整复现 Loong。

32 小时的交付目标是：一个 SFT adapter、一个经过验证的 DPO adapter（数据和效果达标时）、可重放的选择/评分数据、小规模固定快照对照与部分顺序运行结果。三套主测试集全量评测、多种子训练、选择器和翻译器联合优化不在这次时限承诺内。

## 2. 论文实际做了什么

依据用户提供的 `2026.emnlp-main.329.pdf`，论文印刷页 7576–7580（PDF 第 3–7 页），以及第 23 页 Algorithm 2；已提取全文并核对方法图及方法正文。公开入口：[Loong 论文](https://aclanthology.org/2026.emnlp-main.329/)。

| 论文设计 | 具体内容 | 对本项目的启发 |
| --- | --- | --- |
| 3E 记忆 | Essence 摘要、Exemplar 源译例、Entity 实体记录 | 与现有 M2 类型对应，无需另建一份记忆库 |
| Observe-and-Act | 按摘要、译例、实体三个阶段选择，行动包含分析与所选内容 | 学习哪些证据有助翻译 |
| 选择偏好 | 每阶段采样 M 个选择，每选择采样 N 个译文，按平均 sCOMET 比较 | 选择标签来自下游翻译结果 |
| 利用偏好 | 同一上下文下比较高低分译文 | 原方法还训练翻译能力，本方案先冻结它 |
| 优化 | preferred 样本 SFT，再将选择及翻译偏好合并做 DPO | 采用两阶段，但只训练选择输出 |
| 推理 | 每类上下文只选择一次，最后翻译；不在在线推理时重复评分采样 | 昂贵探索留在离线训练 |

论文报告：训练数据来自 News Commentary v18.1，每个语言对约 500 篇、筛选超过 50 行的文档；片段长度 5 句，摘要/译例候选各 4，M=7、N=5。SFT 为 1 epoch、batch 64、学习率 1e-5；DPO 为 1 epoch、batch 32、学习率 5e-6、beta=0.1、LoRA rank=8；最大长度 2,560。每个模型在 4 张 A800 上训练约 20 小时。[论文 §4.1](https://aclanthology.org/2026.emnlp-main.329/)

按上述采样配置计算，一个片段约需要 `3 × 7 × 5 = 105` 次翻译生成，另有选择和记忆更新调用。**20 小时的表述是训练耗时，不能推断其中已包括全部偏好数据生成，也不能由此推出任意四张 48GB 卡能同速完成。**

作者仓库将采样、推理、训练、COMET 分为不同环境；训练路线是全参数 SFT＋LoRA DPO。公开 YAML 与论文也有差异，例如 DPO YAML 使用 `max_steps=1200`，batch 取决于 world size。实施时应固定所用 commit，登记实际配置，不把论文值与仓库值混用。[作者仓库](https://github.com/YutongWang1216/LoongDocMT)、[SFT 配置](https://github.com/YutongWang1216/LoongDocMT/blob/main/train/full_sft.yaml)、[DPO 配置](https://github.com/YutongWang1216/LoongDocMT/blob/main/train/lora_dpo.yaml)

论文 §4.3 的消融显示，只训练选择偏好相比联合训练选择与利用会退化。因此，本方案只训选择器是资源取舍，不能引用论文联合训练收益作为独立选择器的预期提升；若固定翻译器无法利用召回证据，先改善提示词/翻译环境并重新冻结实验，再考虑后续联合训练。

## 3. 在项目哪里加入训练

```text
Occurrence + 固定知识/图快照 + ReadView
    → M3 召回与作用域检查
    → CandidatePool（冻结候选正文、ID/版本、顺序、来源）
    → 硬项由程序保留；软项交给 M4
    → M4 选择器输出自然语言 rationale＋selected_ids
    → Context Builder 验证并生成真实输入包
    → 固定 Translator → 硬检查/必要审校 → 提交
```

RL/偏好优化放在 `CandidatePool → selected_ids`。本轮不学习图节点身份、关系是否真实、分支适用性、术语是否强制或是否批准记忆。那些决定必须在进入策略前完成。

Loong 的三个选择阶段在本项目首版合并为一次短 JSON 输出，避免三次调用及阶段间采样倍增。这是预算驱动的领域适配，效果需实验验证。游戏的源文证据、关系记录和角色笔记一并进入软候选；强制身份/关系要求仍是硬项。

### 3.1 观察与动作

观察包括当前原文、语言方向、已知人物/分支/阶段、硬约束简述、软候选正文及类别、图关系标签、每项 token 成本和软预算。候选正文先做确定性展示，不把完整数据库 JSON、历史模型提示词和长 hash 都送入选择器。

用任务内短 ID `c01…c16` 映射回原始 ID/版本。示例动作：

以下为格式示例，假设当前文本涉及 Cleave，c02 提供其译法，c07 提供适用条件；不代表真实采样结果。

```json
{"rationale": "当前文本涉及技能 Cleave，术语建议中的‘顺劈斩’可帮助保持名称一致（c02）。技能说明补充了仅限近战使用的条件（c07），能避免扩大其适用范围；重复名称条目没有新增信息。", "selected_ids": ["c02", "c07"]}
```

允许空列表，表示只使用硬项，仍需给出简短依据。按用户最新要求，轨迹、SFT、DPO 与部署均保留 1–3 句决策分析；统一输出 rationale 在前、selected_ids 在后的 JSON。选择器输出预留先设 256 tokens，试采样后冻结，不截断未完成 JSON。固定候选顺序、ID 按该顺序规范化，以减少同一集合多种排列造成的偏好噪声。训练时可做受控候选顺序置换，但同一 preference pair 的 prompt 和 ID 映射必须完全一致。

rationale 是明确生成的简短选择依据，说明相关证据及必要的取舍，不代表已核实的模型内部推理；不得引用池外证据、参考译文或未来历史。SFT 对理由和 ID 全部计算 completion loss；DPO 对完整“理由＋ID”计算策略及 reference 的序列 log probability，不将理由设为忽略标签或移到 prompt。执行器只将 ID 对应的证据交给翻译器，理由留作训练与审计，不写入事实记忆。仅增加理由不能获得偏好奖励，实际选择仍由下游翻译质量判断。

显式自然语言要求：观察中保留候选正文；rationale 展开“当前文本需要解决什么、候选实际提供什么、为什么选择或跳过”，不能用 ID 代替语义内容。可描述关键名称及译法、事实条件、角色表达或相关译例的具体作用，ID 仅辅助定位；不要求逐字复制全部候选，也不要求逐项解释所有未选条目。Teacher、学生、轨迹导出、SFT/DPO 与部署均遵循此要求，不能只在 teacher 原始日志里保留展开分析而导出时退化为 ID 说明。通常 1–3 句，但以表达清楚为准，完整输出预算在试采样后冻结。

原生 <think> 模式与此结构化理由协议分开：第一版用 JSON rationale 显式训练简短分析，避免同时生成另一份长思考。若后续采用原生 thinking completion，需单独冻结 chat template、分隔符、完整输出与 loss 范围，不能只保存最终 JSON 却声称原生思考参与训练。当前采样/训练尚未实现，需核对预处理没有屏蔽理由 tokens。

非法 ID、重复、越界、超预算或解析失败都记录为策略失败，在线回退固定选择器。非法动作不作为有效高低质量偏好对；成功回退也不能被记录为学习策略自身成功。

### 3.2 当前代码必须先补齐的部分

已存在的接口见 [graph schema](../src/localization_agent/schemas/graph.py)、[GraphRetriever](../src/localization_agent/retrieval/service.py)、[MemoryService](../src/localization_agent/memory/service.py)。

1. `Candidate.text` 已改为可读展示，短 ID/版本与审计清单保持可追踪映射；模型不读取完整数据库 payload。
2. `build_context()` 已支持严格显式选择：重复/池外/超过五项/超预算的动作直接判非法；固定基线仍整项装填，实际输入和未选 ID 进入清单，不把自动删减后的结果归因给原动作。
3. Translator 软证据预算保持 1,024 tokens，按已冻结预算 tokenizer 执行；闭源服务实际用量另读 API usage。硬项、源文、模板、输出预留单独计入翻译总窗口。选择器看到完整候选池，1024 不是其输入上限；原 2,048 输入/3,072 翻译总输入估算不再作为硬约定，接入 4B 与实际服务后分别实测、冻结，不能静默截断候选证据。
4. 固定最多 16 个软候选，保留类型多样性；硬项独立且不可裁剪。硬项本身超限返回冲突。候选压缩只能选完整证据片段，不能截断条件、数值或否定。
5. `GraphRetriever.commit()` 当前重跑 retrieve 验证选择。训练中冻结 lexical 检索、图和提示词，优先实现对已冻结候选的有效性检查，避免重验改变候选或再次产生模型调用。
6. 已有 HTTP 模型适配器、最小翻译/审校及占位符/术语检查；尚未完成真实翻译闭环验收、4B 选择器接入或源/参考隔离评分器。演示中的 `passed=True` 不能用于训练记忆准入。

### 3.3 Teacher 引导与策略自主选择的边界

Teacher 的职责是为训练状态提出有用的候选动作。类别偏好只用于 teacher 提示词中的检查建议，不作为程序配额、动作合法性条件或奖励；teacher 也可以跳过某类、只选图证据或返回空选择。

| 输入特征或证据需求 | Teacher 的软提示 |
| --- | --- |
| 专业名称、技能、物品或反复出现的术语 | 优先查看适用术语建议，核对源词和译法；软术语不因此变成强制规则 |
| 对话且有明确人物身份 | 优先查看该人物适用的角色/表达笔记；没有身份或笔记时不补造 |
| 本运行近期已有相关译例 | 查看 STM 的译名和表达延续；“近期”不等于相关，也不等于剧情前文 |
| 需要远距离译法一致性或可复用表达 | 查看兼容 LTM，优先提供 STM 尚未覆盖的信息，避免重复译例 |
| 当前句需要主题层面的事实或已建立表达 | 查看摘要；保留条件和分歧，不凭摘要推断剧情顺序 |
| 记忆不足以解释含义、对象或条件 | 查看图召回的定义、相关源文和关系；有明确帮助的图证据可独立入选 |

Teacher 完整模板见 [teacher_context_selector.txt](../prompts/selection/teacher_context_selector.txt)。通用策略模板继续使用 [context_selector.txt](../prompts/selection/context_selector.txt)。两者使用相同冻结 observation、ID 映射和动作预算，teacher 不读取参考译文或额外背景；模型/版本和调用费用另行登记。它可以复用现有强模型服务，但选择动作是独立任务调用，不能混入翻译 prompt 或把翻译结果当选择输出。

提示词按阶段使用：

| 阶段 | 生成器与输入 | 输出及用途 |
| --- | --- | --- |
| Teacher SFT 示范 | 闭源强模型，冻结 observation＋类别软提示 | 自然语言 rationale＋selected_ids，筛选后作为 SFT label |
| Teacher 合成译文 | 外部闭源模型，当前源文＋实际选中证据 | 单独保存 teacher_translation，仅用于示范检查和审计 |
| SFT | 开源选择器，通用策略 prompt＋相同 observation | 学习 teacher 的分析与动作，全部 completion tokens 参与 loss |
| DPO 数据采样 | 冻结 pi_SFT，通用策略 prompt＋相同 observation | 自行采样多条分析＋动作；不混入 teacher／原始学生／固定基线作为主偏好对 |
| DPO 训练与最终 Agent | 初始化为 pi_SFT，通用策略 prompt | 无类别配额或覆盖奖励；始终由外部 LLM 翻译 |

Teacher 选择与合成译文可复用同一闭源后端，但使用独立任务调用；选择任务不输出译文，译文任务使用 [teacher_translation.txt](../prompts/selection/teacher_translation.txt) 和实际 ContextPack，不读取参考、选择理由或未选证据。完整 teacher 轨迹绑定两次调用的来源和上下文 hash。同一服务也可承担固定下游翻译器，但模型、参数、prompt 与各阶段角色须分别登记。

SFT 不再要求 teacher 在旧的混合四方案里获胜，而是筛选 teacher 示范的格式、证据与下游质量。筛选不能只看 JSON 合法便宣称高质量；保留检查依据和少量人工抽查，当前不接运行时 Reviewer。学生 prompt 不含 teacher 类别指导，输出保留 teacher 实际自然语言分析。蒸馏偏差可能存在，自采样 DPO 能针对学生实际输出构造偏好，但不保证优于旧路线，需以同预算 dev 和最终测试验证。

候选池应让相关五类记忆有机会被观察，不能在召回阶段被图资料完全挤掉；这不代表最终必须选齐五类。当前代码只分四组装填，term/style 合组，STM/LTM 均展示为 translation。采样前需要在观察导出中保留术语、角色、摘要、STM、LTM 的类别/召回来源，STM/LTM 继续引用同一份去重译例，不复制存储；该观察扩展尚未实现，实施时同步公共接口。Teacher 与学生都应能看到这些标签。缺失类别保持缺失，不补造记忆。

现有 max_selected=5 仍是所有方案共用的总数上限，并非五类记忆各占一项。是否放宽总项数单独在训练前依据开发诊断决定；一旦修改，teacher、学生与对照统一冻结预算，不能只给 teacher 更多证据。允许 ID、作用域/快照、格式/占位符和适用硬约束始终保留。

## 4. 数据从哪里来，准备多少

### 4.1 第一轮只训练 En→Zh

选择单方向可以减少数据分散、模板差异和验证开销。GuoFeng 主方向 Zh→En 与此不同，不能用这轮 En→Zh 结果宣称已经完成 GuoFeng 主评测或双向泛化；后续另做 Zh→En 或共享双向训练。

主训练来源建议是 News Commentary v18.1 的 En–Zh 文档对齐数据，延续项目与 Loong 的训练路线。数据入口见论文和作者采样说明；本次未下载核验实际文件及文档边界。若拿到的版本仅剩随机句对且无法恢复真实文档 ID，不允许拼成伪文档，应先解决边界或换可追溯的文档训练源。

游戏数据必须同时具有可用于离线评分的人工参考与源侧信息。当前无参考的解包 CSV 可以用于真实源图/导入诊断，不能直接用 sCOMET 构造可靠偏好。Teacher 合成译文允许用于独立标记的 SFT 示范检查，但不是学生训练标签，不能混作人工金标准或正式质量评测参考。无人工参考时不直接用它替代主 DPO 质量信号；若另做合成参考实验，应单列而非混入主实验。

| 划分 | 首轮目标 | 处理方式 |
| --- | --- | --- |
| train | 约 2,000 个有效选择状态 | 来自完整训练文档；约 1,600 通用＋最多 400 游戏状态（有合格数据时） |
| dev | 约 200 个状态及对应完整文档 | 确定长度、偏好阈值与 checkpoint；不进入训练 |
| pilot test | 约 300 条、按完整文档/场景选取 | 策略冻结后一次比较，不用来选模型 |
| 小型游戏诊断 | 约 50 个有明确证据需求的状态 | 人工标注应选证据，检查角色/分支/占位符；单列结果 |

以上是容量目标，不是已取得的数据量。整文档原则优先于凑整数字。若游戏参考未准备好，先做通用方向方法验证并明确“游戏迁移未验证”，不临时编造 400 条数据填满计划。

先按文档/书籍/场景分组划分，再在 train 内选状态；检查同源文档、近重复段落和本地化变体。GuoFeng Test 2、IWSLT2017 指定测试划分、自建游戏 test 全部不进入奖励、SFT、DPO、教师记忆或阈值调节。训练参考仅由独立 scorer 按 sample_id 读取，选择器/翻译器/图/在线 QA 均不读取参考列。

### 4.2 如何构建可信的状态与记忆

对每篇训练文档顺序执行一个固定基线，使用其生成、实际检查通过的译例构建 M2。先在每个独立序列处理约 10 条相关资源形成冷启动，再导出目标状态；短组允许较少历史，不跨划分凑数。某个状态的多种动作都读取这份固定历史与同一个候选池；评分后不把最佳译文写回该基线 run。这样避免同一比较因历史不同而不公平，也避免训练参考通过“挑最高分历史”持续影响候选状态。

每个状态冻结：occurrence/source version、知识快照、图快照、ReadView、完整候选正文/版本/顺序、源侧可见范围、模型/提示词/tokenizer 版本。冻结导出物须自包含，不能只留下日后可能失效的数据库引用；实时在线运行仍拒绝过期证据。

首轮允许类型缺失。按当前用户约定跳过 Reviewer，隔离 run 中程序记录的译例和派生记忆保留 unreviewed，不伪造语义通过；摘要无满足条件的译例则为空，冷启动 10 条不保证五类记忆齐全。每步只按预先固定基线推进一次历史，teacher 合成译文文件本身不作为 seed TM 导入；若同一调用兼作基线写回，也须在评分前确定并保留未审标记。源关系图先用可解释的结构关系和少量批准语义边，不在 32 小时窗口内全量模型抽图。公共语料无人物/分支时保留未知。

目标最多处理约 8,000 条训练源句形成历史，从中选择 2,000 个状态；若实际文档更长，按完整文档减少文档数。每篇依赖前序提交，但可在不同文档之间并行。无序 CSV 不自动形成历史记忆，遵守 [CSV_IMPORT.md](CSV_IMPORT.md) 的未知顺序保护。

## 5. 低成本采样与偏好构造

### 5.1 两阶段数据生成：Teacher SFT 示范 → pi_SFT 自采样

阶段 A：在训练侧冻结状态上，由闭源 teacher 输出自然语言 rationale＋selected_ids，程序严格验证动作并构建实际 ContextPack，再由外部 teacher 生成合成译文。保留检查与质量依据，筛选后导出通用学生 prompt → teacher 分析＋动作；合成译文、参考路径和评分不进入学生输入或 label。固定／图优先／仅硬项动作可作诊断，不再是 SFT 轨迹的必需竞争者。

阶段 B：完成 SFT，在 dev 选择并冻结一个 pi_SFT checkpoint，同时作为 DPO 的采样策略、初始化和 reference。对每个 DPO 训练状态，由 pi_SFT 在同一个通用 prompt 下独立采样最多 2–4 次自然语言分析＋动作，不加入 teacher 类别指导。按 selected_ids 集合去重，只有理由措辞不同的动作不构成新的质量方案；每个保留动作携带自己的完整分析，不补写或拼接 teacher 理由。少于两个有效不同动作时记录原因，首版不无限重采。

每个不同动作交给同一个冻结外部 Translator，按 §5.2 的动作、硬约束、质量及成本规则评分；从同一状态选择一对可靠优劣动作。主 DPO 数据两侧都必须由同一冻结 pi_SFT 生成。Teacher 和固定基线只作诊断，不能冒充学生自采样；以后若采用混合来源，要另设版本和实验。

SFT 与 DPO 可复用同一 train 状态，也可在 train 内扩充 DPO 状态，绝不能使用 dev/test 训练。两阶段导出物分别管理：teacher_demonstrations、teacher_translations、sft_dataset、student_rollouts、preference_pairs；这些是拟定逻辑产物，不是已有命令。DPO 不能直接复用 teacher 阶段的旧 chosen/rejected；即使 observation 相同，也必须在 SFT 完成后重新采样动作并获取下游反馈。

每个序列在当前位置先冻结历史，再比较候选；结束后只将预先指定的固定基线结果写入一次历史。所有候选及 teacher 合成译文不得一起写回，不按参考分选最高结果推进历史。DPO 状态可复用阶段 A 的自包含历史快照，以便先隔离策略变化；顺序端到端评测再让各策略独立演化目标记忆。

新调用预算须分阶段核算：N_SFT 个示范约需 N_SFT 次 teacher 选择＋N_SFT 次 teacher 译文；N_DPO 个状态最多 K×N_DPO 次学生选择＋至多 K×N_DPO 次外部翻译，K=2–4。另计冷启动、逐步基线写回、复测与评测，完全相同请求可缓存但不提前假定命中率。先分别小样本试跑两阶段，再冻结正式规模和费用；旧的“总计最多 8,000 次翻译＋4,000 次选择”不再是本路线总预算。

### 5.2 评分按约束、质量、成本依次决定

先检查动作合法和译文完整性、占位符、批准硬术语等。服务超时、截断、缺译和硬违规单独计数。偏好训练首版只比较两边动作合法、译文硬检查通过的方案，失败状态保留在完整率分母，不能静默丢弃后宣称系统全成功。

以原始 `wmt22-comet-da` 分数作为 Q，不混淆论文表中的百分制展示。起始质量差阈值 `delta_Q=0.01` 是待 pilot 校准的工程参数，不是统计显著性界限；建议检查 0.005/0.01/0.02 的保留量及人工抽查一致性，然后在 dev 冻结。

- 若最佳与较差方案差值 ≥ delta_Q，形成质量偏好。
- 若差值在噪声带内，首版默认不形成质量 pair。若已复核质量相当，且软上下文节省 ≥20% 且至少 128 tokens，可形成单独标记的成本 pair，最多占 20%。
- 有语义错误迹象或必要证据缺失时，不因文本更短而判 preferred。
- 每状态最多一对；避免把同一状态所有两两比较当成独立数据。
- 不按类别齐全、术语/角色优先、图证据占比或模仿 teacher 程度加减分；这些只用于行为诊断。主 DPO 的 chosen/rejected 均由 pi_SFT 自采样产生，优劣来自下游结果，不来自 teacher 身份。

目标得到约 600–1,400 对仅是规划区间，实际取决于合格率。pilot 若可用偏好占比 <25%，先分析候选质量、上下文是否必要和评分噪声；不要简单放宽阈值。总计不足约 400 个有差异状态时，以 SFT 和数据诊断交付为主，DPO 只做探索性小试，不主张可靠收益。

训练集的 sCOMET 只负责偏好反馈。人物口吻、指代、跨句一致性需 dev 的人工抽查和篇章评测补充，不能由 sCOMET 一项证明。模型卡说明该 checkpoint 是参考式指标，需 source、hypothesis、reference。[COMET 模型卡](https://huggingface.co/Unbabel/wmt22-comet-da)

## 6. SFT 与 DPO 怎样训练

### 6.1 模型选择

| 角色 | 首选 | 原因与边界 |
| --- | --- | --- |
| 选择器 | Qwen3-4B-Instruct-2507 | 4B、非思考输出，适合短结构化动作；官方为 Apache-2.0；能力需本任务验证 |
| 轨迹 teacher | 闭源强模型，具体服务/版本在采样前冻结 | 生成 SFT 分析＋选择示范，另行调用生成合成译文；不训练学生翻译，不默认作为质量裁判，费用单列 |
| 固定翻译器 | 闭源 LLM，服务/模型版本待真实闭环前冻结 | 所有对照相同；记录 API 参数、usage、费用、限流与失败，不占本地 7B 部署卡 |
| 偏好 scorer | wmt22-comet-da | 批量、离线参考式质量反馈 |
| Judge | 预先固定的独立后端或人工盲评 | 若无独立模型/API，明确 Judge 尚未完成，不用翻译器自评冒充独立结论 |

选择器模型卡依据：[Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)。选择 4B 是成本与接口复杂度的取舍，不意味着它已被证明优于其他选择器。仅选一套主模型，不在 32 小时里做模型规模扫描。

### 6.1.1 Qwen3-8B 与 48GB 显存可行性（待实测）

用户提出采用 [Qwen3-8B](https://huggingface.co/Qwen/Qwen3-8B)，可作为选择器候选，尚未替换已有 4B 配置。官方模型为约 8.2B 参数，支持原生 thinking/non-thinking；本方案可用非原生 thinking 模式输出显式 rationale，简短分析仍参与训练。

这里的 48GB 指单张 NVIDIA GPU 显存；若指 CPU RAM 或 Mac 统一内存，不能套用下面的 CUDA 训练判断。按 8.2B×2 bytes 估算，BF16 冻结基座权重约 16.4GB（15.3GiB），不是训练总占用。LoRA 还需要适配器梯度/优化器、激活和计算工作区；DPO 一对有 chosen/rejected 两条序列，不能按 SFT 单条估算。

单卡 48GB 的保守起点：BF16 LoRA、micro-batch=1、rank=8–16、gradient checkpointing、关闭训练 use_cache，并采用硬件/依赖支持的高效 attention。先测真实样本的完整 token 长度，再用约 2K–4K 总序列做小批量可行性验证；长观察不得静默截断，4K 不是已冻结的全量上限。理由与 ID 的输出预留另计入总序列，不挤占 1024 翻译软证据预算。

LoRA SFT 通常可行；LoRA DPO 也有可行配置，但需避免额外常驻完整参考基座。可将 SFT 合并为新基座、再训练 DPO LoRA，并使用框架支持的共享基座/adapter 切换保持 reference 为冻结 SFT；或采用 [TRL 的 reference log-prob 预计算](https://huggingface.co/docs/trl/dpo_trainer)。具体实现取决于冻结的训练框架版本，不能把 TRL 参数直接写入 LLaMA-Factory。reference 不得错误退回原始 Qwen3-8B。完整双基座、长序列或更大 batch 可能超限；QLoRA 只作另行标记的备用。

先分别对 SFT 和 DPO 执行真实长样本的最小训练步，记录峰值 allocated/reserved 显存与耗时，保留余量后再扩样。上述为参数量估算和工程判断，不是本项目已测显存或吞吐。

### 6.2 SFT：学习输出有用的选择

从闭源 teacher 的示范中筛选证据可追溯、动作合法、分析与证据一致且下游质量合格的样本，不再从未训练学生/基线的混合方案里挑 winner 作为主 SFT label。目标约 1,000–2,000 个示范仅为容量建议，实际不足时照实记录。Teacher 输出质量仍需验证，不因模型闭源或规模大而自动认定正确。

SFT 的 label 是完整的 rationale＋selected_ids JSON，不是目标译文或参考译文；只对 assistant completion 计算 loss，理由与 ID 均参与。Teacher 的实际理由随其动作保留，不能把 teacher 理由拼给不同动作。Teacher 译文只保留在单独审计中，不进入选择器 prompt 或 completion；固定基线只作诊断，不为训练伪造其思考。

导出时重新用通用策略模板和冻结 observation 构造学生 prompt，配对同一状态经筛选的 teacher 示范。Teacher 的类别建议、生成 prompt 和模型信息仅保留在独立采样审计中，不进入学生输入或 label；不把带引导的 teacher 原始对话直接当 SFT 样本。标签仍须通过相同动作验证与下游筛选。

建议起点：LoRA rank=16、alpha=32、dropout=0.05，注意力和 MLP 线性层；BF16、gradient checkpointing；学习率 1e-4、warmup 0.05、最多 2 epochs、有效 batch=32。四卡每卡 micro-batch=1、累积 8 步得到 32；实际序列上限在完整候选观察的长度测量后确定，原 2,176 仅为早期估算，不能截掉合法候选/动作来凑长度。训练框架字段以实际固定版本为准。

2,000 样本、batch 32、两轮约 126 次 optimizer update，3 小时是包含加载、验证、保存的预算上限，不是已经测得耗时。关注 JSON 合法率、超预算率和 dev 下游分数，不能只看训练 loss。

### 6.3 DPO：偏好更好的选择动作

同一个 prompt 的 chosen/rejected 是两个完整的“理由＋选择 ID”JSON，绝不能填两个译文。理由不屏蔽，和 ID 一起参与 DPO；相同 ID 但仅措辞不同不单独构造质量偏好。SFT 与 DPO 可复用同一 train 状态，但 DPO 的动作和偏好反馈必须在 SFT 完成后重新生成；不能沿用 teacher 动作对。所有 dev/test 状态仍隔离。

这里的 prompt 是与 SFT、部署共用的通用策略 prompt，不含 teacher 的类别建议。两个动作均来自同一冻结 pi_SFT，绑定同一个 observation、pool_hash 和 ID 映射，并保存 sampling_policy_version 与生成参数。首轮在整个 DPO 数据集采样完成后再训练，不边更新策略边混用不同版本的动作；这是学生自采样的离线 DPO，不是在线 GRPO/PPO。若将来另行实现在线 RL，同样不加入类别提示、类别配额或类别覆盖奖励。

DPO 最小化：

```text
-log sigmoid(beta * [
  log pi_theta(chosen|x) - log pi_ref(chosen|x)
  - log pi_theta(rejected|x) + log pi_ref(rejected|x)
])
```

pi_ref 必须是**完成 SFT 后冻结的策略**。推荐把 SFT LoRA 合并导出为新基座，再添加一份新的 DPO LoRA；合并后的 SFT 基座同时是 reference。另一种方式是保存 SFT reference adapter 与可训练 policy adapter，但需明确加载/切换，不能关闭 adapter 后意外退回原始未 SFT 模型。

起点：beta=0.1、学习率 5e-6、rank=8、alpha=16、dropout=0.05、1 epoch、有效 pair batch=16（四卡 micro=1、累积4）。预计算冻结 reference log probabilities，缓存绑定 tokenizer/chat template/prompt/chosen/rejected/model hash；DPO 的 chosen/rejected 两条序列会增加显存，不能按 SFT 的 batch 估算。

按尽量简单的要求，首版优先用 LLaMA-Factory 的配置式 LoRA SFT/DPO；轨迹采样、外部翻译与评分仍由仓库脚本完成。若需要自定义训练或 reference log-prob 预计算，可选 TRL＋PEFT；不能混用两个框架参数，实际环境先跑通最小 batch。COMET 保持独立环境。[DPOTrainer 官方文档](https://huggingface.co/docs/trl/dpo_trainer)

目前仓库没有 GPU 训练锁文件，因此本报告不编造“已验证版本组合”。租卡前准备并固定 torch/CUDA、transformers、trl、peft、accelerate、vllm、unbabel-comet 和各 checkpoint revision；分别保存训练、推理、指标依赖锁文件及一次最小前向/反向记录。

### 6.4 是否要上真正的在线 GRPO

本轮主线选 DPO。在线 GRPO 的每个策略动作还要触发翻译和 COMET，带来嵌套 rollout、奖励服务调度及噪声处理；短 JSON 输出并不会消除下游成本。[GRPO 官方实现](https://huggingface.co/docs/trl/grpo_trainer)

若研究要求必须包含在线 RL，可在后续额外预算做固定状态、每组4动作、30–50次更新的小试，并与 DPO 分开报告。这不是本次32小时交付条件，也不建议挤占保留的评测时间。当前单步选择属于 contextual bandit 设定，只优化当前输出反馈，未解决长期记忆收益的多步信用分配问题。

## 7. 四卡与 32 小时的具体安排

### 7.1 显存与部署

四张48GB卡是四个独立显存空间，不能默认等价于一张192GB卡。4B BF16权重约8GB，这是参数量估算；实际还有激活、KV cache、梯度、优化器、workspace和框架开销。闭源翻译通过 API 执行，本地 GPU 主要用于选择器和评分。

先用单卡 BF16 LoRA micro-batch=1 测峰值，目标保留约15%余量。4B LoRA与短序列有望适配48GB，但本文未在目标硬件上验证。溢出先降 micro-batch、实际序列长度或启用适配的激活优化；QLoRA作为备用，不同时把多个变量全改掉。模型降精度后的对照仍须统一。

| 阶段 | GPU 0–2 | GPU 3 |
| --- | --- | --- |
| 历史生成 | 闭源 API 调度，不常驻翻译权重 | COMET/实际QA，按需处理抽查队列 |
| Teacher SFT 示范 | 闭源 API 生成分析＋动作及独立合成译文；可与阶段历史生成调度 | 程序检查、可用训练参考评分及抽查 |
| SFT 后学生自采样 | 冻结 pi_SFT 生成 2–4 个动作，固定闭源 API 分别翻译；按服务配额限并发 | COMET批量评分，输出学生偏好对 |
| SFT/DPO | 四卡 DDP LoRA，共同训练同一策略；停止其他 GPU 服务 | 同左 |
| 评测 | 同配置闭源翻译 API，4B 选择器按阶段加载 | 指标评分；Judge另按资源登记 |

本轮不安排本地翻译副本；采样时间同时受 API 配额、费用和本地评分吞吐约束。DDP仍需通信；若四卡跨机或PCIe条件较差，要实测吞吐，不能按4倍单卡速度估算。旧 32 小时表只作规划，须按实际 API 试跑吞吐复核。

### 7.2 两阶段路线的候选时间预算（未实测）

下面仅保留 32 小时目标的示意分配，必须用实际 teacher API、学生模型、长度和有效偏好率重估；8B 及分析输出不沿用旧 4B 吞吐。依赖安装、数据准备和权重下载应提前完成。

| 墙钟时间 | 任务 | 停止点 |
| --- | --- | --- |
| 0–2h | 检查配置与隔离，teacher 训练侧约 128 状态试采样及吞吐检查 | 数据/服务不匹配则缩减规模 |
| 2–8h | 基线历史、状态导出、teacher 示范与合成译文筛选 | 按完整资源组截止，保留失败 |
| 8–11h | LoRA SFT、dev 检查与冻结 pi_SFT | 保存可重放的采样策略与 reference |
| 11–21h | pi_SFT 小试后扩样，外部翻译/评分与偏好导出 | 到点停止扩样，不回填 teacher 动作凑 pair |
| 21–25h | SFT reference 检查/预计算（框架支持时）、DPO、dev 选模 | 数据无可靠偏好则保留 SFT，DPO 标为未执行 |
| 25–29h | 固定快照对照及有限完整序列评测 | 提前冻结文档，不只保留成功样本 |
| 29–32h | 故障缓冲、导出、抽查、结果汇总 | 不新增大训练 |

总目标为 32 墙钟小时、四卡最多 128 GPU 小时租用容量，不代表始终满载或已测可完成。费用按实际 GPU 单价加 teacher 选择、teacher 合成译文、学生采样、外部翻译、评分与评测分别统计。SFT 不完成并冻结前不能启动主 DPO 自采样。

### 7.3 怎样判断能否按时完成

把历史生成、选择器、翻译、COMET、QA分别测量；用持续运行而非空载单请求速度。128状态pilot覆盖短/长输入及不同候选类型，记录p50/p95、显存、排队和失败率。

对某阶段：`预计时间 = 请求总数 / 实测端到端有效完成率 + 启停/重试开销`。流水线评分与生成可以重叠，整体吞吐取瓶颈；串行处理则相加，不能只统计decode tokens/s。

- 分别测量 teacher 选择/译文、基线历史、SFT、pi_SFT 采样、外部翻译/COMET 和 DPO，不能只用一次动作采样速度估计两阶段总时间。
- N_SFT 和 N_DPO 可不同，按 §5.1 分阶段请求数量、实际完成率和费用外推。重复动作减少实际翻译量，但不能据此预设会形成足够偏好。
- 训练按实际序列长度、每步耗时、有效 steps、保存与验证外推；SFT 完成前只能粗估学生采样质量，之后必须再做小试。

缓存键包含原文、实际selected IDs/正文hash、硬项、模型参数及prompt版本。缓存只去掉完全相同请求；不能把不同上下文或不同方法的目标记忆混用。

缩减档分别减少 teacher 示范数和 DPO 状态数，学生每状态先用 2 个动作；保留 dev 和完整资源组评测，减少训练量而非删除评测。有效 pairs 不足约 400 时，DPO 只作探索性小试或保留 SFT，不承诺可靠增益。缩减选择只能看train/pilot吞吐和质量，不能看test结果再调整。

## 8. 评测与“有效”的判断

固定同一个候选池、硬项、Translator、token预算、生成参数和检查流程，对照：固定选择器、同规模未训练提示式选择器、SFT、SFT＋DPO。可额外记录仅硬项，但不挤占四个主要对照。先看首次翻译，不先用多轮修复掩盖选择差异。

四个主要对照中的模型选择器统一用通用策略模板，无 teacher 类别建议。在预留的小型 dev 诊断中，可让同一 teacher 使用有/无类别建议、相同 observation 和预算各生成动作，比较下游质量及选择分布；仅解释提示的作用，不把 teacher 大模型对学生小模型的差异当作提示收益。记录每类候选可用率、入选率及 token 占比，区分“没有召回”与“策略未选”；没有选齐五类本身不算失败。诊断规模和费用在采样前计入总预算，不另行全量扩样。

固定快照层：所有方法共享完全相同的冻结历史；评测当前译文与选择开销。顺序运行层：每方法建立独立run，由自己已经检查通过的译文更新记忆；不共享目标侧TM。两类结果分开标注，不能把固定历史单步实验称为全系统收益。

约200 dev＋300 pilot test状态，四方法共约2,000次基础翻译，另预留约500次给小型顺序运行、失败重试或盲评复核。若完整文档超过预算，运行前缩减文档数量并冻结清单，不能到时截掉困难尾段。

报告sCOMET句级均值及文档宏平均；dCOMET按篇章上下文评分，不用句均值替代，窗口/参考可见性/截断按文档边界固定。[篇章指标实现](https://github.com/amazon-science/doc-mt-metrics)。文档未完整翻译或上下文不足时记录未评分原因和覆盖率。

LLM Judge只在已落实独立后端时执行相同条件盲评；无API预算时，先完成30–50个上下文敏感样例的人工盲评，并将Judge标为待完成。不能把它记成三项主指标全覆盖。小样本、单种子结果只能支持初步结论；可按文档bootstrap报告区间，不把微小点差当可靠改进。

同时报告：必要证据覆盖率、非法动作/超预算/回退率、硬约束符合率、失败完整率、选择器自身输入输出tokens、翻译tokens、总延迟及建库/采样/训练/评测成本。选择器可能节省翻译tokens却增加总成本，必须以合计成本判断。

DPO未优于SFT时保留SFT为部署策略，报告DPO无增益；不因已经做了DPO而默认部署它。建议验收目标是有效选择率≥99%、硬项不被删除、dev质量不下降且质量/总成本至少有一项可信改善；这些是目标，不是当前结果。

## 9. 实现任务与产物

以下均是拟新增任务，不是已经可运行的命令或文件。

| 优先级 | 计划位置 | 交付 |
| --- | --- | --- |
| P0 | selection/ 与 schemas/ | SelectionResult、短ID映射、严格动作验证、固定回退 |
| P0 | retrieval/service.py | 模型展示层、token预算、冻结候选验证，实际执行动作与label一致 |
| P0 | models/、evaluation/ | 真实翻译适配、usage日志、COMET隔离scorer、最小真实QA |
| P0 | training/export_states.py | 分组划分、固定历史与候选导出、参考隔离 |
| P1 | training/sample_teacher.py | 闭源 Teacher 分析＋动作、独立合成译文与质量记录，导出选择器 SFT 示范 |
| P1 | training/sample_actions.py | 只在 SFT 后加载冻结 pi_SFT，自采样 2–4 个动作；外部翻译、缓存、失败与版本记录 |
| P1 | training/build_preferences.py | 验证两侧同源 pi_SFT，合法性/分差/噪声过滤，以通用 prompt 导出完整分析＋动作 DPO pair |
| P1 | training/train_selector.py | SFT/DPO LoRA、SFT reference、checkpoint恢复 |
| P1 | evaluation/selector_benchmark.py | 固定快照与独立run评测，质量/成本分开汇总 |

学生自采样 DPO 样本逻辑字段（Teacher SFT 另行保存 prompt/completion 与合成译文审计）：

```json
{
  "sample_id": "train-doc42-line18",
  "split": "train",
  "state_hash": "...",
  "prompt": "通用策略模板＋冻结 observation；含原文、候选、硬项和预算，无 teacher 类别建议或参考译文",
  "chosen": "{\"rationale\":\"技能术语建议提供 Cleave 的译法‘顺劈斩’，相关说明补充仅限近战使用的条件，可同时约束名称与适用范围。\",\"selected_ids\":[\"c02\",\"c07\"]}",
  "rejected": "{\"rationale\":\"名称目录出现了与原文相近的技能名称，可作为理解当前技能的名称线索。\",\"selected_ids\":[\"c01\"]}",
  "preference_type": "quality",
  "score_chosen": 0.82,
  "score_rejected": 0.79,
  "scorer_version": "固定checkpoint revision",
  "translator_version": "固定checkpoint revision",
  "audit": {
    "observation_hash": "...",
    "policy_prompt_version": "...",
    "chosen_action_origin": "pi_sft",
    "rejected_action_origin": "pi_sft",
    "sampling_policy_version": "冻结的 SFT checkpoint revision"
  }
}
```

示例分数仅说明格式，不是实验结果。DPO 训练器只读取 prompt/chosen/rejected，SFT 训练器只读取通用 prompt/teacher completion；评分、teacher 合成译文与 reference 路径属于隔离审计。按冻结的 LLaMA-Factory 或 TRL 版本导出格式，不混用字段。

上述 audit 也是待实现的轨迹导出字段，不表示现有公共 schema 已扩展；teacher/student 各次生成的实际 prompt、hash、参数、动作和费用单独保留在 selection attempts 中。进入训练器前明确投影为所需输入字段，避免整条审计记录被意外序列化进模型 prompt。

至少保存：split manifest、候选快照、teacher 示范及独立合成译文、SFT 数据、冻结 pi_SFT 标识、学生 rollouts、actual context manifests、selection attempts、translations、checks、scores、preference过滤统计、SFT与DPO adapters、SFT合并基座标识、依赖锁文件、运行计时/显存/用量、评测失败清单。大数据和模型留在artifacts或外部存储，Git只保存代码、配置和摘要。

## 10. 开跑前的决策清单

这份报告给出了默认方案；真正租用GPU前需落实以下实际输入：

- 四张卡的具体型号、同机/跨机、互联、CPU/RAM/磁盘及CUDA驱动；48GB不是吞吐保证。
- 32小时计时口径与单卡费用；建议代码、数据、模型下载提前准备。
- News Commentary En–Zh的实际可用文件、完整文档ID、许可与划分清单。
- 游戏训练参考是否存在；没有时按通用方法验证降级。
- 最小真实QA和人工核验能力，避免用演示回执制造“已验证”记忆。
- 是否有独立Judge资源；没有则保留人工盲评并标注指标缺口。

推荐执行顺序：先补齐状态/记忆隔离和 teacher 示范导出，以训练侧约 128 状态 pilot 冻结 SFT 数据生成配置；完成 SFT 并冻结 pi_SFT 后，再验证学生动作多样性、偏好与调用成本，最后扩量构造 DPO。训练规模由实测门槛决定，评测预算提前保留。
