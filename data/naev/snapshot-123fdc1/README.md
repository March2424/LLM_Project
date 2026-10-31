# Naev 固定快照四列本地化文本

主仓库提交 `123fdc1be96d350fdff04cd2f61e2c66c99badd5`，资源仓库提交 `7dcc63f767d6657b737686722f868575fbf8e5c8`。

- [naev_entity_input.csv](naev_entity_input.csv)：源侧输入，只有 `id,source_text,text_type,context` 四列，36,572 条记录。
- [references.jsonl](references.jsonl)：每个 ID 对应一条离线中文记录，`status` 为 translated / missing / fuzzy，保留空译文和原始 fuzzy 标记。非空非 fuzzy 也未经过本项目质量审核。
- [background/provenance.jsonl](background/provenance.jsonl)：原生字段／调用位置、版本、英文 gettext 身份、上下文证据与参考状态。
- [background/generation.json](background/generation.json)：已实际生成的装备与百科文件、生成器、输入和哈希。
- [background/source_catalog.jsonl](background/source_catalog.jsonl)：重建后的 23,186 个源 gettext 条目，供覆盖审计，不是模型公共背景包装。
- [background/unresolved_calls.jsonl](background/unresolved_calls.jsonl)：动态参数或非目录调用；相应资源定义照常保留。
- [background/parse_warnings.jsonl](background/parse_warnings.jsonl)：部分语法树的源码文件，gettext 位置和目录定义提供回补，不静默删文本。
- [orphan_references.jsonl](orphan_references.jsonl)：旧中文目录中不再对应当前源清单的项，仅离线保存，不并入源侧。

逐记录状态：translated=26,131，missing=5,486，fuzzy=4,955。缺译和 fuzzy 都有源侧行，参考状态不增加 CSV 列，也不改变源侧 ID。复数英文单复数分成独立 ID，在 context 标记 plural_form；中文按其单一复数类别索引 0 对齐，参考记录明确标注源形式。

已覆盖固定源码按官方 gettext 规则标记的 UI／系统文字、任务剧情、装备物品、技能与能力说明、地点、阵营、百科和加载文字等。每个可定位的源码位置／XML XPath／资源定义独立编号，不按原文合并。原生 context 来自 gettext 语义上下文、Lua/C/Rust if 分支、任务 offer 条件、百科可见条件、明确的显示调用、唯一字面量角色绑定及 XML 装备元数据。未知身份、类型和动态条件不靠模型补写。

这不是全部运行时字符串组合的穷举：动态分派、动态 gettext 参数和部分宏语法需进一步解析。全量覆盖指当前重建 gettext 清单；未国际化硬编码、图片字和第三方模组不在此范围。split=unassigned，尚未进行真实翻译或正式评测。

CSV 中原样保留 3 条纯空格格式条目，text_type=formatting。它们参与全文本覆盖和参考状态统计；现有 MemoryStore 拒绝纯空白源文，普通翻译／建图任务导入时需按类型单独处理，不将其误报为数据丢失或擅自改写空格。

模型只读 CSV；中文 references、orphan_references 以及上游中文 PO 仅用于允许的离线核对／评测，不进入提取、检索、生成或在线审校。

复现与实际验证见 [Naev 接入说明](../../../docs/NAEV_DATA.md)。来源及许可见 [ATTRIBUTION.txt](ATTRIBUTION.txt)、[LICENSE.txt](LICENSE.txt)、[GPL-3.0.txt](GPL-3.0.txt)、[CC-BY-SA-4.0.txt](CC-BY-SA-4.0.txt)。
