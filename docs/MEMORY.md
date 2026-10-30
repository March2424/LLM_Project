# 记忆模块实现说明：基于 DelTA 的 M2 首版

更新日期：2026-10-28。图引导联查与输入清单扩展已另行实现，见 [GRAPH.md](GRAPH.md)；下文保留 M2 首版说明。状态：**已实现可独立运行的记忆模块，完成离线业务验证；尚未进行真实 LLM 翻译质量实验。**

这次把原先的空目录落实为记忆存储、读取、知识候选生成与更新接口。其他组员可以直接通过公共类型调用它，不需要先完成 LangGraph、图检索或 Loong 训练。

## 1. 做了什么

实现 DelTA 的四类核心记忆，并加入项目的角色笔记存储：

| 记忆 | 已实现行为 | 写入条件 |
| --- | --- | --- |
| 专名/术语 | 从已验证源译对抽取专名对应；查询当前句出现的批准译名；返回相互冲突的译名 | 抽取结果必须能在源句与本次译文中逐字找到；先 draft，再由显式 reviewer 批准 |
| 双语摘要 | 分别生成源侧、目标侧摘要；达到批次后与旧摘要分别合并；可处理不足批次的尾段 | 只使用本运行已验证译文及其源文；生成 draft，批准后用于后续句子 |
| STM | 按位置返回兼容的最近若干源译对 | 检查通过、事务提交、属于当前运行且早于当前句 |
| LTM | 从较宽历史窗口中选择相关译例；支持词法、LLM 选择、注入式向量相似度 | 先完成运行、作用域、位置、水位、失效过滤，再排序；与 STM 去重 |
| 角色记忆 | 保存带证据和条件的 style note，按 speaker 等字段读取 | 当前采用调用方提供草稿与人工/外部审核；尚未实现自动角色画像提取 |

**STM 和 LTM 共用一份译例记录。** 区别是读取方式，避免保存两份互相漂移的译文正文。

## 2. 具体借鉴了 DelTA 的哪些代码

已下载并检查官方仓库，而不只是根据论文摘要实现。固定上游提交为 `6dde70acc9e60fd2533c2ca467eb327884d20dab`。

| 上游位置 | 借鉴内容 | 本项目位置与适配 |
| --- | --- | --- |
| `infer/infer_gpt.py::Context` | 最近源译对窗口 | `MemoryService.read()` 的 STM；改为查询持久化的批准译例 |
| `History.extract_entity/buildin_history` | 从源译对提取专名、按当前句匹配 | `extract_terms()`；JSON 输出、双侧文本证据检查、候选审批；不自动以首次模型译名定稿 |
| `Summary.gen_summary/merge_summary` | 源侧/目标侧分别摘要，新增部分再与历史合并 | `summarize()`；复用中英两个方向的八份提示词，增加版本与来源依赖 |
| `RetrieveAgent.match` | 让 LLM 从历史源句中选相关句 | `retrieval="agent"`；返回稳定记录 ID，以 JSON 解析并校验候选范围 |
| `EmbeddingDict.match` | 余弦相似度与新近度的加权排序 | `retrieval="embedding"`；由外部 Embedder 提供向量，模块实际计算相似度 |
| `infer/run_infer_gpt.sh` | 记忆窗口起始参数 | STM=3，LTM 窗口=20，LTM top-k=2，摘要步长=20 |

