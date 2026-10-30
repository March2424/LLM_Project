# Flare 处理后的中英本地化数据 v0.2

可直接使用的固定快照，采用 `localization-text/0.2`。只含处理后的文本、上下文、统计和精简溯源；不含原始 PO/POT、游戏脚本、C++ 源码、媒体或原始归档。上游署名与许可文本随数据保留。

## 先看哪些文件

| 文件 | 用途 | 记录数 |
| --- | --- | ---: |
| [flare_bilingual.csv](flare_bilingual.csv) | 人工查看已有中英对照 | 3,800 |
| [flare_untranslated.csv](flare_untranslated.csv) | 缺少中文的英文与上下文，中文列保持为空 | 616 |
| [source.jsonl](source.jsonl) | 翻译系统的源侧输入 | 4,416 |
| [references.jsonl](references.jsonl) | 独立中文参考，以 record_id 关联 | 3,800 |
| [coverage.json](coverage.json) | 类别、模块、目录覆盖和缺译统计 | — |
| [manifest.json](manifest.json) | 语言、固定版本、文件哈希与使用边界 | — |

CSV 均为 UTF-8（带 BOM），只有四列：**类型、英文原文、中文参考译文、本地化上下文**。逗号和多行内容按标准 CSV 引号保存；本次入库将记录换行统一为 LF，单元格内容保持不变。

`source.jsonl` 每行是一个对象，仅含 `record_id`、`text_type`、`source_text`、`context`。`references.jsonl` 每行只有 `record_id` 和 `reference_text`。JSONL 可逐行读取：

```python
import json
from pathlib import Path

path = Path("data/flare/v0.2/source.jsonl")
with path.open(encoding="utf-8") as stream:
    for line in stream:
        record = json.loads(line)
        # 仅使用此处的源文和源侧上下文作为翻译输入。
```

也可通过仓库的通用 CSV 接口重新导入。在仓库根目录运行，输出目录需新建或为空：

```bash
PYTHONPATH=src python3 scripts/import_localization_csv.py data/flare/v0.2/flare_bilingual.csv data/flare/v0.2/flare_untranslated.csv --dataset-id flare-csv --output outputs/flare_csv_import
```

这会得到 4,416 条源侧记录、3,800 条参考、616 条缺译。CSV 再导入会生成新的后台编号，上下文保留为可读 `note`。若要保留本快照已有编号和结构化上下文，直接读取 JSONL。

## 覆盖与统计口径

| 资源范围 | 已有中英 | 中文缺失 |
| --- | ---: | ---: |
| Empyrean 主战役 | 2,818 | 1 |
| 引擎界面与消息 | 665 | 6 |
| 公共奇幻资源 fantasycore | 94 | 1 |
| 引擎默认资源 | 20 | 0 |
| Alpha Demo（旧版） | 201 | 539 |
| 开发教学及其他可选模块 | 2 | 69 |
| 合计 | 3,800 | 616 |

覆盖对话、选项、角色/敌人名称、UI、物品名称/说明/属性类别、技能、任务、地点、场景提示、书籍、旁白及制作人员表的可翻译标签。5 个简体中文目录的 2,464 个原文 key 都已在资源中找到；其中 2,148 个目录 key 有非空中文。638 个物品名称使用位置中有 626 个中英对照；75 个物品说明使用位置中有 64 个中英对照。说明包括原始 flavor 和 power_desc，不伪造完整动态数值 tooltip。

3,800 是**使用位置数**，对应 2,044 种不同英文、2,114 种不同中英文组合。同句在不同角色、资源或技能等级中使用时仍分别保留，不能把重复使用位置当成独立语言样本。616 条缺译中，538 条目录译文为空、78 条源文尚未进入对应简体中文目录；没有自动补译。

## 上下文与后台溯源

- 对话保留说话人、话题、候选地点、同块前后两句和可关联的任务。
- 物品和技能保留名称/说明关联；UI 保留可取得的界面、区域、用途和占位符。
- 书籍前后文只连接同一资源中触发条件相同的段落；不补写人物性格或推测的剧情。

[background/provenance.jsonl](background/provenance.jsonl) 保留每条记录的原生 ID、来源版本/文件/行列、使用资源、静态条件、参考目录位置和对齐状态。原始脚本事件、目录正文和完整输入文件清单已移除；这些文件位置只是溯源信息，不是上传的原始资源文件。

[background/dialogue_order.jsonl](background/dialogue_order.jsonl) 保留 93 个有台词的块及块内声明顺序，不含原始事件脚本。[background/view_row_lookup.jsonl](background/view_row_lookup.jsonl) 是 CSV 原导出顺序与后台编号的对应；人工排序后不能继续按旧行号回写。

## 实验边界

模型只读取源侧 `source.jsonl`。中文参考和双语 CSV 用于允许的离线核对/评测，不进入提取、检索、选择器、翻译或在线审校。静态条件可由后台用于作用域筛选，不把参考正文传回源侧。

当前 `split=unassigned`，尚未划分训练、开发、测试。正式实验需按兼容场景/任务和重复资源分组，避免同句及共享上下文跨划分泄漏。旧 Demo 与开发内容按模块区分，不默认与当前战役混作同一评测场景。

中文保留上游原样，尚未人工评级，也未成为批准 TM。86 条对照与英文相同、95 条不含汉字，包含一些专名/符号，不因此直接判错。地图名是候选位置；对话顺序是块内声明顺序，实际游玩路径和全局分支未核验。全量范围是这些固定快照中的本地化文本资源，不包括图片文字和所有动态组合的最终屏幕字符串。

## 来源、变换与许可

- [Flare Game 固定快照](https://github.com/flareteam/flare-game/tree/af6eee6d339ac98011864bfe89da837fe7769c28)
- [Flare Engine 固定快照](https://github.com/flareteam/flare-engine/tree/4075191d576a28cbf4f27ec345ee1c7ef1482756)
- [上游署名与译者信息](ATTRIBUTION.txt)
- [游戏内容的 CC-BY-SA-3.0 许可文本](licenses/CC-BY-SA-3.0.txt)
- [引擎来源材料的 GPL-3.0 许可文本](licenses/GPL-3.0.txt)（上游声明为 GPL-3.0-or-later）

文本、翻译归 Flare 团队及上游贡献者/译者所有，各来源保留原有许可，不将整个混合数据集重新指定为单一许可。本项目新增的是提取、分类、INCLUDE 展开、上下文组织和使用位置编号，未改写原文或译文。

本目录是此前已验证产物的“仅处理后数据”发布版本。源/参考 JSONL 保持原字节，CSV 仅统一记录换行，后台只留下定位/顺序等处理后元数据。原产物 manifest 和适配器哈希记录在本目录 manifest 中；发布清单只列本目录实际附带的文件，不引用不存在的原始输入文件。原始快照的可选下载和重新提取方法见 [项目数据说明](../../../docs/DATA.md#flare-全量本地化文本-v02)。
