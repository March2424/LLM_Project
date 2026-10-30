# 可下载的中英游戏文本：来源与覆盖核查

日期：2026-10-29。目标：优先使用现成文件取得对话、UI、物品名与说明，检查缺口后再决定是否解包。范围依据 [文件解析需求](File%20Parsing%20Requirements.md)。

## 1. 结论与实际工作

已找到可以直接下载、覆盖上述四类的来源：Flare 和《大灾变：黑暗之日》。另找到商业 RPG《永恒之柱》的中英文本仓库，覆盖广，但中文是社区汉化，仓库又包含 AI 辅助修订层，须分开看待。

本轮实际下载 59 个公开文本/JSON 文件，共 44,232,361 字节，未运行第三方程序，未解包游戏，未调用模型。核对了下载哈希、5 对 PO/POT 目录、23 对 XML 表，以及 CDDA 0.G 的游戏数据和中文目录。下面的覆盖数字来自文件统计，不是网页宣传或译文质量评测。

本地证据在 `artifacts/data_source_audit_2026-10-29/`：`download_manifest.json` 保存来源提交、原始路径、URL、大小和 SHA-256；`coverage_summary.json` 保存统计；`audit_scope.json` 保存版本与抽样范围；`raw/` 保存已下载文件。该目录受现有 `.gitignore` 保护，原文与参考译文不加入版本管理。

## 2. 候选比较

