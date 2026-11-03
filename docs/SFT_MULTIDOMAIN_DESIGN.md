# 多领域、双向、无序资源 SFT 设计

> 后续执行更新：用户已授权对齐并开始Teacher采样。29个已知误分类节点在副本失效；52个单元主题和旧模板已对齐，角色仍需源证据且至少2条。1900目标＋520冷启动已启动，模型训练不启动；以下旧“尚未对齐／不执行”状态为当时记录，当前以[Teacher运行说明](SFT_MULTIDOMAIN_RUNTIME.md)为准。

> 当前核对结果：源图已完成，核心Teacher协议与旧1966条一致；学生模板仅首句领域措辞不同。已统一角色最少2条证据，但新公开单元topic绑定／摘要覆盖尚未对齐，已知图误分类也需采样前处理，详见[图简查与Teacher对齐](MULTIDOMAIN_GRAPH_REVIEW.md)。本次未启动Teacher或SFT。

> 2026-11-03 新目录的实体提取运行前参数、提示词与分阶段入口以[当前预检说明](MULTIDOMAIN_EXTRACTION_PREFLIGHT.md)为准。流水线默认graph-only，Teacher必须显式选择all；本轮不执行API或训练。

日期：2026-11-03。本文记录本轮执行设计，替代旧方案中“公开文档默认只读前序源文”、IWSLT 主评测及必须取得 GuoFeng 官方 Train 的约定。既有实验与运行目录保持原样。

**当前交付范围：代码与Markdown文档，按用户最后指示停止实验执行。已完成语料清点、源文／参考分离、57单元提取dry-run、通用Teacher计划接入和双向运行初始化。此前仅完成三个旧提示词源图小批，均归档；没有继续全量建图、生成公开语料Teacher轨迹或进行新混合SFT。**

## 1. 本轮确定的范围

- 训练：现有游戏选择示范＋News Commentary v18.1 En→Zh、Zh→En＋GuoFeng V1 TEST_2 Zh→En 选择示范。
- 主要评测：既有游戏 test、News Commentary 留出文件的 En→Zh，以及 GuoFeng TEST_2 的训练内评测。
- 不增加 BWB、ACL 60/60 或其他专项诊断。
- 只训练 Qwen3-8B 上下文选择器；固定外部 LLM 负责翻译。双向能力指为两种翻译方向选择上下文，不是把 Qwen3-8B 训练成独立双向翻译器。

用户最新要求只使用 GuoFeng TEST_2，同时用于轨迹训练和评测。因此将其命名为 `adaptation_seen`：训练中见过其原文、候选和 Teacher 选择，评测必须标注 **seen / in-sample**，不能宣称是官方未见 Test 2 泛化结果。参考译文仍只给离线评测器，不能进入 Teacher、源图库或生成记忆。它不再充当本轮模型的独立测试集，也不以其分数选 checkpoint。

游戏 test 和 News Commentary 留出部分保持训练隔离。News Commentary 的同一双语文件必须在同一划分，不能把 En→Zh 测试内容反转成 Zh→En 训练内容。

## 2. 已找到的数据与实际规模

| 数据 | 本地来源 | 本轮用途 | 非空源记录 |
| --- | --- | --- | ---: |
| 现有游戏 | 已冻结的 SFT 1966 条与原始游戏划分 | 保留训练示范；原 test 不变 | 1966 条选择示范 |
| News Commentary En→Zh | `LoongDocMT-main/data/news_commentary_v18.1/en-zh` | 20 个上游文件用于训练 | 1044 |
| News Commentary Zh→En | 同一批双语对反向建立源视图 | 同样 20 个文件用于训练 | 1042 |
| News Commentary En→Zh | 同上，5 个留出文件 | 独立文件留出评测 | 258 |
| GuoFeng TEST_2 Zh→En | `DocMTAgent-main/data/guofeng_v1/TEST_2/test.zh`、`test.en` | 训练适配＋训练内评测 | 869 |

两个上游仓库的根目录均位于本机 `/Users/march/Desktop/LLM Project/`。导入结果为 `outputs/multidomain_sft_2026-11-03/`，包含输入 SHA-256、源记录清单、57 个提取单元及独立 `offline/references.jsonl`。本次准备共记录4个空源位置；2条非空源缺参考仍保留，可用于 SFT，不能用于需要参考的评分。不同方向的空源位置分别计数。

