# 数据与划分协议

状态：Flare 接入已有样例与全量产物；其他数据及正式实验划分待补充。

**作用**：明确每次训练与评测使用了什么数据，以及模型在运行时允许看到哪些信息，保证对齐、复现和测试隔离。

后续填写：

- GuoFeng V1 Test 2、IWSLT2017、自建游戏数据的来源、版本、获取方式和使用条件。
- 实际文件名、文档/书籍/场景 ID、语言方向、规模、对齐与预处理规则。
- 训练/开发/测试清单、去重、记忆重置及源侧可见范围。
- 训练语料、游戏参考译文与更新事件标注的制作方法。
- 数据 manifest/hash、受限原始数据的本地存放位置及导出格式。

初始范围见 [README](../README.md#4-评测数据)。本页记录清单与处理规则，不存放受限语料正文或测试答案。

## 游戏数据来源调研（2026-10-29）

《极乐迪斯科》的现成数据库、自行提取路线、来源/版本核对和双语隔离草案见 [数据获取与格式草案](DISCO_ELYSIUM_DATA.md)。目前仅完成公开资料与工具代码核查，尚未选择最终来源、取得全量语料或冻结划分；在线可浏览内容不代表已有完整实验数据。

用户明确需要全类型本地化文本，包含 UI、物品名称/说明、技能、任务等。来源覆盖、双语对齐与评测统计按类别分别记录；对话阅读器不足以代表完整语料，非对话不强制套用剧情顺序或篇章评分。

### 现成文件覆盖核查

已实际下载并核对 Flare、CDDA 0.G 和 PoE 社区 EN/SC 文件，版本、下载入口、条数及缺口见 [来源与覆盖报告](GAME_DATA_SOURCES.md)。本地原始文件和哈希清单保存在被忽略的 `artifacts/data_source_audit_2026-10-29/`。该结果更新了“仅有公开资料核查”的进展；《极乐迪斯科》仍未取得完整本机数据。尚未完成统一导入、译文品质审查和正式划分，PoE 的 AI 辅助 EZ 层未用作参考。

## Flare 统一样例 v0.1

按用户要求选用 Flare，已完成一组跨类型格式样例，未全量导入。游戏快照 `af6eee6d339ac98011864bfe89da837fe7769c28`，引擎快照 `4075191d576a28cbf4f27ec345ee1c7ef1482756`；两者分别固定，不声称运行兼容性已验证。

| 类型 | 条数 | 范围与上下文 |
| --- | ---: | --- |
| 对话 | 21 | Abasi/abasi_2 两个 NPC 使用资源、8 个对话块；保留 him/you、主题、状态/物品条件、奖励及状态变化。共享台词的不同使用位置不合并。 |
| UI | 12 | MenuConfig 设置标签；保留面板、tab 与选项原始符号、源代码语句。未推断视觉顺序。 |
| 物品名称 | 7 | 原始物品 ID 90–95、430。 |
| 物品描述 | 7 | 对应上述物品的 flavor 字段；原始属性与 INCLUDE 引用保留，基础物品未展开，尚非完整动态 tooltip。 |

统一协议见 [接口说明](INTERFACES.md#已实现的-flare-样例契约game-text01)。产物位于本地 `outputs/flare_sample_v0_1/`，双语 Excel 用于人工筛选，`data/` 内包括：

- `source.jsonl`：47 个源侧 occurrence；`entities.jsonl`：10 个源侧实体；`blocks.jsonl`：8 个块的原始条件/事件和声明顺序。
- `references.jsonl`：47 条独立参考，使用原始 gettext key 精确关联，不按相似文本拼接；中文原样保留，quality_status=upstream_unreviewed。
- `review_bilingual.csv/json`：双语人工核对视图，禁止进入在线模型上下文。
- `manifest.json`：来源版本、保存字节的 SHA-256、包内输入路径、类别统计、可见范围与未完成项；`split=unassigned_preview`。

原始脚本在 `artifacts/flare_sample_v0.1/raw/`，两个 PO 目录在 `artifacts/data_source_audit_2026-10-29/raw/`，均不入库。交付 zip 随附这组输入与许可证；解压后也可使用 `--raw-root <包目录>/raw --catalog-root <包目录>/catalogs --output <输出目录>` 运行 [样例入口](../scripts/build_flare_sample.py)。默认参数命令见根 README，已实际执行；无需模型、API 或游戏安装。该入口不会自动下载原始文件。

实际验证：3 项小范围检查通过，覆盖重复台词的使用位置隔离、块内顺序/条件/奖励保留、参考精确关联及源侧隔离；工作簿已导出并检查对话/物品/UI 预览。

品质限制：中文来自 Flare 上游中文目录（原语言标签 zh；样例显示 zh-Hans），尚未人工评级。例如 Sound Volume 的中文为“游戏音乐音量”，但控件符号为 SFX，列为待人工复核，不改写原始参考。当前不能将这组数据宣称为高质量金标准；场景、跨块分支路径、版本间 ID 协调和正式划分仍待完成。

归属与使用：Flare 团队及上游译者，游戏内容 CC-BY-SA-3.0；所引引擎代码 GPL-3.0-or-later。包内保留完整许可证、PO 译者头信息与固定版本链接。本项目只新增适配与格式整理，不把上游文本或翻译写为项目成果。

## Flare 全量本地化文本 v0.2

用户确认采用简化格式，授权全量整理 Flare。固定来源版本与 v0.1 相同；已下载两个仓库的 1,819 个文本/源码/许可文件（14,300,685 字节），逐文件验证上游 Git blob SHA，下载无失败。范围包含所有随附 mod 的本地化字段、全部引擎字面量 gettext 调用，以及 5 个简体中文目录；不下载媒体，不运行游戏。

目录原文总计 **2,464 个 key，全部在资源中找到对应**；其中 2,148 个目录 key 的中文非空。按声明及 INCLUDE 使用位置展开后，输出 **4,416 条记录：3,800 条中英对照，616 条待翻译**。中英对照包含 2,044 种不同英文、2,114 种不同中英文组合；出现位置数、不同英文数与目录 key 数不是同一指标，不能把重复资源当作独立语言样本。

| 资源范围 | 中英对照 | 中文缺失 |
| --- | ---: | ---: |
| Empyrean 主战役 | 2,818 | 1 |
| 引擎界面与消息 | 665 | 6 |
| 公共奇幻资源 fantasycore | 94 | 1 |
| 引擎默认资源 | 20 | 0 |
| Alpha Demo（旧版） | 201 | 539 |
| 开发教学和其他可选模块 | 2 | 69 |
| 合计 | 3,800 | 616 |

中英对照的主要类别：对话 137、对话选项 212、物品名称 626、物品描述 64、物品属性/类别 33、技能名称 354、技能描述 318、UI 525、系统消息 408；另包含角色/敌人名称、任务、地点、场景提示、书籍、旁白及制作人员表的可翻译标签。完整类别统计保存在 coverage.json。

616 条缺失中，538 条来自已存在但译文为空的目录项，78 条源文尚无对应简体中文 key；没有自动补译，也没有把中文缺失记录丢掉。主战役及当前公共/引擎资源共 3,597 条已配对、8 条缺失；旧 Demo 和开发内容按模块区分。旧 Demo 依赖旧 fantasycore，固定快照间未验证运行兼容性，不将其与当前战役混作同一实验场景。

### 简化输出与后台资料

产物目录：本地 `outputs/flare_full_v0_2/`；`flare_localization_full.xlsx` 含中英文本、待翻译、覆盖说明三个页签。两个 CSV 均只有类型、英文、中文参考译文、本地化上下文四列。

- `data/source.jsonl`：4,416 条源侧输入，仅含后台 record_id、text_type、source_text、context。
- `data/references.jsonl`：3,800 条独立参考。
- `data/flare_bilingual.jsonl/csv`：3,800 条离线中英查看记录；`flare_untranslated.jsonl/csv`：616 条缺失记录。
- `data/background/provenance.jsonl`：原生 ID、原始来源/行列、目录 key/flags/译文、使用链、原始条件、对齐与质量状态；`dialogue_blocks.jsonl`：93 个有台词的块及原始事件。
- `data/background/view_row_lookup.jsonl`：原导出顺序的后台编号映射；`input_files.json`：1,819 个实际输入的 SHA-256 清单。
- `data/manifest.json`：协议版本、语言、固定版本、适配器哈希、输出哈希和源侧/参考侧边界；`coverage.json`：全部类别、模块、目录覆盖、缺失和排除统计。

上下文只加入有源侧依据的语义信息；原始 ID 和引擎路径从主要查看表移到后台。对话保留同块前后文、说话人、话题和地图候选地点；书籍保留相同条件的段落前后文；物品和技能关联名称/说明；界面保留用途、能解析的区域及占位符。未加入 LLM 生成的设定。协议细节见 [0.2 接口](INTERFACES.md#简化本地化契约localization-text02)。

### 复现与验证

已有本地快照时，在仓库根目录运行：

```bash
PYTHONPATH=src python3 scripts/build_flare_full.py
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_flare*.py' -v
```

原始输入默认位置为 `artifacts/flare_full_v0.2/raw/`；下载入口 [download_flare_sources.py](../scripts/download_flare_sources.py) 已执行，固定公开版本，可缓存重用，需要网络。zip 随附输入和标准库适配代码；解压后执行 `PYTHONPATH=code/src python3 code/scripts/build_flare_full.py --raw-root raw --output regenerated_data`。无需游戏、模型或 API。XLSX 是 CSV 的人工查看视图，其制作工具不是数据适配器运行依赖。

已通过 6 项检查（含原 47 条样例的回归）：全目录覆盖、记录与缺失守恒、同句不同使用位置及同源行多调用的独立编号、源侧上下文隔离、原始译文精确关联、条件/前后文与物品关联保留；工作簿三个页签和代表性的物品/UI 预览已检查。

限制：全量指上述固定快照的**可本地化文本资源**，不是所有图片中的文字或动态组合的最终屏幕字符串。未执行游戏，也未核验全局分支路径；物品动态属性提示的组成模板和相关原文字段已保留，但不伪造渲染后的整段 tooltip。INCLUDE/APPEND 按静态模块依赖回退解析，目录对齐优先使用资源所属模块的 key，并记录实际目录；不声称完全复现游戏运行时的全局翻译覆盖顺序。未人工评级中文、未划分训练/开发/测试。86 条对照与英文相同、95 条不含汉字（如部分专名/符号），保留为上游原样并在报告中计数，不直接判为错误。

许可和归属沿用原始来源；完整 CC-BY-SA-3.0、GPL-3.0-or-later 许可及 PO 译者头随包保留。适配器依据上游 [本地化字段提取工具](https://github.com/flareteam/flare-engine/blob/4075191d576a28cbf4f27ec345ee1c7ef1482756/tools/localization/xgettext.py)、FileParser 和 NPC 等代码核对字段语义，没有把上游翻译写为项目新增成果。

## 通用 CSV 导入

2026-10-30 已补充与游戏引擎无关的四列 CSV 导入器，复用 localization-text/0.2。支持中文/英文表头、BOM、引号内逗号和多行；原文、参考分别保存。类型/原文必填，中文参考和上下文可选；同句不同出现位置不合并，缺译只保留源侧记录。附带 [自建演示 CSV](../examples/localization.csv)，不是 Flare 原文，也不作为翻译效果结果。

从仓库根目录运行（输出目录需新建或为空）：

```bash
PYTHONPATH=src python3 scripts/import_localization_csv.py examples/localization.csv --dataset-id demo --output outputs/csv_demo
```

该演示有 5 个源侧记录、4 条参考、1 条缺译。多 CSV 可一次导入，例如现有 Flare 查看文件：

```bash
PYTHONPATH=src python3 scripts/import_localization_csv.py outputs/flare_full_v0_2/data/flare_bilingual.csv outputs/flare_full_v0_2/data/flare_untranslated.csv --dataset-id flare-csv --output outputs/flare_csv_import
```

已实际验证 Flare CSV 再导入：4,416 条源侧、3,800 条参考、616 条缺译。它是新的 CSV 数据快照：原有富结构上下文保留为可读 note，重新生成后台编号；不声称能凭四列恢复引擎对象或原始 Flare ID。若 CSV 上下文列已存为 JSON 对象字符串，可显式加 `--context-format json`，保留结构；该模式拒绝非法 JSON，不自动猜测文本格式。

Python 入口：

```python
from pathlib import Path
from localization_agent.data.csv_import import import_localization_csv

result = import_localization_csv(
    [Path("examples/localization.csv")],
    Path("outputs/csv_api_demo"),
    dataset_id="demo",
)
```

输出包括 source.jsonl、references.jsonl、后台 CSV 来源/行号和 manifest；完整字段与错误处理约定见 [接口说明](INTERFACES.md#通用-csv-导入接口)。源侧正文不含参考字段，CSV 的额外列留在后台。上下文列必须由调用方提供源侧资料，导入器不对自由文本进行语义审查；输入哈希记录的是原始 CSV 快照，不能把未审定参考当作批准 TM。

5 项新增离线检查覆盖源/参考隔离、重复原文身份、中文/英文表头和 JSON 上下文、空参考、同名多文件及仅改参考时编号稳定、错误不产生部分数据、已有目录保护；使用自建小样例，不依赖下载数据。另有 6 项 Flare 集成检查，在本地原始快照存在时运行，缺少快照时明确跳过。CI 无需模型、网络下载语料或第三方 Python 依赖。