| 来源 | 对话 | UI | 物品名称/说明 | 数据形态 | 本轮判断 |
| --- | --- | --- | --- | --- | --- |
| [Flare 官方游戏仓库](https://github.com/flareteam/flare-game) + [引擎仓库](https://github.com/flareteam/flare-engine) | NPC 对话脚本及中文 | 主战役通用字符串 + 引擎界面目录 | 物品定义含 name/flavor，PO 有对应中英 | PO/POT + INI 风格 TXT | 最适合先做跨类型适配，无需解包 |
| [CDDA 数据镜像](https://github.com/nornagon/cdda-data) | talk_topic、响应和条件等 JSON | 中文目录含菜单/设置/背包等；UI 总覆盖尚未独立统计 | 大量物品定义及对应译文 | 英文对象 JSON + 中文键值目录 | 更适合扩大规模，无需先解包 |
| [Pillars of Eternity 社区本地化](https://github.com/cerrorism/poe-localization) | 中英 conversations 表 | gui 表 | items 表含名称及说明，另有能力、任务等 | 按路径与 ID 关联的 XML stringtable | 商业 RPG 候选；不是已验证的官方中文金标准 |
| [stardew-data](https://github.com/juliaramosguedes/stardew-data) | 本轮未找到可下载对白输出 | 本轮未找到完整 UI 输出 | 已发布英文物品等结构化数据 | JSON + 提取脚本 | 本轮不满足直接下载完整双语库条件 |
| 《极乐迪斯科》现成数据库 | 已找到对话展示与英文图数据库 | 未核实可下载的完整中英通用目录 | 在线百科有入口，完整结构化双语导出未核实 | 网站、SQLite、SQL | 保留专项候选，当前完整覆盖证据不及前三项；详见 [专项调研](DISCO_ELYSIUM_DATA.md) |

翻译匹配数不代表质量通过。正式样本仍应按类别抽查；NPC 相关目录也不等于其中每条都是实际台词，不能将目录项数写成游玩时出现次数。

## 3. Flare：最省工的完整类型试点

### 下载入口与版本

游戏提交：`af6eee6d339ac98011864bfe89da837fe7769c28`；引擎提交：`4075191d576a28cbf4f27ec345ee1c7ef1482756`。这是两个各自固定的仓库快照，不表示已验证它们的游戏运行兼容性。

- [主战役中文 PO（内含英文 msgid 和中文 msgstr）](https://raw.githubusercontent.com/flareteam/flare-game/af6eee6d339ac98011864bfe89da837fe7769c28/mods/empyrean_campaign/languages/data.zh.po)。
- [主战役英文 POT](https://raw.githubusercontent.com/flareteam/flare-game/af6eee6d339ac98011864bfe89da837fe7769c28/mods/empyrean_campaign/languages/data.pot)。
- [引擎中文 PO](https://raw.githubusercontent.com/flareteam/flare-engine/4075191d576a28cbf4f27ec345ee1c7ef1482756/mods/default/languages/engine.zh.po)。
- [引擎英文 POT](https://raw.githubusercontent.com/flareteam/flare-engine/4075191d576a28cbf4f27ec345ee1c7ef1482756/mods/default/languages/engine.pot)。
- [游戏主战役目录](https://github.com/flareteam/flare-game/tree/af6eee6d339ac98011864bfe89da837fe7769c28/mods/empyrean_campaign)：按需取得 npcs、items、powers、quests、maps 等纯文本。

### 实测覆盖

口径：以同提交英文 POT 的非空 msgid 为分母，按 msgctxt/msgid/msgid_plural 对齐中文 PO，非空且无 fuzzy 标记为匹配。忽略头部和 obsolete 条目；目录项不是业务 occurrence。

| 目录 | 英文项 | 非空、无 fuzzy 中文项 | 缺口 |
| --- | ---: | ---: | --- |
| Empyrean 主战役 | 1,349 | 1,349 | 此目录未发现缺译 |
| fantasycore | 76 | 76 | 此目录未发现缺译 |
| 引擎 engine | 531 | 531 | 此目录未发现缺译 |
| 引擎 default data | 20 | 20 | 此目录未发现缺译 |
| alpha_demo | 488 | 172 | 316 空译文，NPC 相关目录项未译；不建议选作双语首版 |

主战役按来源引用分类：items 575、powers 100、engine 55、npcs 206、quests 69、books 65、cutscenes 24，另有 maps/enemies/scripts。每类在该目录中均匹配；同条若有多个类别引用可重复归类，不能把这些数再当作唯一 occurrence 总数。主战役、fantasycore 和引擎目录之间也没有做跨目录去重。

读取[物品定义样例](https://github.com/flareteam/flare-game/blob/af6eee6d339ac98011864bfe89da837fe7769c28/mods/empyrean_campaign/items/categories/level_4_unique.txt)，已确认同一个 `[item]` 中有 id、name、flavor；所以可以保留名称—说明的实体关联。[任务对话样例](https://github.com/flareteam/flare-game/blob/af6eee6d339ac98011864bfe89da837fe7769c28/mods/empyrean_campaign/npcs/base/abasi/quest_intro1.txt)含 him/you、任务状态条件和修改。UI 由引擎目录补足，不能只取主战役 PO。

### 还缺什么，是否解包

- PO 合并了相同字符串的来源，要按 TXT 中的位置重建 occurrence、说话对象和文本用途。可直接读公开脚本，不需要解包。
- `INCLUDE`、条件与任务状态要保留；暂未恢复完整可达路径。
- 没有验证目录是否覆盖所有运行时可见文本、图片文字或动态拼接结果；100% 只针对上述 POT，不是整个游戏的覆盖率。
- 尚未进行中文质量审核、角色口吻评估或正式数据划分。

## 4. CDDA：直接取得大规模英文对象和中文目录

### 下载入口与版本

使用固定 0.G 发布数据，镜像提交：`59cfbd26da128e34faaae316e2c77bb03c486507`，不将其当作当前游戏版本。

- [英文游戏对象 all.json，约 28.7 MB](https://raw.githubusercontent.com/nornagon/cdda-data/59cfbd26da128e34faaae316e2c77bb03c486507/data/0.G/all.json)。
- [简体中文目录 zh_CN.json，约 9.94 MB](https://raw.githubusercontent.com/nornagon/cdda-data/59cfbd26da128e34faaae316e2c77bb03c486507/data/0.G/lang/zh_CN.json)。
- [镜像生成说明](https://github.com/nornagon/cdda-data/blob/action/README.md)：游戏对象保留 `__filename` 来源；翻译目录按英文与 gettext context 查找。当前生成代码与历史 0.G 的生成过程不一定相同，不能据当前脚本反推该快照全部处理细节。

### 实测规模与覆盖

| 项目 | 实测结果 | 分母说明 |
| --- | ---: | --- |
| 游戏 JSON 对象 | 43,146 | 混合内容类型，不是译文条数 |
| 中文目录 key | 70,824 | 不含头部，含复数/context 等 |
| 非空翻译 key | 70,640 | 其中 70,134 的结果包含汉字；无汉字不自动判错 |
| 物品定义 | 8,154 | 本轮按明确物品 type 列表筛选，包含定义/模板，不是可购买物品数量 |
| 显式物品名称字段 | 8,154，其中 8,118 非空匹配 | 按原始字段 source/context 查目录 |
| 显式物品说明字段 | 7,584，其中 7,549 非空匹配 | 不展开 copy-from 继承 |
| talk_topic 定义 | 2,166 | 不等于独立场景或完整对话路径 |
| 对话直接文本字段子集 | 8,020，其中 7,682 非空匹配 | 仅顶层直接 dynamic_line 和直接 response.text；复杂嵌套未统计 |

UI 方面，检查了 Load、Options、Quit、Inventory 四个 key，均有非空映射。这证明中文目录中包含界面文本，但 JSON 目录没有保留所有源码位置，不能以四个探针估计全 UI 翻译覆盖率。

### 还缺什么，是否解包

- 翻译目录是文本键映射，不能替代每个物品/对话的出现位置。需从 `all.json` 关联原始 ID、字段和来源。
- 保留 gettext context（`\\x04` 分隔）、复数和占位符；部分翻译 JSON 的值是数组。不要按纯英文文本跨 context 合并。
- 物品继承、复合条件、嵌套对话、标签/动态文本需要进一步解析；本轮报告子集覆盖，未静默当作已完成。
- 缺少可直接使用的真实游玩顺序，topic 的说话人可能依赖运行时对象；静态图不能自动证明路径可达。
- 要精确统计 UI，应补取与 0.G 匹配的公开源码/POT，而非先解包；测试中文参考仍隔离。
- 未审核译文质量，也没有从非空映射数量推断“人工优质参考”数量。

## 5. 《永恒之柱》：商业 RPG 文本广，但要排除 AI 修订层

### 来源和下载

仓库：`cerrorism/poe-localization`；提交：`241f4aa98af3d54e73be614310f209b6d14e68ec`。

- [英文基础文本目录](https://github.com/cerrorism/poe-localization/tree/241f4aa98af3d54e73be614310f209b6d14e68ec/data/localized/en/text)。
- [社区简体中文 SC 文本目录](https://github.com/cerrorism/poe-localization/tree/241f4aa98af3d54e73be614310f209b6d14e68ec/data_expansion4/localized/sc/text)。
- [英文 items 文件](https://raw.githubusercontent.com/cerrorism/poe-localization/241f4aa98af3d54e73be614310f209b6d14e68ec/data/localized/en/text/game/items.stringtable)与[中文 items 文件](https://raw.githubusercontent.com/cerrorism/poe-localization/241f4aa98af3d54e73be614310f209b6d14e68ec/data_expansion4/localized/sc/text/game/items.stringtable)。

README 说明其基于 Nexus Mods 汉化，并通过 AI 辅助、人审方式改善 EZ 版本。本轮只下载 EN 和 SC，没有将 EZ、AI 修订术语表或分析文字当参考。SC 作为独立源层存在，不代表已核验其每条翻译的历史；商业游戏的知名度也不能代替对这份社区汉化的审核。[来源说明](https://github.com/cerrorism/poe-localization/blob/241f4aa98af3d54e73be614310f209b6d14e68ec/README.md)

### 文件清单与抽样

英文各内容包的相对路径去重后，与 SC 路径交集：

| 类别 | 英文逻辑表数 | SC 表数 | 同路径表数 |
| --- | ---: | ---: | ---: |
| conversations | 1,405 | 1,386 | 1,386 |
| game（UI/物品/能力等） | 19 | 18 | 18 |
| quests | 146 | 142 | 142 |

这是表路径覆盖，不是逐条翻译覆盖。英文扩展包可能按 ID 覆盖/扩展基础表，生产导入要按包优先级合并；不能将文件数直接相加或用基础表的统计冒充全部 DLC。

实际下载基础 EN 的 19 张 game 表、其中存在的 18 张 SC 对应表，以及 3 对对话表、2 对任务表。以 XML `Name + ID + DefaultText/FemaleText/GenderNeutralText` 对齐非空字段：

| 所查范围 | 英文非空字段数 | 对应非空中文字段数 |
| --- | ---: | ---: |
| gui（界面） | 2,420 | 2,420 |
| items（名称与说明等） | 2,443 | 2,443 |
| abilities（能力） | 1,849 | 1,849 |
| 基础 game 19 表合计 | 12,007 | 11,997 |
| 对话抽样 3 表 | 91 | 88 |
| 任务抽样 2 表 | 55 | 55 |

基础 game 的缺口为一条 interactables 字段及 9 条 unassigned 字段（该 SC 表缺失）；对话抽样另有 3 条缺失/空白。数字包括少量 debug/符号等，尚未完成“仅玩家可见”过滤，不能直接作为最终评测条数。items 的 2,443 是文本字段，不是物品数量。

### 还缺什么，是否解包

- 所查仓库树只有本地化表等文件，未找到 `.conversation` 原始图文件；角色名表存在不代表逐句说话人映射存在。要做人物/分支实验，仍需进一步找结构化场景/对话数据或从游戏补取。
- 物品名称和说明虽在表中，要可靠绑定同一物品仍需对象定义/引用，不能猜两个数值 ID 的关系。
- SC 的来源、版本、品质与扩展包对齐需核验；EZ 是 AI 辅助修订，不能当作未见过的纯人工参考金标准。
- 单句、UI、术语等接口开发可先用现成表；完整剧情评测缺口明确后再决定补取/解包。

## 6. 未列为现成完整双语库的线索

`juliaramosguedes/stardew-data` 的 README 声明支持 12 种语言，但本次完整树中只发现 `data/en-US` 的 37 个 JSON 输出，未见 `data/zh-CN` 输出，也未找到发布的对白或完整 UI 数据。它仍是可复用的提取流程，不足以证明现在就能直接下载用户所需的完整双语库。

本轮没有发现已同时验证“官方高质量中文 + 所有四类 + 完整语境 + 可下载”的《极乐迪斯科》现成包。这是本次核查的范围限制，不是对所有网站不存在此类数据的断言。

## 7. 推荐顺序与统一输入

1. **Flare 做跨类型首版**：覆盖明确、体量可控、来源脚本也公开。先做 UI/物品/技能/NPC 的小样本，重建所属实体与出现位置，无需解包。
2. **CDDA 扩大规模**：已有大规模 JSON 和中文目录，优先完善对象/字段/context 解析，并独立统计 UI 和复杂对话缺口，无需先解包。
3. **PoE 作为商业 RPG 候选**：先核验 SC 质量与版本，再考虑对话图、人物和实体关系补取。若仍以《极乐迪斯科》为正式样本，则继续其专项路线，不能拿 Flare/CDDA 的结果替代它。

这只是数据工程顺序，不冻结主评测集。正式样本选型还受译文抽查、篇章可用性和所需规模约束。

三类输入均能映射到同一基础结构：`game_id + snapshot + resource_namespace + 原始对象/位置 ID + field_name` 定位 occurrence，记录 text_type/text_role 和 source；中文以 reference 单独存储。PO 的 msgid、CDDA 的英文 key、PoE 的数值 ID 都不是跨资源/跨游戏的全局 occurrence ID。对话额外保留图/路径，非对话保留所属实体/使用位置，缺失不凭常识补齐。

## 8. 验证与剩余限制

已执行：下载/哈希一致性检查，PO/POT 键和空值/fuzzy 统计，XML 表名和 ID/性别字段匹配与重复检查，CDDA 明确字段子集的 context 查找。公开参考实现只作阅读，未复制或执行其代码；统计使用独立临时脚本。

未执行：真实游戏运行、完整分支重放、全部 DLC 合并、全量 occurrence 恢复、人工翻译质量评审、训练与翻译评测。因此“非空匹配”只能支持数据可取得与结构覆盖的结论。游戏内容的来源与使用条件随数据清单登记，不以仓库公开或工具代码许可代替语料本身的条件。
