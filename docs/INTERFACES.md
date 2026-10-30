# 公共数据结构与模块接口

状态：M1 游戏文本已有样例与全量适配契约，其余公共接口待提供方和调用方共同填写。

**作用**：让数据、记忆、检索、选择器、工作流、维护和评测模块使用一致的数据含义，减少并行开发后的集成返工。

后续填写：

- README 中八类公共对象的字段、类型、必填/可空规则和最小示例。
- ID、版本、快照、作用域、unknown 和各类失败状态的语义。
- 各模块输入输出、错误处理、预算和幂等约定。
- 接口版本、兼容性变化及对应代码位置。

本页解释协议，已实现类型的唯一代码定义放在共享 schema；不要在此维护一份与代码独立演化的完整实现。

## M1 游戏导入草案（2026-10-29）

首个《极乐迪斯科》数据 profile 与字段映射见 [数据获取与格式草案 §4](DISCO_ELYSIUM_DATA.md#4-统一格式源侧图与参考侧分离)。区分节点、字段级 occurrence、原始本地化 key、图连接和经核验路径；源侧图与测试参考译文分别存储。该草案尚未在真实导出上验证，不表示八类公共接口已冻结或已有代码实现。

文本范围包括 UI、物品、技能、任务等；统一 occurrence 增加 text_type/text_role、资源命名空间与所属实体/使用位置。对话 node/人物/场景字段按适用性填写，不能作为非对话导入的必填条件；非对话 ID 与分组方式详见同一草案。

## 已实现的 Flare 样例契约：game-text/0.1

共享类型定义在 [game_text.py](../src/localization_agent/schemas/game_text.py)，固定样例适配器在 [flare_sample.py](../src/localization_agent/data/flare_sample.py)。这是已验证的小范围 M1 输入协议，其他七类公共对象尚未实现；后续引擎适配可据真实数据扩展。

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
