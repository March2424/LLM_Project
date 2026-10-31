# Naev 四列文本与独立参考

已完成固定快照 `123fdc1be96d350fdff04cd2f61e2c66c99badd5` 的资源生成、gettext 清单重建和导出。[数据目录](../data/naev/snapshot-123fdc1/README.md) 可直接使用；不需要运行或编译游戏。

## 覆盖与数量

源侧 [naev_entity_input.csv](../data/naev/snapshot-123fdc1/naev_entity_input.csv) 只有 `id,source_text,text_type,context` 四列。覆盖重建官方 gettext 清单的全部 **23,186 个条目**，按位置与英文单／复数形式拆为 **36,572 条源侧记录**。包含 UI、系统文字、任务剧情、物品装备、技能能力、飞船、地点阵营、百科及加载文字等。不同位置不按原文合并，92 个 gettext 复数条目同时保留两种英文形式。

本次实际运行固定版本的上游生成器，生成 **72 个装备 XML**（66 个生物船装备、6 个衍生装备）和 **592 个装备／飞船百科 Markdown**，共 664 个文件。遵循该版本 Meson 定义的百科目标，不额外假设未定义的目标。独立 assets 仓库使用主仓库指定的 `7dcc63f767d6657b737686722f868575fbf8e5c8`，32 个加载文字文件也已纳入。

官方 `credits_pot.py`、`toml_pot.py`、`naevpedia_pot.py`、ITS XML 规则和 xgettext 重新生成目录；不是直接沿用过期 POT。重建后相较原有清单增加 1 个 gettext 身份、移除 6 个旧身份。旧中文中不再对应当前清单的 6 项保存在离线 [orphan_references.jsonl](../data/naev/snapshot-123fdc1/orphan_references.jsonl)，不为保留旧译文而捏造当前源文。

## 中文参考状态

[references.jsonl](../data/naev/snapshot-123fdc1/references.jsonl) 为每个源侧 ID 保留一条伴随记录，空译文也不删除。按 `(msgctxt, msgid, msgid_plural)` 精确匹配同版本简体中文 `po/zh_Hans.po`，不运行自动模糊匹配或把旧译文套到新原文。

| 状态 | gettext 条目数 | 导出记录数 | 含义 |
| --- | ---: | ---: | --- |
| translated | 19,726 | 26,131 | 非空且未标 fuzzy；仍未经过本项目人工质量核验 |
| missing | 2,316 | 5,486 | 空译文或中文目录无精确对应项；源侧保留 |
| fuzzy | 1,144 | 4,955 | 保留上游 fuzzy 译文及原 flags，不能当已确认参考 |
| 合计 | **23,186** | **36,572** | 条目与使用位置是两种口径，不能混用 |

伴随字段包括 `record_id`、`reference_text`、`status`、`fuzzy`、`reference_available`、`flags`、`match`、`source_form`、`reference_plural_index`。fuzzy 与是否有译文分开记录；空 fuzzy 也能被表达。本快照导出的 fuzzy 项都有非空译文。缺译的 `reference_text` 为空，不回填英文或模型译文。简体中文只有一个复数类别，因此英文 singular／plural 记录共享中文索引 0，并明确标注来源形式。

**源侧 CSV 不增加参考状态列**。模型只读取 CSV，中文参考和旧中文条目只用于允许的离线核对／评测，不能进入提取、记忆、检索、生成和在线审校。未验证参考不能当作批准 TM。

## 原生上下文、定位与剩余边界

- XML 根据官方 ITS 规则按字段／属性 XPath 拆分，保留原生 slot、size、license 等必要元数据，技能和能力按相应原生资源字段分类。
- Lua／C／Rust 的静态 gettext 调用分别保留文件与字节位置、分支条件。任务头 `cond`、`location`、`done` 分别标为 offer 条件／位置／前置完成项，不把任务报价条件说成整段剧情已经发生的事实。
- 只在唯一、未重赋值且可定位的字面量 `vn.newCharacter` 本地绑定中补充 speaker，不根据变量名、文件名或模型常识推断人物。明确的 `vn.na`、`vn.menu`、任务标题／说明等显示调用提供文本用途。
- 百科保留原生可见条件，嵌入 Lua 的位置回映到原模板；格式符、Lua 模板标记、换行和原有控制标记均保留。
- 原文相同的不同位置分别编号；实际输入保留 ID，文件／键／版本留在后台，不增加外层分组或实体归属列。无法确定的 text_type 为 unknown，不等于丢失文本。

