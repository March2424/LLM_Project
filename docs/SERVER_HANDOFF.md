# 服务器交接与 teacher trajectory 前置检查

检查日期：2026-11-01。当前可以提交并推送代码、配置和处理后数据；**仅 clone 尚不能直接生成 teacher trajectory**。已有的真实模型入口是提取和随机选择翻译试跑，teacher 模板尚未接入采样器。此次只检查仓库、修正 CI 依赖与交接说明，没有提交、推送、划分数据或调用付费模型。

## 1. Git 内已有与未包含的内容

| 内容 | 状态 |
| --- | --- |
| 三款游戏四列源 CSV、溯源信息、独立参考译文、许可 | 已跟踪，可 clone；split 仍为 unassigned |
| 模型 HTTP 适配、SQLite 图与记忆、召回与打包 | 工作区已有实现；需要连同新增文件一起提交 |
| teacher / 通用策略模板 | 已有模板；teacher 只用于采样，学生输入不带类别指导 |
| 三款游戏全量提取 SQLite | `outputs/` 被忽略，需单独同步，无需重新付费提取 |
| 预算 tokenizer | `artifacts/` 被忽略，需单独同步 |
| 数据划分、teacher 动作采样、冻结状态与轨迹导出 | 待实现，没有可执行命令 |
| SFT/DPO、评分器 | 待实现；不是本次迁移的已完成功能 |

检查时待提交候选文件约 67.34 MiB，最大单文件为 Naev provenance，约 28.76 MiB。未发现常见凭据模式或 `/Users/march/` 绝对路径；这不替代历史提交的完整安全审计。`.env*`、运行产物和本机编辑器配置已忽略。不要用 `git add -f outputs/` 把数据库、调用日志和试跑历史全部塞入 Git。

## 2. 单独同步四个运行输入

| 相对路径 | 大小约 MiB | 源记录数 |
| --- | --- | --- |
| `outputs/flare_full_2026-10-31/graph.sqlite` | 18.16 | 4,416 |
| `outputs/shattered_full_2026-11-01/graph.sqlite` | 24.54 | 5,607 |
| `outputs/naev_full_2026-11-01/graph.sqlite` | 193.07 | 36,569；3 条 formatting 不在提取库 |
| `artifacts/retrieval_tokenizer/tokenizer.json` | 另计 | 仅预算分词，无模型权重 |

三个库的图快照 ID 均为 `graph`，项目分别为 `flare`、`shattered`、`naev`。此次只读确认三个原始提取库均为 0 条目标记忆、0 个翻译 run；不要用 `flare_memory_trial.../run.sqlite` 替代它们来初始化正式训练。试跑中的未审译文不自动升级成训练标签。

可在本机仓库根目录打包，上传到服务器后在 clone 的根目录解包，保留相对路径：

```bash
tar -czf artifacts/server-inputs-2026-11-01.tar.gz \
  outputs/flare_full_2026-10-31/graph.sqlite \
  outputs/shattered_full_2026-11-01/graph.sqlite \
  outputs/naev_full_2026-11-01/graph.sqlite \
  artifacts/retrieval_tokenizer/tokenizer.json
```

服务器解包命令中的归档路径按实际上传位置填写：

```bash
tar -xzf /path/to/server-inputs-2026-11-01.tar.gz
```

以下为此次只读检查计算的 SHA256，可与服务器文件核对：

```text
597654ec1917c1d3ad20b8c018c732c7f885fbe081fa7af42f9a23b089ef8dfa  outputs/flare_full_2026-10-31/graph.sqlite
1be50f7623344f022d5160a12bcaccbf9ce37ee9e2c580a278a171f20103ba04  outputs/shattered_full_2026-11-01/graph.sqlite
51c774cd54c195d9c6995291ef8fc7899d6d96df5bcea497efce490d841695b3  outputs/naev_full_2026-11-01/graph.sqlite
c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539  artifacts/retrieval_tokenizer/tokenizer.json
```