News Commentary 的 `en-zh` 和 `zh-en` 目录内容逐文件相同，只导入一份双语对，再创建两个方向的源视图；不能把它们当成两份独立语料。以 seed=20261103 对上游文件及长字面模板连通组划分，留出 `nc_0、nc_2、nc_3、nc_6、nc_11`，两种语言共同遵守该划分。短通用重复另在评分清单中排除，不合并业务 occurrence。

这些文件是上游预处理单元，目前没有核验原始新闻文章 ID；如实报告“上游文件留出”，不写成已经证明原始文章完全隔离。GuoFeng 以带 BOOK/CHAPTER 标记的两个聚合文件为唯一导入源；`test.zh.N`/`test.en.N` 不再重复导入。保留上游标签，不把标签自动当成已核实的原著书籍身份，亦不根据章节号跨文件归并小说。

## 3. 无序资源模式

**源文没有在线时间顺序；生成记忆仍有提交顺序。** 统一复用 `game_resources` 模式，不要求按照章节、句子或游戏剧情顺序翻译。

1. 当前任务可以检索同一授权单元内全部源文，包括尚未翻译的资源；配置为 `source_visibility=all`。所有对照共享同一源侧范围，报告为完整源资源可见的离线翻译设置。
2. 保留原始文档／章节、行号及 occurrence ID。它们用于定位、作用域、分组和离线文档评分；不因为按无序模式处理而删除这些信息。
3. 处理顺序由固定 seed 决定，属于资源调度顺序，不作为剧情时间、因果、说话轮次或实体关系的证据。不为这些文档新建“上一句／下一句”在线上下文模块。
4. 运行记忆只读取本方法、本阶段、本方向在当前水位前实际生成的译例、软术语和摘要；尚未生成的目标语言内容不可见。
5. 摘要为同单元已处理资源的主题摘要，不能表述成“当前剧情已推进至某处”。STM 是近期提交记录，不声称是相邻句子。LTM、术语和角色笔记仍需来源与作用域匹配。
6. speaker、addressee、branch 等缺失字段保留 unknown；没有足够角色证据就不生成角色笔记，不为覆盖五类记忆编造信息。

这会改变公开文档实验的输入条件：它不是仅见前文的流式文档翻译。可以按原始文档位置还原输出后评分，但不能与仅见前文的论文结果直接归因比较。

## 4. 一次建图，跨阶段和方法复用

“一次建图”指：对选定源记录和固定提取配置只支付一次源侧实体关系提取，再让 SFT、DPO 和评测读取相同版本的源图缓存。**共享的是源侧事实，不共享生成的目标侧记忆。**

为直接复用现有游戏代码，每个公开语料上游单元建立独立 project：

```text
project_id = corpus.split.source_lang-target_lang.unit_id
source cache key = 源CSV哈希 + project/方向 + schema/prompt/model版本 + 提取配置
runtime memory key = project_id + stage + method + run_id
```

本轮公开数据共57个源图分片：News Commentary训练40个、留出评测5个，GuoFeng适配12个。它们由一个清单统一管理，不要求把所有实体强行合并进同一张无作用域大图。现有游戏图继续复用；公开数据不重新提取游戏。两个方向的新闻源文不同，需要分别提取；不能把中英对齐参考当成跨语言实体别名直接写入图。

- 每分片仅包含自身的源文，提取批次与 Existing catalog 不跨 project；防止训练实体的描述、别名或关系在提取时吸收留出内容。
- 实体、边、源记录、引文和派生证据都绑定 project、版本和来源。不得先全库跨划分归并实体，再只过滤返回的 occurrence ID。
- GuoFeng 的 SFT 和 seen 评测可以读取同一个源图快照；这是已声明的数据重用，不把它包装成未见评测。
- 新闻训练分片不包含留出源文；留出 En→Zh 分片绝不导入其中文参考作为源文，也不为留出内容建立反向训练视图。
- 每个方法从只读源图种子复制独立运行数据库，或以只读源图库＋独立运行库读取。SQLite 源图种子不承接任何运行写回；不要求此轮重构为新的图库服务。
- SFT、DPO、各评测方法分别重建运行记忆，不继承 Teacher 的最终 TM，也不共享其他方法生成的译文。
- 读取允许 ID、图遍历、关系证据、实体身份候选和记忆读取都遵守同一作用域；候选涉及多条证据时全部证据都必须可见。

