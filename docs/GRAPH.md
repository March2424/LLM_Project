# 图引导检索首版

更新：2026-10-28。状态：已实现标准库 SQLite 图存储、CSV 源文导入、可扩展实体关系、固定图检索与上下文包、M2 联查和提交。已完成离线验证；尚无真实游戏语料或模型质量实验。

## 1. 设计与文件

借鉴 [TransGraph 第 2 节](https://aclanthology.org/2026.eacl-long.75.pdf) 的文本块关系及带关系标签的上下文。游戏实体、版本、作用域、审批与 DelTA 联查属于本项目适配，未移植论文代码，不是原方法等价复现。

| 文件 | 职责 |
| --- | --- |
| [schemas/graph.py](../src/localization_agent/schemas/graph.py) | 图节点、边、类型、快照、候选池及输入包 |
| [retrieval/graph.py](../src/localization_agent/retrieval/graph.py) | 与 M2 共用 SQLite 的图存储、审批、版本及失效 |
| [retrieval/service.py](../src/localization_agent/retrieval/service.py) | 有界图遍历、记忆联查、固定选择、清单和提交 |
| [data/csv_graph.py](../src/localization_agent/data/csv_graph.py) | CSV 列映射、人工标注及技术分块 |
| [configs/graph.json](../configs/graph.json) | 搜索深度、候选数量、字符预算与源侧可见范围 |
| [CSV 导入说明](CSV_IMPORT.md) | 缺少场景、章节或稳定 key 时的处理 |
| [scripts/demo_graph.py](../scripts/demo_graph.py) | 可完整运行的 API 示例与离线演示 |

图是有类型、有方向、带版本的图；节点种类为 document、scene、block、occurrence、entity、memory。实体用 attributes.entity_type 区分 character、skill、item、location、organization 等，允许增加游戏特定子类型。memory 节点只引用已有 MemoryRef，不复制正文。

文档 ID 可以只是导入单元，不保证它是剧情章节。CSV 中缺失场景时不创建虚构的场景节点。block_id 等结构信息保存在图中，不加入 M2 的严格元数据匹配字段。

## 2. 关系类别与扩展

| 类别 | 内置关系 | 说明 |
| --- | --- | --- |
| 归属 | IN_DOCUMENT、IN_SCENE、IN_BLOCK | 场景/块到文档、块到场景、occurrence 到块 |
| 顺序 | PREVIOUS | 当前块到前序块；CSV 仅在显式可信顺序下创建 |
| 人物 | SPOKEN_BY、ADDRESSED_TO | occurrence 到人物；来自已映射字段 |
| 提及 | MENTIONS | occurrence/块到实体；保存引用片段，消歧由核验方负责 |
| 篇章 | REFERS_BACK_TO、EXPLAINED_BY、CONTINUES | 当前块到提供指代、解释或承接依据的块；要求两端证据 |
| 记忆 | HAS_TERM、HAS_STYLE、HAS_TRANSLATION、HAS_SUMMARY | 类型检查后的记忆版本链接 |
| 实体事实 | MEMBER_OF、LOCATED_IN、COUNTERS | 人物到组织、实体到地点、技能/物品到技能/物品 |

关系方向固定，检索不自动生成逆关系。例如从组织查成员，需显式设计该方向的关系或查询；不会默认把所有关系当作双向。

`RelationType` 指定名称、两端节点类别、可选实体子类型、说明及是否参与遍历。使用命名空间支持游戏特定关系：

```python
from localization_agent.schemas.graph import RelationType

graph.register_type(RelationType(
    'mygame:UNLOCKS', ('entity',), ('entity',),
    '技能解锁机制', ('skill',), ('mechanic',),
))
```

注册只是定义合法关系，不创建事实。每条 `GraphEdge` 保存端点的精确版本、Scope、Evidence、attributes、draft/approved 状态和 reviewer。定义不可覆盖；变更关系语义应使用新名称。带多方参与者的事件可表示为自定义 event 实体，再用 PARTICIPANT、TARGET 等注册关系连接；当前没有自动事件抽取。

游戏特定的“人物效忠组织”“道具开启机关”“技能克制机制”不需要新增 Python 节点类。影响适用性的 branch_id、phase_id 等必须写入 Scope.conditions；attributes 可存强度、说明等附加信息，检索不会把任意 attributes 字段自动当成过滤条件。

关系提交流程：`put_edge(draft)` → `approve(ref, reviewer)` → `create_snapshot()`。实体节点是调用方已确认的实体登记；自动抽取器不能直接把未核验的实体身份当作批准事实。引文存在只验证来源，不证明关系语义成立。当前未接入自动实体、别名、场景或篇章关系提取模型。

## 3. DelTA 联动与读取

```text
当前 occurrence → 人物/提及实体 → 批准角色笔记/术语
当前 occurrence → 所属块 → REFERS_BACK_TO → 远处块 → 原文
                                                    → 本 run 的兼容译例
```

`GraphRetriever.retrieve(occurrence, view, graph_snapshot)` 返回 CandidatePool。它沿有向边进行有界广度遍历，并按 IN_BLOCK 反向获取块成员。默认最多 4 跳、40 个节点、30 个软候选；不会通过文档或场景节点无界扩展全文。结果含 MemoryRef、原文证据、图路径版本和关系标签，截断在 unresolved 中明示。

M2 的 `eligible()` 提供未经过 LTM top-k 裁剪的有效记忆。图能沿相关 occurrence 找到历史窗口之外的译例；目标侧仍要求当前 run、既定知识快照、固定水位、早于当前位置、元数据兼容且未失效。图中的 memory 代理也必须通过这一检查。未命中译例时只返回原文。

普通读取继续使用 STM/LTM/摘要；但当前块顺序不可信时，本检索器关闭局部 translation/summary 基线，只保留术语和角色资料，再允许经批准图关系找回兼容译例。直接调用旧的 MemoryService.read() 不具有这个 CSV 顺序保护，CSV 工作流应使用 GraphRetriever。

当前摘要是 M2 原有按运行/元数据累积的双语摘要，未自动改成块摘要。HAS_SUMMARY 链接需要调用方明确确认覆盖范围。当前不会自动建立记忆代理或关系；完整示例见 demo_graph.py。

默认 source_visibility=previous，只允许同文档、同 branch/phase 的当前及前序源文；all 仅用于明确提供完整源脚本的游戏运行。位置表示导入顺序，顺序未知时不能据此声称剧情先后。测试参考不进入导入映射、关系证据或记忆。首版不做跨文档检索、剧情分支继承或阶段区间推理。

## 4. 输入包、预算与提交

`build_context(pool, selected_ids=None)` 使用固定顺序选择。显式 ID 必须来自候选池且不重复；候选中的 mandatory 项始终保留。当前批准术语和 content/edge.attributes 中显式 mandatory 项属于硬项。这不是完整的格式、占位符或关系冲突解析器；候选召回的完整性仍需上层处理，尤其应检查截断标志。

预算是序列化候选的**字符数**，包括 ID、来源和关系标签；不是 token 计数，也不包括未来 Translator 的系统提示词与输出预留。硬项超预算返回 budget_conflict，术语冲突返回 conflict，不允许提交。soft 项不会截断正文，只整项选取。

ContextPack 保存 selected、manifest、context_hash 和 characters。manifest 只记录实际选中项；完整 pool 是检索日志，应由调用方另外持久化。图关系及原文使用各自类型的引用，不冒充 MemoryRef。

```python
view = store.view(run_id)
pool = retriever.retrieve(occurrence, view, graph_snapshot_id)
pack = retriever.build_context(pool)
# 上层实际翻译和 QA，并将 pack.context_hash 绑定到 TranslationCheck。
# retriever.commit(occurrence, target, check, pack)
```

`commit()` 检查 ready 状态、hash、候选绑定，按原 view 和图快照重新验证实际选择，再委托 M2 事务提交。建议此首版使用确定性的 lexical 模式；如果启用 M2 agent 检索，重验可能再次调用模型，输出变化会要求重建和重新检查。模型失败照常抛出。

M2 提交增加可选 context_sources/context_manifest，旧调用兼容。实际源证据和图结构证据均记录为源版本依赖；结构证据用于失效，不等于都作为正文发给模型。源更新使有关 TM 和派生记忆失效。`GraphStore.invalidate()` 同时失效图项、关联边及清单中使用它们的 TM；旧正文留作审计。

图快照独立于 M2 知识快照；一次任务固定二者及 ReadView，并写入清单。新图版本不会替换旧快照，但显式失效与源版本更新会阻止旧快照继续使用失效内容。系统未实现完整历史事件重放、多线程调度或全 M6 维护。按单线程顺序 run 使用；不要绕过 GraphStore 直接改表。

## 5. 运行与验证

仓库根目录：

```bash
PYTHONPATH=src python3 scripts/demo_graph.py
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

演示默认内存 SQLite，可用 `--db` 指定一个全新的文件路径；演示固定 ID，不要对同一演示数据库重复创建快照。真实导入及恢复不受这个演示约定限制。

本次执行 15 个测试通过（原 M2 9 个，图相关 6 个）：覆盖 CSV 同文不同位置、重复导入、缺失标注、未知顺序、前序可见性、自定义关系端点约束、草稿/批准/快照、条件过滤、远距离原文及 TM、硬术语预算、非法选择、源更新和图失效传播。离线演示返回术语、实体、原文、译例并成功保存清单。

尚未验证真实游戏 CSV 的字段分布、关系标注质量、真实翻译收益、大规模查询性能。节点扫描和依赖传播采用简单实现；无 Neo4j 或向量库依赖。下一阶段接入实际 CSV 映射和少量人工关系样本，再接模型候选提取及人工抽查。