预算 tokenizer 是已固定的 Qwen2.5 文件，来源见 [轻量召回说明](LIGHTWEIGHT_RETRIEVAL.md#5-真实源库诊断与复现)，不代表已选定实际 teacher 或学生模型。API 凭据在服务器通过 `LLM_API_KEY` 配置，不打包或提交。若使用本地 teacher，模型权重和推理服务需另行准备。

## 3. 服务器环境与可运行检查

建议 Python 3.11+，在仓库根目录按 [SETUP.md](SETUP.md) 创建虚拟环境并安装三个已有 requirements。读取处理后的 CSV/SQLite 不需重新下载原始游戏；完整数据重建另需各适配器说明中的工具，例如 Naev 的 gettext。

已有源库诊断可用于确认数据库、tokenizer 和召回在服务器正常，不调用模型：

```bash
PYTHONPATH=src python scripts/validate_game_retrieval.py \
  --db outputs/flare_full_2026-10-31/graph.sqlite --project flare \
  --tokenizer artifacts/retrieval_tokenizer/tokenizer.json \
  --cases examples/flare_retrieval_cases.json --sample 40 \
  --output outputs/server-retrieval-check/flare
```

这只是迁移诊断，不是 teacher 轨迹生成命令。现有 `run_translation_pilot.py` 固定随机选择，不能因含有译文/记忆日志便当作 teacher 选择监督。

## 4. 在服务器继续实现的最小顺序

1. **分组划分**：用现有 provenance 的资源对象/任务/场景和重复源文关联生成稳定 `train/dev/test` manifest，不改 occurrence ID，不随机按 CSV 行划分。参考译文单独保存；Naev fuzzy、缺译不能直接充当确认参考。先明确全项目源侧是否共同可见；允许共同可见时只共享原文/源图，各划分的目标记忆仍分别隔离，不必重新提取全图。
2. **生成基线历史并冻结状态**：各划分独立 run，顺序生成历史，保留当前翻译之前的 ReadView、水位、完整候选池和哈希。冷状态与相关记忆充足的状态都保留；不使用当前或未来译文填充过去。按用户当前要求可跳过 Reviewer，但统一标记 unreviewed，不声称语义验收通过。
3. **接 teacher 动作**：复用 `selector_observation`、teacher 模板、HTTP 适配和 `select_context`，保存完整 `rationale + selected_ids`、实际 prompt、模型版本、预算、失败和实际输入清单。当前观察把 STM/LTM 都标为 translation，接五类提示前需补充类别来源标签；不增加类别配额。学生 prompt 使用通用模板，reference 不进入观察或在线记忆。
4. **导出 teacher trajectory**：保存 source ID、split、冻结状态/候选、teacher 理由与动作、译文、记忆增量和调用成本，支持逐条恢复。先做少量真实样本确认后再扩量，明确 teacher/translator 模型、费用上限与输出位置。若继续构造 DPO，再对同一冻结状态采样多种动作并做独立评分；原始 teacher 轨迹不自动等于偏好对。

此顺序的最小代码缺口是划分脚本、状态导出、teacher 采样/恢复与轨迹导出，不需要先引入图数据库、向量服务或全套 LangGraph Reviewer。训练协议和理由参与 SFT/DPO 的约定继续以 [TRAINING.md](TRAINING.md) 为准。

## 5. 本次验证

将所有未忽略的候选提交文件复制到临时目录，排除 `.vscode/`，不带本机 `artifacts/`、`outputs/` 或凭据，运行现有测试。首次发现 64 项：54 通过、7 跳过、3 项缺依赖报错；据此给 CI 补上已有 requirements 的安装步骤。在全新临时虚拟环境安装三个 requirements 后，65 项检查中 58 通过、7 项因无可选原始快照而跳过，0 失败。

随后仅给临时副本补入 Flare 原始提取 SQLite 和 tokenizer，执行上文源库诊断：48 条全部通过，最大软上下文 598 token，0 次模型调用。已有多人物案例的一条期望原文进入候选池但未进入固定基线最终五项，该语义限制保持如实记录。`git diff --check` 通过。

这是 macOS 上模拟 clone 文件集的验证，不冒充 Linux 服务器或远程 GitHub Actions 已运行。