因此，一次建图可以省去重复提取成本，但不能省去各方法自身的记忆生成与运行隔离。合并统计图分片不等于允许跨文章互认人物身份。

## 5. 实体与关系：复用现有协议

沿用 [game-graph/1.1](EXTRACTION_SCHEMA.md)，不为新闻和小说另造一套图系统。

- 人物→`character`，组织→`organization`，地点→`location`，可指称物体→`item`；小说中实际存在的能力、法术和任务可用 `skill`、`quest`。
- 继续使用 MENTIONS、DEFINES、SPOKEN_BY、ADDRESSED_TO、MEMBER_OF、LOCATED_IN、COUNTERS、USES、PART_OF 及既有端点约束。
- 抽象经济概念、事件、方法不硬塞进技能或物品；可保留为源文本证据，后续生成软术语。没有合适关系就不造边。
- 同名仅作为候选线索，不按名称自动合并。合理局部推断保留 `basis=inferred` 和 `reason`；相邻行、同批次和处理先后不能作为关系依据。
- 四列提取输入为 `id,source_text,text_type,context`。公开数据默认 `text_type=unknown, context=""`，不把参考译文、双语标题或对齐目标放入 context。
- 逐项程序检查和 `program_only` 标记沿用既有机制，不增加第二轮全量模型审查。保留失败与被拒候选，程序准入不等于人工事实核验。

## 6. Teacher 轨迹与数据配比

初始规模采用现有1966条游戏＋新闻600条 En→Zh＋新闻600条 Zh→En＋GuoFeng约700条 Zh→En，合计约3866条有效选择示范。约66%为 En→Zh、34%为 Zh→En。这是本轮工程起点，不能称为验证过的最优比例；不通过复制记录或放宽质量要求凑数。

每个公开语料单元在本阶段只建一个持续 run，按固定随机顺序先处理10条冷启动，其余为候选目标。每个文件／章节被当作一份独立游戏资源包，因此文件间不会混用人物和事实；这是为了复用现有 project 隔离的明确选择。12个 GuoFeng 单元共869条，扣除120条冷启动最多749条候选，700条是采样目标而非成功保证。40个新闻训练分片合计400条冷启动，两个方向分别保留足够候选采样约600条。公开数据共520条冷启动，不进入SFT标签。

原游戏轨迹仍保留“每游戏一次10条冷启动”的历史，不为了新语料重新冷启动、重新生成或重标旧记录。现有1966条中336条为继承固定基线历史、1630条按Teacher选择推进历史；混合训练保留此来源标记，不能改写成全量新协议。

单目标流程固定为：

```text
冻结 ReadView／源图版本 → 检索16项软候选＋固定硬约束
→ Teacher 输出 rationale＋selected_ids
→ 校验 ID、最多8项、1024 soft tokens
→ 外部 Translator 使用实际选中 ContextPack 翻译
→ 程序检查 → 仅向本 run 写回 unreviewed 译例和衍生记忆
```

Teacher 所选 ContextPack 必须驱动该条翻译；同条不能并行“选择”和“翻译”。选择失败不回退后写入历史；翻译／记忆失败与选择有效性分别记录。不同单元可以并发，同单元必须串行更新。所有阶段沿用成功响应缓存、费用账本、逐条持久化和不确定请求人工处理机制。

SFT 输入为通用学生 prompt、原文、显式语言方向、任务元数据、预算、硬约束及候选正文／ID；标签仅为完整 `rationale + selected_ids`。当前译文、参考译文、Teacher 专属类别提示和评分都不进入学生输入或标签。分析不作为事实交给 Translator，也不写入记忆。没有类别配额、长分析奖励或强制非空选择。

导出保留每条数据的 corpus、split、direction、project、源图哈希、ReadView、证据展开、提示词／模型版本、选择检查和记忆状态。只导出允许训练的单元；GuoFeng 带 `seen_evaluation_overlap=true`，游戏与新闻测试记录明确拒绝进入 SFT。

## 7. 混合 SFT 与96GB参数

