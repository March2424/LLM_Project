# 实体关系提取定稿：game-graph/1.1

日期：2026-10-31。本页定义用于后续不同游戏的通用最小提取协议，当前三款游戏只是输入样本。实现位于 [schemas/extraction.py](../src/localization_agent/schemas/extraction.py)，机器可读格式见 [提取 JSON Schema](../configs/extraction.schema.json)。真实提取质量仍待小样本验收。

## 输入与实体

输入继续使用 `id,source_text,text_type,context`，无 group_id、外层分组或中文参考。每条文本保留独立身份，文件与版本保存在后台。空 context、unknown 类型不会阻止提取。

| 类型 | 范围 |
| --- | --- |
| character | 人物、NPC、敌人、明确的玩家角色；职业称谓作为角色类指称时也可使用，不据此合并具体人物 |
| skill | 技能、法术、能力及可独立指称的效果 |
| item | 可指称的物体、装备、材料、工具、载具；不为具体游戏单独增加顶层类型 |
| location | 地点、区域、星球、星系 |
| organization | 组织与阵营 |
| quest | 任务、可独立指称的任务线 |

只提取有助于跨文本理解和译名一致性的可指称对象，不把每个数值、形容词、脚本变量或占位符建成实体。没有必要为了覆盖 UI 再增加菜单实体类型。

## 关系及方向

| 关系 | 起点 → 终点 | 含义 |
| --- | --- | --- |
| MENTIONS | 文本 → 实体 | 提到或可明确解析地指代实体 |
| DEFINES | 文本 → 实体 | 命名、说明或解释实体；名称标签本身也可提供定义入口 |
| SPOKEN_BY | 文本 → character | 发话者 |
| ADDRESSED_TO | 文本 → character | 明确的说话对象 |
| MEMBER_OF | character → organization | 当前有效的组织归属 |
| LOCATED_IN | 实体 → location | 地点归属或有条件的位置事实 |
| COUNTERS | skill/item → skill/item | 克制或抵消，非普通关联 |
| USES | character/organization/item → skill/item | 人物、组织或物体使用能力或其他物体 |
| PART_OF | 实体 → 实体 | 部件与整体；不替代人物组织归属或物品所有权 |

首版不增加泛化 RELATED_TO。模型可以多提出有依据的上述联系，不为凑边生成没有具体含义的关联。原有图存储的其他结构关系不属于本协议的模型输出词表。

## 输出约定

实体字段：`key,name,entity_type,reuse_id,evidence`。`key` 是本批局部键；`reuse_id` 为已有候选实体 ID 或 null。同一身份在本批只列一次。名称与类型匹配且语义兼容时允许复用，无需每次提供独立人物传记；有歧义时保持不同身份。

关系字段：`key,source,target,relation,conditions,evidence`；可附 `basis,reason`。端点使用 `text:<行ID>` 或 `entity:<局部键>`；所有实体端点必须列在本批实体数组，包括复用项。

- `basis` 默认为 direct；依赖改写、别名、指代或局部语义推断时使用 inferred，并附一句简短 reason。通过程序检查后两类关系都可参与检索，图属性保留区别。
- evidence 为一个或多个 `{id,field,quote}`；field 为 source 或 context，quote 是对应字段中的非空原文片段。片段可以很短，不要求它逐字包含整条关系。可组合多条证据，但相邻行或同批本身不是证据。
- conditions 为元数据键到字符串列表的映射，无条件为 `{}`。条件性事实采用 `requirements=[完整原生context]`，不推断脚本变量的剧情意义。不将否定、假设、过去关系变成无条件的当前正向事实。
- 程序检查 ID、引用、类型、证据可定位和端点方向；这些定位检查不等于要求语义必须逐字明说。

例如，`Mara wields Emberblade.` 可以提出 character Mara、item Emberblade，以及 `Mara USES Emberblade`：basis=inferred，reason 为“持用武器意味着使用它”，证据直接引用原句。这种局部语义推断可以接受，不需要另一条原文再证明“uses”。

相反，`Basic melee attack` 恰好邻接 `Slash`，不能只凭行序建立它对 Slash 的 DEFINES；`case WARRIOR` 也不能证明台词说话人为 Warrior。

## 单次提取与程序检查入图

为控制成本，每批只调用一次提取模型，不再进行独立的模型语义复核。程序仍检查字段、类型、端点、引用存在、引文可定位以及已知原生触发条件，通过后写入可检索图。

BuildResult 只包含 entities、edges、extraction_call 和 verification；verification 固定为 program_only。新实体和边属性也保留该标记；边的 reviewer 为 program:graph-contract。存储中的 approved 表示通过当前程序准入、可供检索，不代表人工或另一个模型确认事实正确。

合理语义推断仍允许入图并保留 basis=inferred/reason。程序无法判断所有身份混并、否定或关系含义错误，这些由提取 prompt 约束并在小规模验收时人工抽查。原始提取响应与实际输入保存在 model_calls，不再生成第二份模型 decisions 或 review_call。

## Prompt 中的候选实体和关系

提取从同一份 Python schema 动态注入允许的实体类型、关系类型及方向/含义，模板中分别为 `{entity_types}`、`{relation_types}`、`{entity_definitions}` 和 `{relation_definitions}`，避免提示词落后于程序词表。

这里有两类候选：

- **已有实体身份候选**：`Existing catalog: {catalog}` 在运行时替换为当前项目已批准且有效的实体 ID、名称、类型，按本批文本相关性排序并受数量/token 预算限制。用于复用身份；首批通常为 `[]`，模型仍可以发现新实体。
- **候选关系类型**：`Allowed relation types` 与方向定义指定本批可提出的九种关系。具体三元组由模型根据输入提出，随后直接做程序检查，不再发送第二次模型请求。当前不把整张历史关系图重复输入每一批。

例如已有 `Emberblade / item` 时，读取 `Mara wields Emberblade.` 可以复用该物体身份、发现 Mara，并提出 `Mara USES Emberblade`。这不是要求人工预先填好每条实体或关系。

完整渲染样例见 [extraction_prompt_preview.txt](../examples/extraction_prompt_preview.txt)，使用自建文本和示例候选 ID，不是实际游戏提取结果。真实模型输入可在数据库 model_calls 的 prompt 字段核对。

1.1 移除未验收草案中的 ship 顶层类型并调整 USES 端点。已有旧关系类型定义的数据库不会静默改写；继续使用新数据库路径，不进行自动迁移。
