# 三款游戏四列数据复核（2026-10-31）

本次复核 Flare、破碎的像素地牢和 Naev 的已生成固定快照。19 项相关检查通过（8 项数据适配检查、11 项既有 Flare／建图检查），并逐条核对源文／参考对齐、原生上下文证据、文件哈希及源侧字段隔离。复核不代表翻译质量或真实模型实验结果。

| 游戏与模型输入 | 源记录 | 非 fuzzy 非空参考 | 缺译 | fuzzy |
| --- | ---: | ---: | ---: | ---: |
| [Flare](../data/flare/flare_entity_input_v0.2.csv) | 4,416 | 3,800 | 616 | 0 |
| [破碎的像素地牢](../data/shattered/v4.0.2/shattered_entity_input.csv) | 5,607 | 5,606 | 1 | 0 |
| [Naev](../data/naev/snapshot-123fdc1/naev_entity_input.csv) | 36,572 | 26,131 | 5,486 | 4,955 |

三份 CSV 严格只有 `id,source_text,text_type,context`，ID 唯一，标准 CSV 引号保留逗号、多行、控制标记及格式符。没有 reference_text、group_id、文件／版本列或外层实体分组。重复原文及已定位的不同使用位置不按正文合并；未知类型保持 unknown。输入文件和中文参考的固定路径、哈希、状态入口见 [game_samples.json](../data/game_samples.json)，不能通过扫描整个数据目录把双语查看表或参考 JSONL 一起交给模型。

上游原文自带的行尾空格保留；`.gitattributes` 为生成数据固定 LF，并仅对 Naev 原文 CSV 关闭代码式空白告警，避免改写原文或改变发布哈希。

## 原生上下文

- **Flare**：2,081 条带上下文。每条线索已与 raw 字段、资源头或明确 UI 调用核对；player／npc 与 `you`／`him`／`her` 一致，状态条件与执行事件保留不同字段。没有复制相邻译文或推断的人物设定。[说明](benchmark_requirements.md#flare-现有快照导出)
- **破碎的像素地牢**：1,812 条带上下文，4,313 项上下文证据逐项核对源码位置、条件／switch／case 或显示调用。其余可来自无确定调用的资源定义，context 留空；不猜测人物身份。[说明](SHATTERED_DATA.md)
- **Naev**：25,143 条带上下文，55,832 项证据逐项核对源码、XML XPath、任务头、百科头或明确角色绑定。664 个生成资源的哈希核对通过。模板中的嵌入 Lua 位置回映到原模板。[说明](NAEV_DATA.md)

条件是静态原生线索，不是已批准的剧情事实或运行时 scope。动态键、继承分派、异步执行后的状态及所有运行时字符串组合未穷举；未解析调用与目录定义均有明确后台记录。覆盖指各固定快照的已整理／国际化文本模板，不能写成全游戏所有屏幕与实际路径已核验。

## 中文参考与缺译

- Flare 中文在 [references.jsonl](../data/flare/v0.2/references.jsonl) 中，缺译状态在 [后台溯源](../data/flare/v0.2/background/provenance.jsonl)。616 条缺译明确区分为 538 条 blank 和 78 条 missing_catalog；这两类仍在源 CSV 中。
- 像素地牢中文在 [references.jsonl](../data/shattered/v4.0.2/references.jsonl) 中，1 条缺译在 [后台溯源](../data/shattered/v4.0.2/background/provenance.jsonl) 标为 missing_reference，不用英文回退冒充中文。
- Naev [references.jsonl](../data/naev/snapshot-123fdc1/references.jsonl) 每个源 ID 都有伴随项，status、fuzzy、reference_available 与原始 PO 精确匹配。缺译可以是空串，fuzzy 保留原译文但不能当作已确认参考；旧目录的 6 项另存 orphan_references，不并回源侧。

像素地牢和 Naev 的针对性检查包含修改中文／fuzzy 标记后源 CSV 字节不变；Flare 导出只消费源 JSONL 与已核验的原生线索。源 CSV 均逐条与英文原始字段核对，比“是否含汉字”的检查更可靠，英文原始资源本身也可能含非拉丁专名。

## 接口与发布边界

Naev 的 3 条纯空格间距／排版文本原样保留，text_type=formatting。完整 CSV 格式有效，但当前 MemoryStore 拒绝纯空白源文；普通建图／翻译任务应按格式类型单独处理。此前实际接口验证导入其余 36,569 条；不能为了接口通过删除这 3 条或擅改原文。Flare 与像素地牢可完整导入现有四列接口。

原始下载、构建目录、解析库安装目录和临时核查结果保留在被忽略的 artifacts 下；发布范围只包含处理后数据、许可、适配器、配置、必要检查和文档。未提交密钥、模型权重、数据库或原始音画资源。所有数据 split=unassigned，参考译文质量仍为 upstream_unreviewed，不能当作真实研究收益或批准 TM。
