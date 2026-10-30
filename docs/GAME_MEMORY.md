# 无序游戏资源：轻量实体图与五类记忆

状态：2026-10-29，已实现离线接口和演示，未验证真实模型翻译收益。设计背景见 [轻量方案](MODULAR_GAME_MEMORY_PLAN.md)。

## 实施更新（2026-10-30）

四列 CSV 桥接、从空实体表发现/跨批次复用、实体间关系候选与单独模型复核、图派生身份/主题及最小逐条翻译记忆集成已实现。实际API、验证与剩余限制见 [AUTO_GRAPH.md](AUTO_GRAPH.md)。下文原有“待实现/需人工登记”内容属于此前设计或旧入口；完整 LangGraph 工作流、真实模型效果与全局实体维护仍未完成。

## 使用

```bash
PYTHONPATH=src python3 scripts/demo_game_memory.py
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

演示使用内存数据库与明确标记的模拟回复，不调用外部服务。展示技能文件→实体→人物台词的跨文件检索，以及术语、角色笔记、近期译例和主题摘要。只有一条译例时，LTM 在排除 STM 后为空。

创建游戏运行：

```python
store.start_run('game-run', 'project', None, 'en', 'zh', 'knowledge-snapshot',
                mode='game_resources')
```

原调用默认 `mode='document'`，保留同文档、前序读取。游戏模式使用现有 runs.document 的 NULL 表示项目范围，无新增数据库表；旧运行不会自动改变模式。不同实验方法仍各自创建 run。项目应只包含本次允许读取的源资源；游戏图模式允许读取图快照内同项目、同语言方向的完整源文，不用于放开公开文档测试的未来上下文。

## 图和 CSV

`CSVConfig(namespace='hero', mode='game_resources', columns={'speaker_id': 'speaker', 'topic_id': 'topic'})` 可映射现有源侧字段。没有这些列时不要配置；缺失主题不会阻止翻译。namespace 保留文件身份。speaker_id 必须是项目内明确的身份，不是未经消歧的同名字符串；同一人物可跨文件复用。

使用已有 GraphNode 创建项目级 entity；使用 MENTIONS、SPOKEN_BY、新增 DEFINES 或已注册的实体关系连接。跨文件共享实体和关系使用 `Scope(project_id)`，必要分支/阶段条件保留在 scope 中。原 occurrence 节点可以保留文档 scope：游戏图读取时文档只表示来源。

游戏检索从当前文本出发，反查实体的定义和提及；优先定义，每节点默认最多 6 个邻居，实体—实体最多延伸一层，仍受总节点/跳数/候选预算限制。不沿技术块或 CSV 相邻行生成剧情上下文。图路径仍进入现有输入清单，关系失效会使依赖它的译文及摘要失效。

模型提取入口：`retrieval.extraction.EntityLinker(graph, memory).propose(texts, entities)`。参数是已登记文本和实体的 GraphRef tuple。通过已有 ChatModel 调用 `prompts/memory/entity_links.txt`，返回草稿边，需 `graph.approve(ref, reviewer)` 后重新创建图快照。非法 ID 或非原文引文会报错。首版实体表由调用方明确登记；不实现自动同名合并，也不声称能恢复未知场景。实体之间的关系继续使用已有 GraphEdge/RelationType API。

## 五类记忆

- 术语和角色笔记沿用原来的批准、作用域和快照机制。
- 游戏 STM 按明确 topic_id 或图关联筛选，再取本 run 最近提交的译例；它是“近期相关译例”，不是剧情前文。直接调用 MemoryService.read 可传 `related_ids`；GraphRetriever 自动从可见图路径提供。
- LTM 检索本 run 全部兼容译例，优先相关实体/主题，排除 STM；不再先截断最近 ltm_window 条。没有实体信息时可退回词法/向量查询。只作为参考，不自动覆盖译文。
- 游戏 `summarize(next_occurrence, view, flush=True)` 按 metadata.topic_id 生成主题概览。也可调用 `summarize_topic(..., topic_id=实体ID, sources=相关Evidence)` 使用图选出的源资料。源侧可覆盖尚未翻译的资料，目标侧仅覆盖本 run 已验证译例。首版双语摘要至少需要一条相关译例；没有时返回 None。
- 摘要从底层记录重建，达到 summary_step 个新增译例后更新，flush 可提前刷新；输入未变化时不重复调用。没有主题则不生成摘要。两侧分别调用模型，不推断剧情顺序；返回草稿，仍需批准。

branch_id/phase_id 在目标译例和图路径中保持兼容检查；未知不当作已知分支。文本类型、人物等元数据不再要求整字典相等，所以技能说明可以提供给人物台词作为参考；角色风格仍只按说话人 scope 生效。译例保留原文件来源证据、版本和读取水位。

主题摘要仍有有限上下文限制：当前实现适合小型主题，没有自动对超长主题做层级摘要。需要注入真实 ChatModel/Embedder 才能调用真实提取、摘要或向量检索。本轮没有增加新模型依赖、训练、引擎解析或导出系统。