上游链接：[推理实现](https://github.com/YutongWang1216/DocMTAgent/blob/6dde70acc9e60fd2533c2ca467eb327884d20dab/infer/infer_gpt.py)、[运行参数](https://github.com/YutongWang1216/DocMTAgent/blob/6dde70acc9e60fd2533c2ca467eb327884d20dab/infer/run_infer_gpt.sh)、[提示词](https://github.com/YutongWang1216/DocMTAgent/tree/6dde70acc9e60fd2533c2ca467eb327884d20dab/prompts)、[ICLR 2025 论文](https://openreview.net/forum?id=hoYFLRNbhc)。许可与修改说明见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

没有直接复制整套 GPT/Qwen 推理脚本，因此运行记忆模块不依赖上游的 PyTorch、Transformers、spaCy 或 API 客户端。

## 3. 与原仓库的重要差异

1. **数据库替代进程内列表。** 重启后仍可读取历史，并固定知识版本及运行读取水位。
2. **验证和审批前置。** 原始推理流程生成译文后更新记忆；这里要求上游工作流提供绑定当前候选的检查记录。术语和摘要另有 draft → approved 步骤。
3. **不执行模型返回的字符串。** 上游检索路径使用 `eval` 解析选择结果；这里使用 JSON，非法 ID、重复 ID、数量不符或解析失败直接报错，不静默当作空检索。
4. **摘要包含当前批次最后一条已提交译文。** 本模块在提交后查询批次，而不是从尚未包含当前句的列表切片；源侧调用只接收原文，目标侧调用只接收本方法的译文。
5. **LTM 与 STM 显式去重。** 先取最近 20 条兼容译例，再排除 STM，最后选 LTM。原脚本和本适配的候选数量可能不同，应在消融实验中说明。
6. **增加离线词法模式。** 默认 `lexical` 是方便接口开发的无模型基线；运行 DelTA 式模型检索需显式改成 `agent` 并提供后端。它不是训练过的 Loong 选择器。
7. **游戏作用域更保守。** TM 与摘要要求元数据一致，不把不同分支、人物关系或未知上下文的译文当作通用示范。角色笔记则按自身声明的条件匹配。

因此这是 **DelTA 启发的项目记忆实现**，不是原论文完整实验或原始参数下的等价复现。

## 4. 代码如何组织

| 文件 | 作用 |
| --- | --- |
| [schemas/memory.py](../src/localization_agent/schemas/memory.py) | Occurrence、Scope、Evidence、MemoryRef、ReadView、TranslationCheck、MemoryRecord、MemoryBundle |
| [memory/store.py](../src/localization_agent/memory/store.py) | SQLite 持久化、草稿/批准、译例提交、快照、水位、版本检查与依赖失效 |
| [memory/service.py](../src/localization_agent/memory/service.py) | 统一记忆读取、三种 LTM 检索、术语提取、摘要生成与合并 |
| [models/protocols.py](../src/localization_agent/models/protocols.py) | ChatModel、ModelReply、Embedder，供团队接入现有模型服务 |
| [configs/memory.json](../configs/memory.json) | 可调窗口、摘要间隔、检索方式、新近度权重 |
| [prompts/memory](../prompts/memory) | 独立维护的摘要、专名提取与历史选择提示词 |
| [scripts/demo_memory.py](../scripts/demo_memory.py) | 无需 GPU/API 的持久化运行示例 |
| [tests/test_memory.py](../tests/test_memory.py) | 关键记忆边界验证 |

调用关系：

```text
M1 注册源文 / 项目批准资料
    ↓
MemoryStore：来源、草稿、批准知识、知识快照
    ↓
建立 run → 固定 ReadView → MemoryService.read()
    ↓
MemoryBundle（带 ID/版本的候选，不是最终 Context Pack）
    ↓
未来 M3/M4 打包和选择 → 未来 M5 翻译与检查
    ↓
commit_translation() 保存通过检查的本运行译例
    ↓
后续 STM/LTM 可读；extract_terms()/summarize() 生成知识草稿
    ↓
外部核验后 approve() → 后续句子可使用
```

## 5. 数据、版本和隔离规则

SQLite 中目前有六张表：

| 表 | 内容 |
| --- | --- |
| `sources` | 不可覆盖的 project + occurrence ID + source version 与原文元数据 |
| `records` | 不可覆盖的记忆 ID + version；正文、证据、依赖、批准人、运行和顺序号 |
| `snapshots` | 某个项目批准知识的精确 ID/version 清单 |
| `runs` | 实验运行所属项目、文档、语言方向及知识快照 |
| `invalidations` | 失效的精确记忆版本与原因；不删除原记录 |
| `model_calls` | 聊天模型的任务、完整提示词、提示词 hash、返回值、模型与可提供的用量/费用、调用异常 |

### 作用域

`Scope` 包括项目、可选文档、语言方向和条件集合。条件例如 `{"speaker_id": ("A",), "branch_id": ("main",)}`。未声明的条件不限制；声明条件但当前值为 `None` 或缺失，返回 `unknown`；已知不符返回 `no_match`，优先于 unknown。

首版把剧情阶段当作等值条件，例如 `phase_id`，尚未实现区间、剧情分支继承或关系图推理。M1 应统一字段名与空值约定：未知用 `None`，不要填字符串 `"unknown"`。

术语和角色笔记只在 `scope_match == "match"` 时提供。译例与摘要另要求保存的元数据与当前元数据相同；因此一个未知分支的译例不会流入已知分支。没有角色/分支元数据的普通文档可以用空字典进行文档内记忆。

### 两个读取坐标

- `run` 绑定一个不可覆盖的知识快照。后来新增或修改批准知识不会偷偷替换该运行的快照。
- `ReadView(run_id, watermark)` 固定在线记忆读取范围。`watermark` 是本运行记忆写入序号，包含草稿和批准动作，**不是已翻译句数**。
- 还要求记忆的源位置小于当前任务的位置，避免乱序写入的未来译文泄露。
- 新的 source version 会使引用旧源版本的记忆及其派生物失效；旧源版本不能再次用于提交或批准。
- 手动失效及依赖失效会阻止旧快照继续把该条记录作为有效上下文。历史正文仍可 `get(ref)` 查看，但这不是无条件重放失效前状态的完整事件数据库。

### 检查与批准的信任边界

`TranslationCheck` 绑定 occurrence、源版本、目标文本 hash、context hash、检查者与通过状态。模块会拒绝不匹配或未通过的检查；**它不会自己判断翻译质量，也不会替 M5 重新运行 QA。** 调用方必须真实执行检查，并提供真实输入包 hash。

`approve(ref, reviewer)` 是明确批准动作，不是自动判断证据语义正确。引文存在只是最低结构检查；角色推断、术语正确性及摘要忠实性仍由人工或独立审核器判断。样例里的 reviewer 和 `passed=True` 是离线夹具，不能用于正式质量结论。

如两个已批准术语同时适用但译名不同，`MemoryBundle.conflicts` 返回源词，保留双方记录。M3/M5 应进入冲突处理，不能静默挑一个。

## 6. 怎样运行

在仓库根目录执行；首版仅使用 Python 标准库，无需安装模型推理依赖：

```bash
PYTHONPATH=src python3 scripts/demo_memory.py
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

已在 Python 3.14.7 上验证。代码使用 Python 3.10+ 语法；其他版本尚未实测，不视为已验证兼容环境。Windows 成员可以先设置 `PYTHONPATH=src`，再运行相同 Python 入口；此平台未验证。

演示会创建 `artifacts/memory-demo.sqlite`。重复执行使用新的示例项目和运行 ID，不覆盖已有运行。示例输入是人工构造的源句和演示译文，模型是标明 `offline-demo` 的函数。

示例最后读到：**术语 1 条、双语摘要 1 条、STM 2 条、LTM 1 条、角色笔记 1 条**。摘要经历了生成和合并，记录了依赖；SQLite 中也保留调用日志。这只能证明管线运作，不能证明译文或摘要质量。

## 7. 小组成员怎样调用

### A：数据、资料与记忆

1. 为每个出现位置建立 `Occurrence` 并调用 `put_source()`。ID 在项目内唯一，建议带文档前缀；同一句不同位置不共用 ID。
2. 通过 `draft("term"/"style", ...)` 提供批准资料候选；证据填写真实源记录与逐字引文。
3. 核验后 `approve()`，再 `create_snapshot()`。未批准的记录不会进入快照。
4. 每个实验方法/文档/语言方向创建自己的 `start_run()`；重启时打开现有库，直接读取已有 run，不重复创建同名 run/snapshot。

### B：检索与选择器

```python
view = store.view(run_id)
bundle = memory.read(occurrence, view)
# bundle.terms / summaries / stm / ltm / styles 都携带 record.ref。
# 检查 bundle.conflicts，再构造 CandidatePool / ContextPack。
```

`MemoryBundle` 是 M2 的候选结果，尚无统一 token 预算、图导航或硬规则解析。**不要把 `bundle.refs` 直接当成实际输入清单。** 选择后只把实际供给 Translator 的记忆 ID/version 记录到清单和提交依赖；检索到但没有供给的记录另存检索日志。

LTM 的 `agent` 模式是 DelTA 风格的固定提示词检索基线。Loong 训练的 M4 仍在它之后/上层选择最终上下文；如果训练要看到完整候选池，需要 M3 独立召回候选，不能只给它已经裁剪完的两个 LTM 结果。

### C：工作流与模型后端

模型接入契约为：

```python
# 实现该签名的函数或可调用对象，然后注入 MemoryService。
# task 区分 extract_terms、retrieve、summarize_source/target、merge_source/target。
def model_adapter(task: str, prompt: str) -> ModelReply:
    ...  # 使用团队选定后端；返回文本、真实模型 ID、可获得的 token/费用。
```

这是后端接入契约，不是一个已实现的远程客户端。模块没有写死模型 ID、URL 或密钥，没有启动付费调用；可按 task 路由到不同模型。摘要调用保留上游的两侧分开方式，第一批 2 次调用，后续批次通常 4 次调用；术语提取每次 1 次，LLM 检索在候选数超过 top-k 时 1 次。

`MemoryConfig.from_file("configs/memory.json")` 读取配置；构造 `MemoryService(store, config, chat=model_adapter)`。默认提示词路径基于仓库位置，部署到其他目录时可显式提供 `prompt_dir`。向量检索另提供 `embedder(text) -> list[float]`，由适配器管理模型版本和嵌入费用；当前没有向量缓存或索引，规模扩大后应移交 M3。

翻译提交顺序：读取固定 view → 实际打包 → 生成 → 检查 → `commit_translation(..., check, used=实际使用的记忆引用, view=view)`。保存的依赖必须属于该运行可见范围且兼容当前上下文。提交失败不能继续假设 STM 已更新。

摘要/术语更新在译例提交后调用，两者失败不会回滚已提交的正确译例。每批摘要批准后再更新下一批，避免反复生成未批准候选。文档或场景结束时，用相同元数据、位置大于末句的边界 `Occurrence` 调用 `summarize(..., flush=True)`；该边界对象只是查询参数，不必注册为真实数据。它不会自动保存未批准摘要，调用方仍需审核批准。

### D：验证与维护

`invalidate(ref, reason)` 会递归失效直接/间接派生记忆。替换旧译例、注册新源版本也会失效有关派生物；往已摘要范围补插译文会让相关摘要失效，之后由 `summarize()` 重建。

这属于 M2 的基本一致性支持。完整 M6 的影响范围搜索、人工锁、修订建议、no_change、工作流恢复、导出和自动重新验证尚未实现；不能把现有递归失效当作整个维护系统已经完成。

## 8. 已验证内容与剩余工作

已执行 9 个针对业务边界的离线测试，全部通过：

- SQLite 关闭再打开后数据仍可读；同一运行旧水位固定，其他运行和未来译文不进入上下文。
- 文档/方向不匹配被拒绝；同文不同分支分开；未知字段不被补成已知事实。
- 未批准知识不进入快照；批准知识版本固定；冲突译名显式返回。
- 译文检查绑定、预期版本冲突、跨运行依赖被检查。
- 摘要包含批次末句，源目标输入分离，旧摘要能够合并。
- 术语草稿必须有双侧逐字证据；提取重复执行不会重复加入同一有效候选。
- LLM/向量检索契约可运行，非法 ID 被拒绝，STM/LTM 不重复。
- 源文更新及被依赖译例失效会传播到摘要；摘要调用失败不丢失已验证 TM。
- 向已压缩历史补插译文会使旧摘要失效。

当前剩余事项：

| 事项 | 当前状态 / 下一步 |
| --- | --- |
| 真实模型提取、摘要与检索效果 | 未测；接入后端后先用独立开发小样本做人工抽查，登记成本和失败率 |
| 自动角色画像、实体消歧、关系边抽取 | 未实现；当前能存取人工/外部核验的角色笔记 |
| 项目资料适配与跨文档 seed TM | 未实现；首版 run 固定一个文档，不自动共享其他运行的目标译文 |
| 图检索、输入包、Loong 选择器 | M3 已有图联查与字符预算 ContextPack，见 GRAPH.md；统一 token 预算与 M4 仍待实现 |
| LangGraph 和真实 QA | 待 M5；当前提交接口把检查服务作为可信调用方 |
| 完整维护与并发调度 | 待 M6；SQLite 单连接由所属线程使用，当前建议每个 run 顺序处理 |
| sCOMET / dCOMET / Judge | 尚未接入本模块；没有声称取得任何质量分数 |
| 大规模性能 | 当前查询、依赖失效和 embedding 使用简单实现；没有建立向量库或执行性能基准 |

建议下一次协作先由 C 接入一个实际模型适配器，A 提供十余条独立开发用源句及少量可信资料，B 对齐 MemoryBundle → CandidatePool 的转换，D 检查证据忠实性和调用日志。先获得可信的小闭环，再扩大数据与模型成本。


## 2026-10-29：无序游戏模式

新增轻量实体图与五类记忆组织方式；`start_run(..., mode="game_resources")` 启用项目范围，默认文档模式保持原行为。DEFINES 反查、候选链接、主题 STM/LTM 和双语概览的实际接口及限制见 [GAME_MEMORY.md](GAME_MEMORY.md)。
