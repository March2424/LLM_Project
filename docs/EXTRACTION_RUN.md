# 低成本实体关系提取：配置与验收

日期：2026-10-31。已实现 HTTP 模型适配、长度自适应批处理和共享预算，已完成 Flare 24 条源文、1 次 DeepSeek API 真实提取试跑；尚未完成真实提取质量验收。Schema 见 [定稿协议](EXTRACTION_SCHEMA.md)，源文数据见 [三款游戏验收](GAME_SAMPLE_QA.md)。

## 模型接入

复制并修改 [extraction.example.json](../configs/extraction.example.json)：填写完整 chat/completions endpoint、模型名和提供密钥的环境变量名称。密钥只从环境读取，不写进配置或结果。标准库 HTTP 实现见 [chat_completions.py](../src/localization_agent/models/chat_completions.py)，无需安装 SDK。

请求使用 messages、model、max_tokens、temperature、stream=false；提取默认要求 json_object 输出。协议字段参照 [Chat Completions API 文档](https://api-docs.deepseek.com/api/create-chat-completion/)，不绑定或推荐某个具体模型。服务如果需要关闭思考，可按其文档在 extra_body 配置；适配器不假设所有服务支持同样的扩展。服务不支持 JSON 模式时，可显式将 response_format 设为 text，模型仍须按提示词返回 JSON。

每批只有一次实体关系提取模型调用。reviewer 配置、reviewer-factory 参数和 review_graph 任务已移除；旧配置可删除 reviewer 字段。`--config` 使用内置 HTTP 适配和预算；`--model-factory` 仍可注入一个自定义 ChatModel，其网络和费用控制由调用方负责。

## 一次送多少文本

默认每批最多 64 条，同时受以下估算限制；无分组语义，不为每行重复附带其他整段文本：

- 行数据输入预算：5,000 估算 token。
- 图 JSON 输出预算：8,192 token，按每行约 `192 + 行数据token/2` 预留，避免短名称批次产生大量实体/边时输出被截断。
- 已有实体候选：最多 48 个、1,200 估算 token，只传 ID、名称和类型，不重复发送整套证据/模型调用记录。
- 模型示例配置：上下文 32,768、单次最大输出 8,192；发送前还检查完整 prompt 与输出预留之和。须按实际服务限制修改。

token 估算使用 UTF-8 字节数/3 加固定开销，不是服务 tokenizer 或账单。长文本自动减少同批条数；单行超过预算会明确报错，不截断原文。图输出数量取决于文本内容，超出服务输出上限时报告截断错误，不尝试解析或自动重发。验收后可调整行数、输入和输出预算。

## 预算与结果

示例默认只处理前 5 批，每批一次图调用；max_batches=0 或 --max-batches 0 才尝试全部。budget 默认最多 20 次调用、200,000 token，包含提取，也包含显式开启的翻译调用。

如需金额上限，先按实际服务价格填写每百万输入/输出 token 单价，再设置 max_cost；价格与金额限额使用同一货币单位。未填写价格时费用显示未知，不能声称免费。价格不在仓库硬编码，也不自动读取最新价格。

发送前按估算输入和最大输出预留调用/token/金额预算，成功后按服务 usage 回填；返回截断也计费用。无 usage 或网络失败保守保留预留额，不自动重试以避免重复计费。启发式 token 与配置价格不能保证真实账单的绝对上限，费用需与服务后台核对；不使用缓存折扣来降低预算预留。

每批完成后原子更新结果 JSON，包含已完成结果、活跃批次、状态、预算统计、输入哈希和提取 prompt 哈希。失败时保留之前已完成的图和调用记录；现在可用 --resume 从数据库批次检查点续跑。恢复会检查输入、模型、prompt、schema 与批次配置，并沿用累计预算。新的运行仍需新路径；已有数据库不自动迁移。

Naev 的 3 条纯空白 formatting 行不送模型，原 CSV 不修改；输出单列 skipped_formatting_ids。其他空白原文仍报错。三款游戏应分别指定 project 和数据库。

## 验收入口

以下本地规划命令已执行，无密钥、数据库或模型调用：

```bash
PYTHONPATH=src python3 scripts/run_benchmark_agent.py data/flare/flare_entity_input_v0.2.csv --project flare --config configs/extraction.example.json --dry-run
```

替换输入路径和 project 即可规划其他游戏。输出总批次、每批行数范围/均值、试跑行数和图调用数上界；这不代表已经执行提取。

实际运行入口支持 --config、--db 和 --output。填写实际服务配置、设置对应环境变量并确认费用后，可用新输出路径执行；目前仅以离线模拟适配器验证执行路径，未宣称真实服务联调通过。默认只提取图，--translate 才额外翻译。

验收重点：检查 schema 类型与关系方向；阅读提取正反例；确认合理 inferred 关系经程序检查后能够入图；核对批次规划与预算；最后用少量真实批次检查实体复用、漏边和错边，再决定是否扩大规模。

## 本次实际验证（2026-10-31）

使用示例配置对三份完整 CSV 执行本地 dry-run，无模型调用：

| 游戏 | 待提取记录 | 全量规划批次 | 平均每批行数 | 默认前 5 批行数 |
| --- | ---: | ---: | ---: | ---: |
| Flare | 4,416 | 123 | 35.9 | 186 |
| 破碎的像素地牢 | 5,607 | 162 | 34.6 | 174 |
| Naev | 36,569 | 1,097 | 33.3 | 161 |

Naev 另有 3 条 formatting 被单列记录。全量规划批次不代表默认会执行全量；每款默认只试前 5 批，图调用数为 5，仍受总预算约束。实际计费与关系召回率未测。

相关离线检查共 23 项通过：新增模型/预算/批处理检查 6 项、自动建图 7 项、图检索 6 项、游戏记忆 4 项。覆盖合理推断的 通用物体/USES 推断入图、JSON Schema 与代码一致、HTTP 请求结构/用量、预算发送前拦截、截断计费、每批单次提取、失败保存已完成批次和既有记忆依赖约束。文档本地链接与 diff 空白检查另行通过；未执行真实服务请求。

独立模型复核已删除，图结果标记 verification=program_only，语义正确性由验收时人工抽查。Prompt 中的类型词表及方向定义由同一份 schema 动态注入。已有实体候选则由运行时注入 catalog，首批为空是正常情况；可直接阅读[完整渲染样例](../examples/extraction_prompt_preview.txt)。

删除独立模型复核后，重新运行提取/预算、图检索与游戏记忆相关离线检查共 23 项，全部通过；单批 Flare dry-run 确认图调用上界为 1，无真实模型调用。

## 首次真实试跑（2026-10-31）

Flare 24 条源侧记录、9 种文本类型，使用 deepseek-flash，关闭 thinking；一次请求，无模型复核或重试，全部完成并通过程序检查。服务返回输入 2,272、输出 3,521 token，共 5,793；产出 16 个实体、30 条关系。按官方峰时保守费率估算 0.0049068 USD，非账户实际扣款。配置仅允许 1 次调用，金额上限 0.02 USD。

本地报告、输入、模型原始输出、人工查看关系 CSV 与数据库保存在 `outputs/acceptance/real_smoke_2026-10-31/`，不随仓库分发。初步发现描述被当作实体名称、上下文推断标记为 direct 等待确认项；未进行完整真值标注，不报告精确率、召回率或全系统质量收益。

## Flare 正式提取（2026-10-31）

用户已完成小样本验收，并授权本次全量预算上限 2 USD。专用配置 [flare.extraction.json](../configs/flare.extraction.json) 沿用验收的 deepseek-flash、关闭 thinking、单次最大输出 12,288；全量 4,416 条初始规划 82 批。最多 200 次调用、3,000,000 token、2 USD，任一限额触发即停止。费率沿用验收记录中的保守配置，不代表真实扣款。

```bash
PYTHONPATH=src python3 scripts/run_flare_extraction.py
PYTHONPATH=src python3 scripts/run_flare_extraction.py --resume
```

专用入口只读取仓库忽略的 .env.extraction 来装入环境变量，不打印密钥。结果位于 outputs/flare_full_2026-10-31/。每个成功批次的图写入与数据库检查点共同提交，避免中断产生半批图；模型成功响应按完整 prompt 哈希复用，恢复不重发已缓存响应。网络结果未知的失败仍保守预留预算，不承诺跨网络中断的绝对一次请求。

结构校验失败或输出截断时可拆为两半继续，子批次各有持久检查点；单条仍失败时保留明确失败状态，不静默跳过。拆分增加的调用共用原预算。不会自动重试网络错误。原输入、schema 和 prompt 在正式运行期间保持固定。

可在运行期间或结束后导出查看（运行中导出只代表已完成部分）：

```bash
python3 scripts/export_extraction_graph.py --db outputs/flare_full_2026-10-31/graph.sqlite --output outputs/flare_full_2026-10-31
```

导出 entities.jsonl、relations.jsonl、relations.csv 和 graph_summary.json。所有结果仍为 program_only，正式提取完成不等于人工确认全部语义正确。

全量运行新增 keep_valid_items 策略：逐项执行同样的结构/证据校验，错误项放入 BuildResult.rejected 并附原提议及错误原因，其余合格项正常入图；不再为单个坏端点重发整批。Flare 专用配置启用此策略，通用入口默认保持严格整批校验。整份 JSON 不可解析或输出截断仍按拆批处理。重放和恢复不更改 prompt、schema、源文或旧成功批次；result.json 记录 validation_policy，原始提议均留在模型调用日志。导出的 rejected_items.jsonl 可用于单独复查，不把被拒候选计入已建立的边。

### 本次全量运行结果

已完成 4,416 / 4,416 条，检查点覆盖无重复/遗漏，源输入哈希一致。最终 1,812 个实体节点、3,959 条关系；另有 52 个候选被程序拒绝并单列原提议/原因，其中 45 个为关系端点种类不符。共 98 个完成子批次、16 个拆分父批次；116 次请求尝试，记录 114 次成功响应。源文、schema、模型和 prompt 保持验收版本，运行中由严格整批校验切换为逐项保留合格结果以节省重发费用。

按配置费率及服务 usage 估算已记录费用 0.8256369 USD；计入连接失败/取消请求的保守预留后为 0.8578611 USD，低于授权 2 USD；不是账户实际扣款。结果位于本地 outputs/flare_full_2026-10-31/REPORT.md，含 CSV、JSONL、汇总与 SQLite 图，不随仓库提交。所有结果仍为 program_only，未开展正式提取精度/召回评测或翻译实验。

本轮实际检查：提取/运行与恢复 8 项、自动建图 7 项、图检索 6 项、游戏记忆 4 项，合计 25 项通过；另检查全量覆盖、输入哈希、导出条数、本地文档链接和 diff 空白。

## 像素地牢与 Naev 全量（2026-11-01）

用户确认合计 15 USD，分别以像素地牢 3 USD、Naev 12 USD 的限额同时启动。沿用 Flare 验收的模型、schema、prompt 与逐项校验，不跨游戏混用实体库，不执行翻译或实体合并。

```bash
PYTHONPATH=src python3 scripts/run_game_extraction.py shattered
PYTHONPATH=src python3 scripts/run_game_extraction.py naev
```

每个命令可加 --dry-run 本地规划，或 --resume 从同一游戏断点继续。独立配置为 configs/shattered.extraction.json 与 configs/naev.extraction.json；完成后自动导出图文件，最终覆盖报告可用 `python3 scripts/report_extraction_run.py outputs/<游戏>_full_2026-11-01` 生成。结果仅在状态 complete 且检查点覆盖通过后报告全量成功。

初始规划：像素地牢 5,607 条、110 批；Naev 36,569 条、793 批，另 3 条纯空白 formatting 单列保留。输出分别位于 outputs/shattered_full_2026-11-01/ 与 outputs/naev_full_2026-11-01/。节点数不等于唯一实体数，报告附类型＋名称分组作为重复线索，不据此自动合并。

### 像素地牢已完成（2026-11-01）

5,607 / 5,607 条全部处理，110 次模型调用；检查点覆盖无重复、无遗漏，输入哈希和关系导出条数一致。产出 2,314 个实体节点、5,302 条关系；266 个候选被拒并单列。类型＋名称分组为 1,839 组，不能视为唯一实体数量。本次未做实体归一或翻译。服务 usage 为输入 594,434、输出 642,607 token，按配置费率估算 0.9494586 USD，低于该任务 3 USD 限额，非账户实际扣款。报告位于 outputs/shattered_full_2026-11-01/REPORT.md。该阶段 Naev 仍运行中；最终完成结果见下文。

### 两款全量均完成（2026-11-01）

Naev 已完成 36,569 / 36,569 条待提取记录，另 3 条纯空白 formatting ID 单列保留；检查点覆盖无重复、无遗漏，源文件哈希及 CSV 导出条数一致。795 次请求尝试，累计产出 30,065 个实体节点、36,635 条关系，827 个被拒候选单列保存。类型＋名称精确分组为 7,089 组，其中 2,502 组有多个节点；没有做全局实体归一，这些分组也不等于唯一实体数量。

Naev 已记录服务 usage 对应配置费率估算 7.5536229 USD，含不确定请求保守预留为 7.5701598 USD；像素地牢为 0.9494586 USD，两款合计保守估算 8.5196184 USD，低于合计授权 15 USD 及各自限额。上述均非账户实际扣款。模型、prompt、schema 和四列源数据维持运行配置，未执行翻译，中文参考未进入模型。

最终报告和关系 CSV 分别位于 outputs/shattered_full_2026-11-01/ 与 outputs/naev_full_2026-11-01/ 的 REPORT.md、relations.csv；同时保存实体/关系 JSONL、被拒候选、汇总及 SQLite 图。全部结果为 program_only，提取完成不代表实体已消歧或语义准确率已经验收。两款完成后停止后台监控。
