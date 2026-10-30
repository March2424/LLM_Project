# 公共数据结构与模块接口

状态：2026-10-30，M1 通用 CSV 与 Flare 数据契约、M2 记忆及基础 M3 图检索接口已实现；其他模块契约待实现时补充。

M2 类型代码定义：[schemas/memory.py](../src/localization_agent/schemas/memory.py)。本页解释边界，完整用法和示例见 [MEMORY.md](MEMORY.md)。

| 对象 | 约定 |
| --- | --- |
| `Occurrence` | 项目内唯一 occurrence_id、文档、0 起位置、非空源文、正整数源版本；元数据未知为 None；默认 en→zh，其他方向显式提供 |
| `Scope` | 项目、可选文档、语言方向、字段→允许值 tuple；未声明字段不限制 |
| `Evidence` | 注册过的 occurrence_id、source_version、源文中存在的逐字 quote |
| `MemoryRef` | record_id + version；跨模块引用不得只保存正文或只保存 ID |
| `ReadView` | run_id + watermark；知识快照从该 run 获取；watermark 是记忆写入序号，不是译文计数 |
| `TranslationCheck` | occurrence/source version、目标 hash、context hash、reviewer、passed；真实检查由 M5 提供 |
| `MemoryRecord` | kind 为 term / summary / translation / style；带 scope、content、evidence、dependencies、状态和可选 run/position |
| `MemoryBundle` | 按类别返回适用记忆及冲突；是候选集合，不是 ContextPack 或实际输入 manifest |

`MemoryRecord.content` 的必需字段：term 为 source/target，summary 为 source/target，style 为 note；translation 由 `commit_translation` 统一产生，包含源译文、检查记录、元数据、知识快照和输入 view。源侧/目标侧摘要正文同存于一条记录，生成时分别调用模型。

写入与读取接口：

- `MemoryStore.put_source()`：相同源版本重复登记需内容一致；新源版本会使旧证据及派生物失效。
- `draft()` → `approve(ref, reviewer)`：显式审批。审批产生新版本；读取批准版本，草稿不进入上下文。
- `create_snapshot()` → `start_run()`：知识固定版本，run 绑定一个项目/文档/方向；同名重复创建会报错，恢复运行时使用已有 ID。
- `view()` → `MemoryService.read(occurrence, view)`：同次任务固定 view；M3/M4 根据候选生成真实输入包。
- `commit_translation(..., check, used=..., view=...)`：检查绑定当前候选，提交时校验版本；`used` 只含实际供给模型的记忆，不能直接塞入所有检索结果。
- `extract_terms(ref)` / `summarize(next_occurrence, view, flush=False)`：返回草稿，外部核验后批准；摘要一次处理至多一个批次。
- `invalidate(ref, reason)`：失效一个版本及递归派生物，保留历史正文供审计。

所有在线目标侧记忆限定于当前 run。测试参考译文不属于本接口；数据适配器必须负责源/参考物理分离，不能把参考伪装成通过检查的译文传入。显式批准和检查回执是可信调用方操作，不是本模块自动完成的语义判定。

错误约定：未知对象 `KeyError`；内容、作用域、模型结构不合法 `ValueError`；预期版本/源版本冲突 `VersionConflict`；缺少所需模型适配器 `RuntimeError`；真实模型异常记录后上抛。调用方应决定重试/人工处理，不统一转成空结果。`summarize()` 返回 None 表示没有达到更新条件，不表示模型调用失败。

尚未实现的公共结构：SelectionResult、TranslationOutcome、UpdateReport。M6 的人工锁、完整事务回执、幂等工作流恢复与导出协议也尚未冻结。


## 图与 M3 接口（2026-10-28）

类型定义见 [schemas/graph.py](../src/localization_agent/schemas/graph.py)，完整约定见 [GRAPH.md](GRAPH.md)。

- GraphRef：node/edge、id、version；不与 MemoryRef 混用。
- GraphNode / GraphEdge：类型、Scope、Evidence、版本及属性；边另有两端精确版本、审批状态。
- RelationType：关系名称、两端节点类别/实体子类型、方向语义及是否遍历；注册定义不可覆盖。
- GraphSnapshot：固定项目图引用；源更新和显式失效仍会阻止使用旧记录。
- CandidatePool：当前 occurrence/源版本、ReadView、图快照、候选、冲突和未解决项。
- Candidate：正文、类别、mandatory、MemoryRef（可选）、源文 Evidence、GraphRef 路径和关系标签。
- ContextPack：实际 selected、状态、manifest、context_hash、characters。字符预算不是 token 预算。
- MemoryService.eligible()：公开读取未经过 top-k 的有效记忆，用于图联查；保留原隔离规则。
- MemoryStore.commit_translation()：新增可选 context_sources/context_manifest。图调用方使用 GraphRetriever.commit() 验证图及输入包；旧 M2 调用保持兼容。

