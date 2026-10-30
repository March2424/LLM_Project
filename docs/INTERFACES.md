# 公共数据结构与模块接口

状态：2026-10-28，M2 和基础 M3 图检索已实现；其他模块契约待实现时补充。

M2 类型代码定义：[schemas/memory.py](../src/localization_agent/schemas/memory.py)。本页解释边界，完整用法和示例见 [MEMORY.md](MEMORY.md)。

| 对象 | 约定 |
| --- | --- |
| `Occurrence` | 项目内唯一 occurrence_id、文档、0 起位置、非空源文、正整数源版本；元数据未知为 None；默认 en→zh，其他方向显式提供 |
| `Scope` | 项目、可选文档、语言方向、字段→允许值 tuple；未声明字段不限制 |
| `Evidence` | 注册过的 occurrence_id、source_version、源文中存在的逐字 quote |
| `MemoryRef` | record_id + version；跨模块引用不得只保存正文或只保存 ID |
| `ReadView` | run_id + watermark；知识快照从该 run 获取；watermark 是记忆写入序号，不是译文计数 |
| `TranslationCheck` | occurrence/source version、目标 hash、context hash、reviewer、passed；真实检查由 M5 提供 |
| `MemoryRecord` | kind 为 term / summary / translation / style；带 scope、content、evidence、dependencies、状态和可选 run/position |
| `MemoryBundle` | 按类别返回适用记忆及冲突；是候选集合，不是 ContextPack 或实际输入 manifest |

`MemoryRecord.content` 的必需字段：term 为 source/target，summary 为 source/target，style 为 note；translation 由 `commit_translation` 统一产生，包含源译文、检查记录、元数据、知识快照和输入 view。源侧/目标侧摘要正文同存于一条记录，生成时分别调用模型。

写入与读取接口：

- `MemoryStore.put_source()`：相同源版本重复登记需内容一致；新源版本会使旧证据及派生物失效。
- `draft()` → `approve(ref, reviewer)`：显式审批。审批产生新版本；读取批准版本，草稿不进入上下文。
- `create_snapshot()` → `start_run()`：知识固定版本，run 绑定一个项目/文档/方向；同名重复创建会报错，恢复运行时使用已有 ID。
- `view()` → `MemoryService.read(occurrence, view)`：同次任务固定 view；M3/M4 根据候选生成真实输入包。
- `commit_translation(..., check, used=..., view=...)`：检查绑定当前候选，提交时校验版本；`used` 只含实际供给模型的记忆，不能直接塞入所有检索结果。
- `extract_terms(ref)` / `summarize(next_occurrence, view, flush=False)`：返回草稿，外部核验后批准；摘要一次处理至多一个批次。
- `invalidate(ref, reason)`：失效一个版本及递归派生物，保留历史正文供审计。

所有在线目标侧记忆限定于当前 run。测试参考译文不属于本接口；数据适配器必须负责源/参考物理分离，不能把参考伪装成通过检查的译文传入。显式批准和检查回执是可信调用方操作，不是本模块自动完成的语义判定。

错误约定：未知对象 `KeyError`；内容、作用域、模型结构不合法 `ValueError`；预期版本/源版本冲突 `VersionConflict`；缺少所需模型适配器 `RuntimeError`；真实模型异常记录后上抛。调用方应决定重试/人工处理，不统一转成空结果。`summarize()` 返回 None 表示没有达到更新条件，不表示模型调用失败。

尚未实现的公共结构：SelectionResult、TranslationOutcome、UpdateReport。M6 的人工锁、完整事务回执、幂等工作流恢复与导出协议也尚未冻结。


## 图与 M3 接口（2026-10-28）

类型定义见 [schemas/graph.py](../src/localization_agent/schemas/graph.py)，完整约定见 [GRAPH.md](GRAPH.md)。

- GraphRef：node/edge、id、version；不与 MemoryRef 混用。
- GraphNode / GraphEdge：类型、Scope、Evidence、版本及属性；边另有两端精确版本、审批状态。
- RelationType：关系名称、两端节点类别/实体子类型、方向语义及是否遍历；注册定义不可覆盖。
- GraphSnapshot：固定项目图引用；源更新和显式失效仍会阻止使用旧记录。
- CandidatePool：当前 occurrence/源版本、ReadView、图快照、候选、冲突和未解决项。
- Candidate：正文、类别、mandatory、MemoryRef（可选）、源文 Evidence、GraphRef 路径和关系标签。
- ContextPack：实际 selected、状态、manifest、context_hash、characters。字符预算不是 token 预算。
- MemoryService.eligible()：公开读取未经过 top-k 的有效记忆，用于图联查；保留原隔离规则。
- MemoryStore.commit_translation()：新增可选 context_sources/context_manifest。图调用方使用 GraphRetriever.commit() 验证图及输入包；旧 M2 调用保持兼容。

CSV 的 document/scene/block 为结构归属；branch/phase/speaker/addressee/text_type 进入兼容元数据。没有场景时保持未知；技术块不构成语义关系。CSV 工作流使用 GraphRetriever 以启用未知顺序保护，详见 [CSV_IMPORT.md](CSV_IMPORT.md)。


## 2026-10-29：无序游戏模式

新增轻量实体图与五类记忆组织方式；`start_run(..., mode="game_resources")` 启用项目范围，默认文档模式保持原行为。DEFINES 反查、候选链接、主题 STM/LTM 和双语概览的实际接口及限制见 [GAME_MEMORY.md](GAME_MEMORY.md)。
