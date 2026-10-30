# 《极乐迪斯科》数据获取与统一格式草案

核查日期：2026-10-29。范围：PC《Disco Elysium: The Final Cut》的中英本地化文本，包括对话/旁白、UI、物品、技能、任务、人物/地点名称与游戏特有文本；对话另保留人物与分支。

状态：完成公开说明和提取代码阅读；用户暂未安装游戏。本仓库尚未解包、下载全量社区数据库或验证实际双语对齐；下列字段是首版设计草案，不是已实现接口。游戏是否用于最终评测、数据来源与具体版本仍待样本核验。

## 1. 可直接查看的现成数据库

| 来源 | 本次确认 | 尚未确认 | 建议用途 |
| --- | --- | --- | --- |
| [Disco Elysium Scribe](https://disco-elysium-scribe.pages.dev/) | 站点说明支持中英文台词、人物、条件/脚本搜索与对话图；可显示 conversation/entry 标识 | 全量原始导出、公开 API、对应发行版本、中译完整性 | 先查看样本与图结构，核对需要的语境 |
| [Disco Elysium Wiki 中文对白](https://discoelysium.wiki/zh-cn/对白)与[英文对白](https://discoelysium.wiki/dialogue) | 两个语言的对白目录均可读取，包含人物、地点和条数；英文详情包含前序与回复链接 | 本次中文详情页抓取未成功；完整条件、稳定逐节点双语映射、全量导出均未确认 | 双语与人物/地点的辅助检查 |
| [elysia](https://github.com/arda-y/elysia) | 仓库含约 23.7 MB 的 `db/DiscoElysium.db`；说明列出 actors、dialogues、dentries、dlinks、checks、modifiers、alternates；有数据库下载接口设计 | 中文资源、完整性、与当前游戏版本一致性 | 检查现成结构化英文图数据是否可作为输入 |
| [Disco API](https://github.com/msyavuz/disco-api) | 文档提供角色与台词 API，并说明附带 `disco.db`；返回 actor、conversant、conversationid | 中文、分支 API、当前服务可用性 | 角色/台词接口参考 |
| [Disco Elysium Explorer](https://github.com/Leerion/discoElysiumExplorer) | README 说明 SQL 迁移包含数据；本次目录核对存在 actors、conversations、dialogues、links SQL | 虽有 translations 模型，未找到对应翻译 SQL，不能据此认定提供中文 | 旧版图数据和结构参考 |

elysia 的 README 明确将原始数据库标为第三方、日期 2021-03-29 的导出，且其运行时派生库会修正连接和部分条目。这是来源自己的说明，未与游戏独立核实。若使用，应同时记录原库哈希与派生修正，不能当作当前版本原始解包结果。

[FAYDE](https://fayde.co.uk/info)是进一步核查候选；本轮访问返回 403，未直接验证其双语与下载能力，不作为已经核实的数据入口。

在线展示证明有可查看内容，不等于已取得完整、可复现的研究数据。公开工具代码的许可证也不能自动替代游戏正文的使用条件。首版可以先检查公开快照的结构与来源，优先采用可定位的原始导出，避免从网页显示顺序反推剧情。

### 1.1 全类型文本覆盖清单

用户已明确需要 UI、物品名等非对话文本。前述阅读器主要用于对话核查，不能代表完整游戏本地化库。[Wiki 中文首页](https://discoelysium.wiki/zh-cn/)另列有物品、技能、任务、思维、地点和成就等入口；本轮部分中文子页抓取未成功，尚未验证这些类别的完整双语内容，也未确认完整菜单、设置或错误消息库。

| 目标类别 `text_type` | 示例 `text_role` | 需要的语境 | 获取状态 |
| --- | --- | --- | --- |
| dialogue / narration | utterance、option、tooltip | 人物、原节点、图连接与条件 | 有提取代码和在线展示依据；未实际提取 |
| ui | label、message、tooltip | 菜单/面板/控件用途、占位符；长度限制有证据才记录 | 已找到通用本地化资源路线；具体覆盖待核对 |
| item | name、description | 物品实体、物品类别、相关源侧说明 | DialogueDatabase 有 items；各文本字段与中文 key 待核对 |
| skill / stat / status | name、description、tooltip | 对应实体与机制、参数 | 数据库/资源与技能网页作为查找入口，完整性待核对 |
| quest / journal | name、body、objective | 任务实体、阶段、条件 | 可检查 conversation 的任务字段和通用资源；映射待核对 |
| character / location | name、description | 实体 ID、别名及来源 | 角色表、名称资源、场景资源分开核对 |
| thought / achievement / tutorial / lore | name、description、body、message | 对应系统、实体、条件 | 全部在目标清单；来源和字段待核对 |
| unknown | unknown | 原始资源位置与 key | 尚未分类的可见文本保留，不静默丢弃 |

这是目标覆盖表，不是对已取得数据的声明。编辑器内部标题、调试字段和数值参数不因出现在 dump 中就变成待译文本。图片中可见文字另记为资源覆盖项；尚未提取与对齐前，不混入普通文本条数。

数据来源可以组合为：对话数据库提供图与节点，通用/名称本地化资源提供 UI 等字符串，实体和任务资源提供分类与关联，网站辅助检查。各来源保留独立版本与命名空间，验证映射后才合并。

## 2. 自行提取的具体对象

需要同时盘点英文对话/实体数据库、英文及中文的通用/名称等本地化资源，以及必要的任务/场景资源。仅取得英文对话数据库和中文对白包不能作为全类型交付。

### 2.1 英文与对话图

EMNLP 2023 研究作者[公开了 AssetStudio 操作步骤](https://pl.aiwright.dev/docs/getting-started/dataset/)：加载游戏资源，定位名为 `Disco Elysium` 的大资源，导出为 `Disco Elysium.json`。作者使用 Windows Steam 版；其 Mac 截图是在 Parallels 中运行 Windows 工具，不是原生 Mac 路线的验证。[论文 §3.1](https://aclanthology.org/2023.findings-emnlp.151/)报告提取了人物、物品、对话、节点和游戏变量。

[捷克语本地化项目 README](https://github.com/prekladyher/disco-preklad/blob/7e568aebe0088d7f02aeb8b32e50a49af4abea81/README.md)记录的 Windows 查找模式：

```text
<游戏数据目录>/StreamingAssets/aa/StandaloneWindows64/dialoguebundle_assets_all*
    → DialogueDatabase，资源显示名可能为 Disco Elysium
```

不要写死哈希后缀；目录、平台和导出工具决定实际名称。导出后先确认具有 `actors`、`conversations`、`dialogueEntries`，不要只依据文件名接受结果。

作者路线的操作次序（尚未在本机游戏验证）：

1. 在独立输出目录保存提取结果，并记录游戏版本/平台、原包哈希和工具版本。
2. AssetStudio 使用 `File > Load Folder` 加载包含该 bundle 的目录；必要时按作者步骤扩大到游戏资源目录。
3. 在 Asset List 查找显示名 `Disco Elysium` 的资源，选择 `Export selected assets`。
4. 如只能得到原始 MonoBehaviour 字节，转入 UABEA 导出和版本匹配的类型解析；不要将二进制改名为 JSON。

[AssetStudio v0.16.47](https://github.com/Perfare/AssetStudio/releases/tag/v0.16.47)是本轮查到的原项目最新发布，项目已归档；[UABEA v8](https://github.com/nesrak1/UABEA/releases/tag/v8)可作为进一步检查候选。这些是已核查的上游版本，尚不是本项目验证通过的环境。

### 2.2 中文本地化

同一捷克语项目记录的非英语资源查找模式：

```text
<游戏数据目录>/StreamingAssets/AssetBundles/Windows/lockits/<language>
    → DialogueLockit<Language> / LanguageSourceAsset
```

对白以外，`GeneralLockit*` 对应通用文本；英文通用资源在该项目记录中来自 `resources.assets`。人物名称的本地化需另行检查，不能假设对白资源涵盖一切。

进一步阅读同一提交的 [l10n/main.js](https://github.com/prekladyher/disco-preklad/blob/7e568aebe0088d7f02aeb8b32e50a49af4abea81/scripts/l10n/main.js) 和 [build.sh](https://github.com/prekladyher/disco-preklad/blob/7e568aebe0088d7f02aeb8b32e50a49af4abea81/build.sh)，可确认该项目将 `General`、`Dialogues`、`CollageMode` 等作为不同类别处理。类别名证明存在非对话文本路线，但不能据此断言 `General` 覆盖全部 UI、物品、技能或任务。提取时先列出实际全部 LanguageSourceAsset，再按 key/引用定位用途；不要仅扫描 DialogueLockit。

UABEA 导出得到的资源可能仍是原始 `.dat`，需解码为 `LanguageSourceAsset`。本次阅读的[上游 schema](https://github.com/prekladyher/disco-preklad/blob/7e568aebe0088d7f02aeb8b32e50a49af4abea81/scripts/game/schema.js)包含：

- `mSource.mLanguages[]`：`Name`、`Code`、`Flags`。
- `mSource.mTerms[]`：`Term`、`Languages[]`、`Flags` 等。

每个资源都根据自己的语言表确定文本数组索引，记录原始语言名称和 code。先区分简体与繁体，再映射实验语言标识；不能照抄某份脚本把所有资源的 `Languages[0]` 当中文，也不能用英文回退值补成中文参考。

### 2.3 双语连接键

两份独立实现都使用以下规则连接：

```text
对话节点 fields["Articy Id"] 与待译字段名
    → localization_key = 字段名 + "/" + Articy Id
    → 中文资源 mTerms[].Term
```

依据：[捷克语项目 extract.js](https://github.com/prekladyher/disco-preklad/blob/7e568aebe0088d7f02aeb8b32e50a49af4abea81/scripts/l10n/extract.js)、[旧版 CatalogExporter](https://github.com/Bandris86/DiscoTranslator/blob/e728831ba908bfaffbfdc78b5c091c55da4d8942/DiscoTranslator/Translation/CatalogExporter.cs)。上游列出的待译字段含 `Dialogue Text`、`Alternate1`–`Alternate4`、`tooltip1`–`tooltip10`；不能只导出主台词而静默丢弃变体。

此规则已有代码依据，但必须用实际版本核查命中率、一对多冲突、空译文和源文变化。没有 Articy ID 时保留缺失，不自动按文字相似度配对。不同社区库的数字 ID 或合成 key 也不能直接视为此原始 key，需分别核对命名空间和映射。

上述“字段名/Articy ID”是对话字段的已知规则，不强加给 UI 等资源。通用字符串先按对应资源命名空间中的原始 `Term` 在英文、中文间连接；跨资源同名 key 不自动合并。物品、任务等文本若需要由对象字段生成 key，须通过实际引用核实并记录映射规则，不能套用未经验证的对话规则。

### 2.4 运行时导出备选

[DiscoTranslatorFinalCut](https://github.com/Gianxs/DiscoTranslatorFinalCut/blob/6df69e72d3eaf5d7d01dd1d0d39c90bf7f79872c/README.md)提供 Windows/IL2CPP 的 PO 导出路线，可参考但不作为首选依赖。阅读其[ExporterManager](https://github.com/Gianxs/DiscoTranslatorFinalCut/blob/6df69e72d3eaf5d7d01dd1d0d39c90bf7f79872c/Translator/Exporter/ExporterManager.cs)发现：

- 它遍历当前已加载 `LanguageSourceAsset`，并不证明覆盖未加载语言。
- 多语言循环写相同的 `fullName + ".po"`，可能相互覆盖。
- 语言文本写入 PO 的 source 一侧，不能直接假定 `msgid=英文、msgstr=中文`。
- PO 不保存完整 outgoingLinks、条件和状态脚本。

所以即使采用 Mod 导出，仍需分别标记语言、保留 key，并另外获取原始对话图。旧版 BepInEx Mod 的存在不代表与当前最终剪辑版兼容。

## 3. 哪些语境能够拿到

以下字段来自已阅读的上游二进制 schema 与导出逻辑；实际文件仍需确认。

| 需求 | 原始字段/对象 | 语义与限制 |
| --- | --- | --- |
| 说话人 | 节点 `fields.Actor` → `actors[].id` | actors 也包含技能、物体等；保留原始 actor 声明，动态替换不凭静态文件断言 |
| 听话对象 | `fields.Conversant`，必要时检查 conversation 的参与者字段 | 不等于“除说话人之外的另一个角色”；缺失/动态绑定保留 unknown |
| 节点身份 | conversation `id`、entry `id`、`Articy Id` | entry ID 不能假定全局唯一；字段变体必须单独标识 |
| 前后连接 | `outgoingLinks` 的 origin/destination conversation/entry ID | 可跨 conversation，保留环与共享节点 |
| 条件 | `conditionsString` | Lua 源表达式可保存；未求值不等于 true |
| 状态变化 | `userScript`、`onExecute`、数据库 `globalUserScript`、variables | 前者可保存源码；Unity 事件/自定义函数等可能依赖游戏环境 |
| 路由控制 | `isRoot`、`isGroup`、`conditionPriority`、`falseConditionAction`；link `priority`、`isConnector` | 无正文的路由节点也要保存，不能删除后把上下游无条件直连 |
| 场景/地点 | conversation 标题/描述，必要时关联场景资源 | 不把 conversation 与 Scene 强行一一对应；从标题推测的地点标为派生，未经核对不作为确定作用域 |
| 变体选择 | `Alternate*`、tooltip 和其他原始字段 | 可提取文字；触发逻辑须进一步核对，不将多个变体拼成一条台词 |

对话顺序是图上的路径，没有可从节点排序直接恢复的唯一全局时间线。[Pixel Crushers 的执行顺序说明](https://forum.pixelcrushers.com/post/howto-conversation-execution-order-13716723)还涉及链接优先级、节点条件、脚本与事件。它用于解释底层机制，不替代对游戏自定义逻辑的验证。

可保留分支图不等于已复现游戏运行：没有初始状态、检定结果、外部事件和自定义函数实现时，只能报告静态连接。不要枚举所有图路径后标为合法游玩记录。

## 4. 统一格式：源侧图与参考侧分离

首版建议使用 UTF-8 JSONL 保存结构，CSV 仅作离线人工核对导出。该建议不依赖最终采用自行解包还是社区快照。

| 文件 | 最小字段与职责 |
| --- | --- |
| `manifest.json` | schema/profile 版本、game_id、发行版本或 unknown、来源 URL/提交、下载时间、输入/输出哈希、工具版本、source_lang/target_lang、处理配置、计数与失败、split 清单、源侧可见范围 |
| `entities.jsonl` | entity_id、原始对象 ID、entity_type、英文名称及源侧资料、来源；覆盖角色、物品、技能、任务等，中文名称/说明另放参考侧 |
| `conversations.jsonl` | conversation_id、原始 ID、源侧标题/描述、根节点、已证实场景/unknown、必要的执行设置、来源 |
| `nodes.jsonl` | node_id、conversation_id、entry_id、Articy ID、根/组标识、Lua 条件/脚本、路由控制、原始位置；包括没有可译正文的节点 |
| `occurrences.jsonl` | occurrence_id、string_id/原始 key、resource_namespace、field_name、英文 source、source_version/hash、text_type、text_role、owner_entity_id、usage_locator、来源；node_id、speaker_id、addressee_id、scene_id、block_id 只在适用时填写 |
| `edges.jsonl` | edge_id、from_node_id、to_node_id、原始 link 顺序/priority/isConnector、来源；条件仍属于原节点，不随意搬到边上改变语义 |
| `references.jsonl` | occurrence_id（或 entity_id）、target_lang、reference_text、原始 key、输入快照、alignment_status、quality_status、来源；只供离线评测/获准的独立训练部分 |
| `paths.jsonl` | path_id、完整 node/edge 步骤与有序 occurrence 清单、变体选择、已知初始状态/unknown、状态来源、核验依据/状态、split、覆盖率；用于顺序上下文与篇章评分 |

其中 `entities` 到 `edges` 属于源侧视图；不要把混合语言的原始 dump 或整份 `mTerms` 直接作为“源侧附加字段”传给模型。原始资源保存在独立 ingestion 区。缺字段使用 `null` 表示 unknown，不能将未知条件状态写为 true，或将未知全局分支写为 `main`。

所有文本类别共用 occurrence 与 reference 结构，对话图是附加语境。物品的名称、说明分别为独立 occurrence，通过同一个 owner_entity_id 关联；UI 按资源/控件出现位置定位。非对话记录不要求人物、scene、node 或 path，也不自动分配虚构剧情顺序。可空字段需配合 text_type 区分“不适用”和“应有但未知”；有待补全的适用语境在解析报告中记录。

`text_type` 表示业务类别，`text_role` 表示文本用途，两者分开，例如 item/name、item/description、ui/label。分类依据资源引用或已核对规则；仅凭 key 猜测时标为推断/待核对。源侧名称—说明关联可以供模型使用，测试集中文名称/说明不能因此进入输入。

### 4.1 ID、变体和版本

- 原始命名空间应进入 manifest：同一个 `id=123` 在两个网站不一定代表同一节点。
- 对自行解包的源，`node_id` 可以取 `de:conv:<conversationID>:entry:<entryID>`；保留 Articy ID 用于对照和版本迁移。
- `occurrence_id = node_id + ":field:" + field_name`，对字段名作无歧义编码。`Dialogue Text`、`Alternate1`、tooltip 各自保存；源文更新使用新 source_version/hash，不靠文本内容重建身份。
- `string_id` 保留原始本地化 key，与 occurrence 身份分开。相同英文不做业务去重；同一 key 被多个位置引用时保留全部映射。
- 同一节点在路径中反复出现，用 path step_index 区分访问次序，不覆盖基础 occurrence。状态导致表现差异时记录状态和变体，不将不同状态的历史混用。
- Articy ID 缺失、重复 key、多译文或跨版本迁移不清楚时返回冲突/未对齐，不静默覆盖。
- 非对话 occurrence 使用资源命名空间、稳定 owner/usage 标识和字段名构造 ID，不依赖 node_id 或文本哈希。若语言表只保留 key、无法恢复所有控件使用位置，以资源条目为暂时可追溯单元，并记录 `occurrence_resolution=resource_entry_only`，不能宣称已恢复全部使用位置。

### 4.2 顺序与评测

- `block_id` 依据可解释的局部交流结构建立；原始 conversation 可以作为文档分组起点，但不自动等于一个线性 block 或地理场景。
- 分支路径优先来源于真实记录或人工按源侧逻辑核验。只有图连接成立的候选路径标 `structural_only`，不用于宣称完整游戏可达的主篇章结果；`path_status` 区分 `structural_only`、`verified`、`unresolved`。
- 首版保留原始 Lua，不要求重写完整游戏模拟器。无法核验的路径单独报告，不能按默认空状态执行后宣称合法。
- dCOMET 在冻结且经核验的路径/文档边界内评分，不混合互斥选项。节点级 sCOMET 可以独立报告，不以句均分冒充 dCOMET。
- train/dev/test 先按 conversation/任务线分组，再检查跨 conversation 边、共享 key/节点和重复正文。发生共享时合并分组或显式排除共享项；最终是否可实现所需比例依实际图决定。用户当前仅选取数据研究目标，未冻结训练用途与比例。
- 路径间复用节点要同时报告唯一 occurrence 数与路径访问次数，避免重复访问使分数和样本规模虚增。
- 在线提取、记忆、检索、选择、翻译和审校不读取测试中文参考，也不使用其推导的中文角色 note。参考译文质量默认为 `unreviewed`，来源为游戏中文不自动变成已核验金标准。
- 非对话按实体、任务线或 UI 面板等有依据的组划分；名称与说明及共享 key 不能随意拆到不同集合。UI、物品等分别报告质量与约束指标，不把无篇章关系的字符串拼成文档计算 dCOMET。

### 4.3 CSV 核对视图

离线可导出 `occurrence_id, text_type, text_role, owner_entity_id, resource_namespace, string_id, field_name, source, reference, alignment_status, quality_status`；对话附加 conversation_id、entry_id、speaker_id。条件、边、路径等继续以 JSONL 为准。源侧运行输入另外导出不含 reference 的视图，不能把这一双语核对表直接交给在线系统。

## 5. 下一步及最小验收

先选能取得结构化数据的来源组合：对话检查 2–3 个 conversation，覆盖多人物、一个分支/检定、一个条件/脚本节点以及至少一个变体（若样本中存在）；另外至少核对一组 UI 标签/提示、一个物品名称—说明对、一个技能名称—说明对和一个任务标题—目标对。某类资源尚未取得时标记 pending，不以对白样例替代。

1. 记录来源快照与 schema；每类有可追溯的中英对应和原始连接键才进入双语样例；actor/node 只检查适用的对话记录。
2. 核对 source key 到中文 key 的命中和冲突，检查简/繁语言来源与空值回退。
3. 核对节点引用、角色引用和原始路由，不静默丢弃空正文节点。
4. 人工核对一小段中英内容和一条路径；覆盖不足明确记录，不扩大解释成全量通过。
5. 按 text_type 分别报告原文数、中文匹配数、缺译/冲突/待分类数与未知覆盖范围；对话另报完整 conversation 与经核验路径数。取得对白不能作为“全游戏文本已提取”的验收条件。

若先用在线数据库，正式样本前仍需核对数据版本、来源和导出覆盖。若拿不到结构化中文或原始条件，可把网站作为核对入口，自行提取作为补足路线；目前没有证据证明需要先购买游戏才能开展结构设计，也没有证据证明现成网站已经满足全部实验输入。

## 6. 本次验证与复用边界

已做：读取作者提取教程、论文相关段落、三份提取/翻译工具代码、社区库说明与目录，以及在线数据库的公开页面。未做：本机游戏解包、社区数据库全量下载、实际双语 join、数据规模/翻译质量评估、可达路径重放。

本次只记录字段和规则，没有移植上游实现。捷克语项目本次仓库元数据未给出许可证；不能未经确认直接复制其二进制解码器。两份 DiscoTranslator 代码仅作行为核对，若后续复用需登记对应提交、许可证与适配内容。