既有游戏 SFT 最终模型保留为历史基线。本轮正式混合模型从同一原始 Qwen3-8B 重新初始化 LoRA，完整混合数据固定2 epoch；不把在游戏 SFT 上追加训练伪称为同等训练预算的混合对照。

| 参数 | 本轮起点 |
| --- | --- |
| 框架／模型 | LLaMA-Factory 0.9.5，Qwen3-8B |
| 模板／监督 | `qwen3_nothink`，completion-only；完整理由与ID参与loss |
| 微调 | BF16，LoRA all linear，r16 / alpha32 / dropout0.05 |
| 长度 | cutoff4096，动态padding，不packing；新增全量真实模板预检后确定 |
| 单卡batch／累积 | 8×2，有效batch16，梯度检查点开，SDPA，use_cache=false |
| 优化器／LR | fused AdamW，1e-4，cosine，warmup_ratio0.05 |
| 计划 | 固定2 epoch，无dev、无early stopping、采用最终E2 |
| 保存 | 每25 updates、epoch末及优雅停止；保留最近3份完整状态 |
| 监控 | SwanLab独立新run；实时训练指标与阶段汇总分组 |

此前PRO6000实测8×2可运行，最长旧样本3862 tokens，峰值allocated约77.3GiB；16×1开检查点、4×4关检查点均OOM。这些仅是旧数据结果。中英混合数据完整 tokenization 和最长样本的少量实际训练步通过后才能沿用batch；若4096不足，先处理真实长度，再重新测显存，不静默裁掉候选或标签。

约3866条时，Trainer预期约484个optimizer updates，warmup约25步；以实际有效导出条数和Trainer结果为准。新混合训练用新输出目录、新optimizer/scheduler和新SwanLab run；旧实验的checkpoint、冻结数据和run指纹都不覆盖。

同一混合训练的断点必须恢复adapter、optimizer moments、scheduler、global_step、RNG、Trainer数据跳过状态；LR与保存值一致，不能再次warmup或追加两轮。沿用 [run_selector_sft.py](../scripts/run_selector_sft.py) 的完整状态标记与恢复校验。参数和输出配置落实前不直接复用指向旧输出的96GB配置启动。

## 8. 评测和后续DPO

| 结果 | 角色 | 输入范围与报告 |
| --- | --- | --- |
| 游戏test | 独立测试 | 原有资源分组，源文仅限对应授权test范围，记忆各方法独立 |
| News Commentary En→Zh留出文件 | 独立文件留出 | 全部本单元英文源文；无中文参考在线可见；原始文章身份未核验须注明 |
| GuoFeng TEST_2 Zh→En | 训练内评测 | 全部本章节中文源文；单列seen结果，不与独立测试汇总 |

所有方法固定候选配置、Translator、提示词、预算、源图版本和处理顺序。评测从空运行记忆开始；如使用10条热身，从正式评分中排除且各方法一致。缺参考或训练重复的新闻目标从质量评分清单排除，但保留失败／排除计数。不同语料与方向分别报告sCOMET、完整率、成本和适用的文档指标；不合成掩盖方向差异的总分。

保留上游单元及原始位置，输出还原位置后交真实篇章上下文评分器；dCOMET不能用句级均分代替。未核验真实文章／书籍边界时，分数标为上游单元上下文评分，不能宣称完整原著篇章性能。LLM Judge与在线生成／记忆更新分离；GuoFeng seen分数不用于调参数或选择模型。

混合SFT结束后固定其E2为新的pi_SFT。DPO必须由该模型重新自采样，不能直接沿用旧游戏pi_SFT的偏好对；初始化和reference都使用新pi_SFT。仍采用同状态2–4个学生动作、固定翻译环境、比较期间只读记忆、固定基线仅写回一次。

主DPO的参考式奖励先只用游戏train和News Commentary train的可用参考。GuoFeng TEST_2参考继续限定离线seen评测，不自动转成COMET训练奖励；如以后要把它的参考也用于偏好学习，需要单独更新数据用途，结果仍必须标为训练内。此处不新增PPO/GRPO或其他在线RL。

## 9. 已执行准备与接下来执行顺序

已增加 [prepare_multidomain_sources.py](../scripts/prepare_multidomain_sources.py)，仅用Python标准库：

