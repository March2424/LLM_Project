# 四列输入、自动建图与游戏记忆接入

状态：2026-10-30，已实现模型注入接口、标准库命令行和离线集成验证。没有运行真实模型实验或全量 Flare 采样，不代表翻译质量已验证。

## 输入

```csv
id,source_text,text_type,context
s1,Ember Ward,skill_name,
s2,Ember Ward absorbs damage.,skill_description,
s3,Keep Ember Ward active!,dialogue,npc_name=Captain; utterance_role=npc
```

这是自建结构示例。源文原样保存；id 必须跨输入文件唯一，同名文件不允许合并导入。输入必须恰好四列，不接受参考译文列。context 仅填写原生信息，空值正常；不预填实体或主题 ID。不接受 group_id 或其他额外列，不增加外层分组。独立记录的相邻位置及同批关系不能作为实体归属或剧情关系证据。

按输入记录顺序分批，默认每批 12 行；每行在一次遍历中输入一次。当前按行数限批，超长单行需调用方控制模型上下文预算。所有文件先解析，再登记源文。版本冲突仍由现有存储报错；不是完整工作流事务恢复器。

提取和复核模型只收到 id、source_text、text_type、context；文件来源仍在后台。正文及 context 不能确认归属时允许不连边，不强制复制名称/说明来补齐上下文。旧五列 CSV 需删除 group_id 列后再导入；已有数据库不自动迁移。

Python 接入：

```python
from localization_agent.data import import_benchmark
from localization_agent.retrieval import AutoGraphBuilder

inputs = import_benchmark(["skills.csv", "dialogue.csv"], "game", graph)
builder = AutoGraphBuilder(graph, memory, reviewer=review_memory)
results = [builder.build(batch) for batch in inputs.batches]
graph.create_snapshot("graph-v1", "game")
```

memory 和 review_memory 都是共享 MemoryStore 的 MemoryService，注入 ChatModel。提取和复核分开调用；可使用不同模型适配器。没有模型时明确报错，不生成随机关系。

## 自动提取与核验

AutoGraphBuilder 从空实体表开始：

- 提取新实体，或请求复用本批提示提供的 catalog ID；程序不按同名自动合并。
- 实体类型为 character/skill/item/location/organization/quest。
- 关系支持 MENTIONS/DEFINES/SPOKEN_BY/ADDRESSED_TO/MEMBER_OF/LOCATED_IN/COUNTERS。
- 证据必须定位到本批行 ID，field 为 source 或 context，并提供其中的逐字 quote。
- context 证据保存在节点/边的 support 属性。现有 Evidence 仍指向真实源记录；不会把 context 字段伪造为 source_text 中的引文。修改 context 也必须增加源版本。
- 校验 ID、引用、端点类型、条件后，另一次 review_graph 调用逐项返回 accepted/rejected/unknown 及理由。实体复用身份也需复核。
- 只有 accepted 实体和端点已接受的 accepted 关系入图。其他项留在 BuildResult.decisions；模型提示、结果和调用标识保存于现有 model_calls。

程序校验不能证明语义正确，单独模型复核也不是金标准。必须在真实样本上测错误合并和关系精度。catalog 默认最多 80 个候选，按当前源文/原生 context 的词面重叠排序；候选不足可能产生重复实体，当前没有全局实体合并/拆分维护器。

包含 requires_ 的原生 context 保守保存为 requirements，事实边及目标记忆保留该条件，不解释状态变量的剧情含义；互不兼容的触发上下文不能拼成一条事实。这不是通用脚本解释器。其他条件由模型提出并经复核；当前文本缺对应元数据时仍按 unknown 过滤。歧义、否定和条件错误须由实际审查发现，不能以图连通率代替质量。

## 联动五类记忆

GraphRetriever 从当前可见图快照的有效直接边派生：

- 唯一 SPOKEN_BY → speaker_id；多个说话者候选时不强选。
- 优先 DEFINES，其次 MENTIONS 的非人物实体 → 一个主要 topic_id；多主题时以固定排序选主主题，其余仍参与图检索，不宣称这是语义最优主题选择器。

派生字段存入 CandidatePool.resolved_metadata 和实际输入 manifest，不修改登记的 Occurrence 或原始 CSV。实体、边及来源引用同时进入清单，即使没有选任何软候选也保留；身份关系失效会使对应译文和派生记忆失效。

已有术语读取/提取、图关联 STM、跨文件 LTM 继续使用。角色记忆按派生 speaker_id 读取；主题摘要按派生 topic_id 更新，不需要人工主题标签。

`retriever.update_memory(occurrence, view, snapshot_id, flush=False)` 生成主题摘要及角色笔记草稿。摘要以当前有效底层资料和本 run 已验证译文重建，默认按 summary_step 节流；现在支持刚提交的当前译文进入覆盖。角色笔记先独立复核，首版每个人物/条件生成一条有效笔记，不自动将单句扩展为永久人设。草稿仍需要核验批准；不要把它们当作已有记忆直接读取。

## 最小逐条翻译集成

```python
from localization_agent.workflow.game import GameSession

store.create_snapshot("knowledge", "game")
store.start_run("run", "game", None, "en", "zh", "knowledge",
                mode="game_resources")
session = GameSession(retriever)
for occurrence in inputs.occurrences:
    outcome = session.translate_one(occurrence, "run", "graph-v1")
```

单条执行：取图与记忆 → 翻译 → 单独模型审校 → 常见占位符/标签及硬术语检查 → 带输入清单提交 → 提取术语、按需生成摘要/角色笔记 → 逐项审查后批准。

状态包括 committed、failed_check、conflict/budget_conflict、memory_update_failed。最后一种状态明确包含已经提交的 translation 引用，不能盲目重新提交。审批拒绝的记忆保留草稿，不进入读取。常见格式的占位符检查可通过 GameSession 的 placeholder_pattern 调整；不声称覆盖全部引擎格式。

这是最小顺序集成，不是完整 LangGraph 审校修复、重试、人工锁定或幂等恢复系统。术语生成与更新默认使用现有 MemoryService；目标记忆严格限定本 run，参考译文从未作为模型输入字段。

## 命令行

```bash
PYTHONPATH=src python3 scripts/run_benchmark_agent.py --help
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_auto_graph.py' -v
```

命令行必须提供 `--model-factory 模块:函数` 和 `--reviewer-factory 模块:函数`，函数无参返回项目现有 ChatModel 协议适配器；适配器可按 task 路由模型。默认只建图，显式 `--translate` 才翻译及更新记忆。提供新的 --db/--output 路径；已有文件不覆盖。模型 ID、服务地址、凭据与采样参数由适配器配置，不写入示例密钥。

此前五列实现已实际验证帮助入口、7 项新离线测试，以及使用模拟适配器的完整 CLI：两批自动建图、两条逐条提交。全量测试共 40 项，其中 37 通过、3 项因缺少对应本地原始快照跳过。没有调用远程模型或下载语料来验证新链路。

本次移除 group_id 后，重新运行 7 项自动建图离线检查及 CLI 帮助入口，均通过；补充验证四列模型输入、独立记录批处理、重复原文身份保留及拒绝额外列。未重跑全量测试或真实模型实验。