[provenance.jsonl](../data/naev/snapshot-123fdc1/background/provenance.jsonl) 记录实际调用／字段或回补定位及上下文证据；[generation.json](../data/naev/snapshot-123fdc1/background/generation.json) 记录所有生成文件、输入、生成器和哈希。完整生成文件保存在本地构建目录，不重复随处理后数据发布。

共 266 个动态参数／目录外调用单独记录。C 宏等导致 67 个文件存在部分语法树，采用 gettext 的真实文件／行号补回位置，不静默删文本。最终位置口径为：20,604 条字面量调用、6,769 条 XML 字段、8,113 条百科正文、649 条百科标题、46 条 TOML 字段、202 条署名／加载文字、160 条 gettext 定位回补、29 条依赖目录定义。后者没有假装拥有可确定的游戏调用场景。25,143 条记录带原生上下文。

全量覆盖指固定版本官方国际化清单中的文本模板；未穷举动态变量展开后的所有屏幕组合、运行时控制流或人物／场景组合，未国际化硬编码、图片字和第三方模组不在本轮范围。数据 split=unassigned，尚未进行游玩路径、真实模型翻译或正式训练／评测。

CSV 原样保留 3 条源文只有空格的格式条目，类型标为 `formatting`，包括单位间距和 Markdown 排版用途；参考状态也照常保留。它们确实属于上游的本地化条目，不能为了接口通过而删掉或修改源文。当前 MemoryStore 的非空白源文约束不变；实际接口验证导入 36,569 条非纯空白记录，3 条格式记录保留在完整 CSV 中，需要下游按格式任务单独处理。

## 复现与验证

入口为 [build_naev.py](../scripts/build_naev.py)，数据处理依赖单列在 [requirements-naev.txt](../configs/requirements-naev.txt)。本次使用 Python 3.12.15、GNU gettext-tools 1.0、polib 1.2.0、PyYAML 6.0.3、lxml 6.1.3、tree-sitter 0.25.2 及配置中固定的语法库。tree-sitter 0.26.0 在真实 Lua 文件遍历时崩溃，已改用通过本次全量导出的 0.25.2。

本机依赖安装在忽略目录 `artifacts/naev_adapter_deps/`；原始 checkout 位于 `artifacts/game_text_audit_2026-10-30/naev` 和 `naev-assets`。要求 checkout 提交号与固定版本相同，输出和构建目录新建或为空；复现时用 Python 3.11+：

```bash
PYTHONPATH=src:artifacts/naev_adapter_deps python3 scripts/build_naev.py --work artifacts/naev_resource_build --gettext-bin /opt/homebrew/opt/gettext/bin --output outputs/naev_rebuild
```

实际成功构建目录为 `artifacts/naev_build_v3/`；也可复用其生成结果，只重新导出，脚本会检查所有生成文件的哈希：

```bash
PYTHONPATH=src:artifacts/naev_adapter_deps python3 scripts/build_naev.py --prepared-build artifacts/naev_build_v3 --output outputs/naev_export
```

实际验证包括：从固定原始资源完整执行生成与 gettext 重建；2 项针对性检查覆盖多位置、分支、明确角色绑定、动态调用、缺译／fuzzy 状态、英文单复数、纯空格保留和中文修改不影响源侧；对全量产物核对 gettext 身份／形式覆盖、参考精确对齐、ID 唯一、生成及输出哈希；通过现有四列接口导入非格式记录，纯空格记录单独核验。未运行真实模型。

上游源码／Lua 与 XML 等内容的许可分别保留，详见数据目录中的原始 LICENSE、GPL 正文及 CC BY-SA 4.0 正文。新增处理工作不改变上游文本与译文的归属。