```bash
python3 scripts/prepare_multidomain_sources.py \
  --news '/Users/march/Desktop/LLM Project/LoongDocMT-main/data/news_commentary_v18.1' \
  --guofeng '/Users/march/Desktop/LLM Project/DocMTAgent-main/data/guofeng_v1/TEST_2' \
  --output outputs/multidomain_sft_2026-11-03
```

该命令已实际完成，原目录已存在时不要重复执行。源侧CSV位于`source/`；`sources_manifest.jsonl`、`extraction_units.jsonl`和`summary.json`不含参考正文；参考只在`offline/references.jsonl`，不得将该目录加入提取／Teacher的可读输入清单。准备器不是Teacher采样计划生成器，不能把其输出直接传给现有`generate_teacher_trajectories.py prepare`。

后续顺序：

1. 对57个单元用现有`run_benchmark_agent.py --dry-run`检查四列输入和批次，零API调用。固定分片输入、模型配置、提取缓存键和本轮费用上限。
2. 复用该入口的显式`--source-lang`、`--target-lang`逐单元建图；留出图仅源侧提取，不生成Teacher标签。每次完成后导出并冻结源图种子。
3. 通用Teacher计划与双向初始化已接通：`prepare_multidomain_teacher.py`生成`source_projects.json`，`selection/teacher.py`读取显式源图路径，collector从已登记Occurrence读取语言方向初始化run；检查点保留corpus、原划分和seen标记。新的Zh→En运行／恢复离线验证和原有5项运行验证均通过；真实双向轨迹仍需后续实际采样确认。project级隔离复用既有检索和记忆机制，无需改公共图schema。
4. 先用训练侧少量真实记录核对方向、Teacher所选上下文、写回来源、费用与恢复，再按同一配置扩量。只抽查关键语义和既有相关检查，不增加重复的大型测试体系。
5. 合并有效示范，记录实际来源配比，完成真实模板全量长度／loss检查；使用独立混合配置和输出启动两轮SFT。
6. 冻结混合pi_SFT，再分别做评测和DPO采样。

用户此前表示无API费用上限，代码可配置`max_cost=null`并保留调用账本；随后明确本次只完成代码和文档，**不执行建图或SFT**。因此不继续使用此前执行授权。流水线入口为`run_multidomain_collection.py`，默认只dry-run；未来另行决定执行时必须显式提供`--execute`，检查三个源图小批后才用`--continue-after-pilot`扩量。全图完成后才生成1900目标＋520冷启动的Teacher计划；没有自动GPU训练步骤。

小批发现旧“game localization”提示词在一份中文新闻中返回空图，而源文有明确人物和地点；原因尚未由重提取确认。已将实体提取、学生选择、翻译和软术语提示的领域措辞改为通用资源／领域表述，实体关系schema不变。三个旧提示词分片及全部调用记录保存在`pilot_game_prompt_graphs/`，4次调用估算费用合计0.0194304 USD；`archived_graph_usage.json`保留累计支出。用户停止后没有用新提示词再次付费验证。

六项Teacher运行验证及九项既有自动建图检查通过；这不代表新通用提示词的真实提取效果已确认。后续混合导出时，旧游戏记录的学生输入首句需要按新通用模板从已存观察重建，并记录转换来源，不改rationale、动作或历史原始记录。服务器文档镜像与停止状态见 [SFT_MULTIDOMAIN_HANDOFF.md](SFT_MULTIDOMAIN_HANDOFF.md)。

## 10. 依据

- 项目接口：[实体提取协议](EXTRACTION_SCHEMA.md)、[记忆](GAME_MEMORY.md)、[Teacher流程](TEACHER_TRAJECTORIES.md)、[训练协议](TRAINING.md)。本文的新数据／无序／seen评测约定优先于它们的历史范围描述。
- [GuoFeng官方说明](https://github.com/longyuewangdcu/GuoFeng-Webnovel)：V1为Zh→En对齐语料。当前本地TEST_2用途及大小以本轮清单为准。
- [WMT数据目录](https://www2.statmt.org/wmt26/mtdata/)与[MTData的Statmt注册实现](https://github.com/thammegowda/mtdata/blob/main/mtdata/index/statmt.py)：News Commentary v18.1版本来源。它们不证明本地预处理文件已经保留原始文章边界。