CSV 的 document/scene/block 为结构归属；branch/phase/speaker/addressee/text_type 进入兼容元数据。没有场景时保持未知；技术块不构成语义关系。CSV 工作流使用 GraphRetriever 以启用未知顺序保护，详见 [CSV_IMPORT.md](CSV_IMPORT.md)。


## 2026-10-29：无序游戏模式

新增轻量实体图与五类记忆组织方式；`start_run(..., mode="game_resources")` 启用项目范围，默认文档模式保持原行为。DEFINES 反查、候选链接、主题 STM/LTM 和双语概览的实际接口及限制见 [GAME_MEMORY.md](GAME_MEMORY.md)。

本页解释协议，已实现类型的唯一代码定义放在共享 schema；不要在此维护一份与代码独立演化的完整实现。

## M1 游戏导入草案（2026-10-29）

首个《极乐迪斯科》数据 profile 与字段映射见 [数据获取与格式草案 §4](DISCO_ELYSIUM_DATA.md#4-统一格式源侧图与参考侧分离)。区分节点、字段级 occurrence、原始本地化 key、图连接和经核验路径；源侧图与测试参考译文分别存储。该草案尚未在真实导出上验证，不表示八类公共接口已冻结或已有代码实现。

文本范围包括 UI、物品、技能、任务等；统一 occurrence 增加 text_type/text_role、资源命名空间与所属实体/使用位置。对话 node/人物/场景字段按适用性填写，不能作为非对话导入的必填条件；非对话 ID 与分组方式详见同一草案。

## 已实现的 Flare 样例契约：game-text/0.1

共享类型定义在 [game_text.py](../src/localization_agent/schemas/game_text.py)，固定样例适配器在 [flare_sample.py](../src/localization_agent/data/flare_sample.py)。这是已验证的小范围 M1 输入协议；M2/M3 运行时类型见前文，后续引擎适配可据真实数据扩展。

| 字段/结构 | 语义 |
| --- | --- |
| occurrence_id | 本项目派生的出现位置 ID；不是原始游戏 ID。对话按 NPC 使用资源＋被包含脚本＋块序号＋字段及字段出现序号标识；相同英文不会跨使用位置合并。 |
| native_id / localization_key | native_id 保留原生物品数字 ID 的字符串形式；没有独立原生 ID 时为 null。localization_key 保留英文 gettext msgid；UI 控件原始代码符号另保存在 context。 |
| game_id / resource_namespace | 游戏与命名空间，当前分别为 flare 与 empyrean_campaign/engine。 |
| text_type / text_role | dialogue/utterance、ui/label、item/name、item/description；物品名称和描述共用 entity_id，分别占一条 occurrence。 |
| source_language / source_text | 原文语言与未改写原文。 |
| entity_id / speaker_id | 所属实体与说话人分离；玩家发言属于对应 NPC 对话的使用位置，speaker_id 为 flare:player。人物外观、性格和听话人未经核验不补写。 |
| block_id / sequence_index | 对话块与从 1 开始的块内发言声明顺序；不表示实际游玩顺序。UI/物品不适用时为 null。 |
| scene_id | 当前未解析地图，保留 null；JSON null 在此表示 unknown/未解析，不代表已证明不存在。 |
| source | repository、revision、path、line、field、url；当前文件实际行优先于可能陈旧的 PO 行号引用。 |
| context | 源侧补充信息：INCLUDE 使用位置、主题、条件、UI 原始语句/控件符号、物品原始字段；重复键保留为有序列表，每项带原始文件/行。 |
| blocks.jsonl | 条件、有序全部脚本字段（含效果/奖励/消息）、块内声明相邻边；跨块连接保留 null，runtime_path_status=not_verified。 |
| references.jsonl | 以 occurrence_id 关联的独立中文参考侧；含上游 msgid、msgctxt、flags、目录位置和原始 PO 引用。exact_gettext_key 表示对齐依据，不表示译文质量通过。 |

ID 在固定快照内稳定；脚本插入/重排导致序号改变时，需要版本间 ID 对照，当前未实现自动协调。源侧文件不含中文参考；双语 CSV/XLSX/JSON 为离线人工查看视图，不作为 Translator/检索/选择器输入。尚未冻结正式训练/开发/测试划分。

## 简化本地化契约：localization-text/0.2

按用户要求，本地化文本及必要语境是核心，原始游戏 ID、部署/引擎结构不作为核心必填字段。0.1 样例保留用于历史复现；全量 Flare 使用 0.2。共享类型 `LocalizationText` / `LocalizationReference` 定义在 [game_text.py](../src/localization_agent/schemas/game_text.py)，生产者为 [flare_full.py](../src/localization_agent/data/flare_full.py)。

| 源侧字段 | 语义 |
| --- | --- |
| record_id | 后台关联用的不透明编号，区分相同文字的不同使用位置；不是引擎 ID。固定快照、资源使用位置和同一适配器版本下可复现，不承诺跨资源重排自动保持。 |
| text_type | 本地化类别，如 dialogue、ui、item_name、item_description、skill_name、quest_description、book_text 等；可读标签映射存于 coverage.json。 |
| source_text | 未改写的英文原文。 |
| context | 可定位的源侧本地化语境。按类别保存 module、speaker/character、topic、locations、previous/next、item_name/item_description、item_category、skill_name/skill_description、quest_name/related_quests、screen/ui_section、usage、requirements、placeholders 等适用字段；无依据的属性不补写。 |

参考侧仅以 record_id 关联 reference_text。参考语言、原目录语言、版本、使用边界和文件哈希由 manifest 统一记录。没有中文时不生成空参考记录，而是保存英文与上下文到待翻译视图，并在后台明确标记 blank 或 missing_catalog；不把它们伪装成已有参考。

人工查看的 CSV/XLSX 只有四列：**类型、英文原文、中文参考译文、本地化上下文**。原始 ID、POT/PO key 与 flags、脚本条件、资源名、源行、INCLUDE 使用链、对话原始事件及质量状态保存在 background，不默认送给翻译模型。JSONL 保留关联编号，查看表不承担按身份回写职责；后台行号映射仅适用于原导出顺序。

同块对话保留前后两句源文；书籍前后文只连接同一资源中触发条件相同的段落。locations 是从地图脚本引用取得的**候选地点**；条件脚本保存在后台供作用域过滤，不将它们推断成已核验的游玩路线。related_quests 和物品条件名称来自源文件的同状态/同物品关联，不推测状态变量的剧情含义。

源侧只允许读取 source.jsonl。references.jsonl、中英查看视图、含译文的后台溯源都限于允许的离线参考流程；候选筛选若读取后台，只取源侧 ID/条件，不读其中 catalog.raw_translation。当前尚未实现一个强制访问控制层，后续工作流必须按此边界接入。正式数据划分仍为 unassigned。

## 通用 CSV 导入接口

入口为 [import_localization_csv](../src/localization_agent/data/csv_import.py)，命令行为 [import_localization_csv.py](../scripts/import_localization_csv.py)，不依赖 Flare 或第三方包。返回 `CsvImportResult`，包含输出目录、源侧/参考/缺译条数；输入问题抛出带文件、逻辑记录号及物理行号的 `CsvImportError`，文件读取/目录冲突保留明确异常。

- 表头支持中文四列及 `text_type/source_text/reference_text/context` 英文四列，两种命名可混用；同一字段出现两列时报告歧义，不猜测取舍。类型和原文必填，参考和上下文可缺省；额外列（如原生 ID）只保存到后台。
- 已知中文类型映射到公共 `TEXT_TYPE_LABELS` 的规范类型，Flare 与 CSV 共用此定义；未知自定义类别原样保留，不推断类别。
- UTF-8（含 BOM），逗号分隔，按 CSV 引号规则保留逗号、多行和原文/译文空白。完全空行跳过，列数不一致或必填值为空时报错，不静默丢弃。
- 默认 `context_format="text"`，上下文原样保存为 `context.note`；显式指定 `json` 时接受 JSON 对象，非法 JSON 或非对象报错。不会把参考列加入上下文，也不会语义审核调用方提供的上下文是否包含答案。
- 多文件按传入顺序、每个文件按原始记录顺序导入；不去重、不推测剧情顺序。后台编号依赖 dataset_id、输入序号/文件名、逻辑记录号，不依赖译文；同一布局下只改参考译文不会改变编号。文件重排、改名、行重排后不保证原编号，且不还原 Flare 原始 record_id。
- 全部输入解析通过后，整体发布到新的或空的输出目录；已有非空目录不覆盖。输出 `source.jsonl`、`references.jsonl`、`background/provenance.jsonl` 和 `manifest.json`，沿用 localization-text/0.2；空参考不写成正式参考记录。

source 只含四个核心字段；后台保存原 CSV 文件/行、额外列与参考存在状态。输入哈希、语言、上下文模式及缺译统计记录在 manifest。参考仍为未审定的离线数据；不建立 TM，不自动划分数据集，也不标记译文通过质量验证。

仓库随附的 [Flare 处理后发布目录](../data/flare/v0.2/README.md) 保持相同源侧/参考侧协议。其后台是精简发布变体：保留来源、原生 ID、静态条件和声明顺序，移除原始事件脚本及目录正文，参考只从独立 reference 文件或离线双语视图读取。完整提取产物与精简发布产物分别使用各自 manifest，不要求已发布目录包含原始输入清单。
## M1 与 M2/M3 的接入边界（2026-10-30）

`game_text.py` 中的 M1 导出类型与 `memory.py` 中的运行时 `Occurrence` 分属不同阶段。通用四列 CSV 导入器输出 source/references JSONL；`csv_graph.py` 则按显式源侧列映射直接登记 MemoryStore/GraphStore，参数及格式见 [CSV_IMPORT.md](CSV_IMPORT.md)。两套入口保留各自用途，当前没有从 localization-text/0.2 JSONL 自动写入 M2/M3 的桥接适配器。后续接入需明确 record_id 到 occurrence_id、源版本、项目、作用域和来源的映射；仅可读取源侧资料，不得将参考译文或双语查看表作为批准 TM。
