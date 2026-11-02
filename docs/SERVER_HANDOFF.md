# 服务器交接与 teacher trajectory 前置检查

当前状态以文末按日期追加的记录及 [TEACHER_TRAJECTORIES.md](TEACHER_TRAJECTORIES.md) 为准；前面的2026-11-01检查为历史记录。

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
4. **导出 teacher trajectory**：保存 source ID、split、冻结状态/候选、teacher 理由与动作、译文、记忆增量和调用成本，支持逐条恢复。先做少量真实样本确认后再扩量，明确 teacher/translator 模型、费用上限与输出位置。Teacher 合成译文另行调用与保存，仅用于示范检查；SFT label 只有自然语言分析＋ID。先导出筛选后的 teacher 示范并训练、冻结 pi_SFT，再由 pi_SFT 自采样多种动作，经固定外部翻译器和独立评分构建 DPO；主偏好对两侧都来自同一冻结学生，不能直接把原始 teacher 轨迹当 DPO。

此顺序的最小代码缺口是划分脚本、状态导出、teacher 采样/恢复与轨迹导出，不需要先引入图数据库、向量服务或全套 LangGraph Reviewer。训练协议和理由参与 SFT/DPO 的约定继续以 [TRAINING.md](TRAINING.md) 为准。

## 5. 本次验证

将所有未忽略的候选提交文件复制到临时目录，排除 `.vscode/`，不带本机 `artifacts/`、`outputs/` 或凭据，运行现有测试。首次发现 64 项：54 通过、7 跳过、3 项缺依赖报错；据此给 CI 补上已有 requirements 的安装步骤。在全新临时虚拟环境安装三个 requirements 后，65 项检查中 58 通过、7 项因无可选原始快照而跳过，0 失败。

随后仅给临时副本补入 Flare 原始提取 SQLite 和 tokenizer，执行上文源库诊断：48 条全部通过，最大软上下文 598 token，0 次模型调用。已有多人物案例的一条期望原文进入候选池但未进入固定基线最终五项，该语义限制保持如实记录。`git diff --check` 通过。

这是 macOS 上模拟 clone 文件集的验证，不冒充 Linux 服务器或远程 GitHub Actions 已运行。


## 2026-11-01：实际无卡服务器准备结果

已在 Linux 服务器的 /root/autodl-tmp/LLM_Project 完成 CPU 准备，基于 GitHub commit b80de113ebd1f7164840ae5722584bcb5fd8432b，工作分支 codex/cpu-preparation-20261101，新增代码保持未提交、未推送。CPU 环境为 Python 3.12.3 的 .venv-cpu，依赖锁与报告在 artifacts/cpu-preparation/。

经用户明确授权迁移三个只含源侧提取的 SQLite 与预算 tokenizer，连同代码共 11 个迁移文件哈希一致，三个原始库均没有翻译 run。Qwen3-8B 现有五个分片通过索引/header 检查，补齐 config.json/tokenizer_config.json，tokenizer 可在 CPU 读取；没有加载权重或运行模型。

实际验证：67 项检查，60 通过、7 因缺少可选原始快照跳过、0 失败；Flare 48 条和像素地牢 5 条源侧召回无失败。Naev 只核对迁移哈希与源库计数，未在 2GB 实例上建立全量召回索引。GPU 使用与付费 API 调用均为 0。

outputs/cpu_preparation_2026-11-01 已生成划分草案和 128 个试采样目标，涉及 97 个序列、418 条计划冷启动记录。没有真实冷启动译文、teacher 轨迹或 SFT/DPO 标签。巨大联通组、参考质量与源侧可见范围尚需复核；后续采样器、状态导出、预算配置与训练仍待实施。入口及格式见 [CPU_PREPARATION.md](CPU_PREPARATION.md)。


## 2026-11-02：无 dev 配置与 Teacher 入口交付

LLaMA-Factory 0.9.5 已安装到 .venv-training；SFT/DPO 的 CPU 参数解析、实际 tokenizer/template 及两类处理器的分析 loss mask 检查通过，未加载权重或执行 GPU 步骤。依赖锁和日志见 artifacts/llamafactory-setup/。本轮不设 dev，原 dev 组并入 train、测试 ID 保持不变，SFT 固定2轮最终模型。

Teacher prepare/run/export 已同步。服务器输出 outputs/teacher_pilot_2026-11-02 已执行 prepare：128目标＋388计划冷启动，共516步，预算上限配置1.0 USD（估算），API计数0。尚未执行真实生成，未产生有效训练轨迹。开始命令及HTML进度见 [TEACHER_TRAJECTORIES.md](TEACHER_TRAJECTORIES.md)。新增教师采样2项、记忆/可见范围5项、轻量召回5项的服务器离线检查通过；无dev划分2项亦已通过。

代码仍为 codex/cpu-preparation-20261101 工作区改动，未提交或推送。修改源代码/提示词后必须重新准备新输出目录，旧准备目录的哈希绑定会拒绝不一致运行。真实候选导出仍为program_only，后续质量筛选、约2,000有效示范、GPU SFT和学生自采样DPO都未执行。


## 2026-11-02：选择协议v2审查与无付费升级

本轮修复已同步至同一工作区，未提交/推送。Teacher只输出显式分析＋最多8个选择ID，独立固定基线翻译仅推进记忆；删除多余Teacher译文调用。完整选择证据、选择/记忆独立状态、逐类别恢复、目录锁、注册导出与真实模板CPU预检已补齐。审查清单和剩余阶段见 [TEACHER_DESIGN_REVIEW.md](TEACHER_DESIGN_REVIEW.md)。

发现用户已在旧目录运行至135/516步，预算账本289次成功调用、估算0.0584106 USD，无正在运行的采样进程。原config/state/budget/calls哈希保留；migrate复制至outputs/teacher_pilot_2026-11-02_v2。37个旧协议目标保留单列，91个未开始目标按8项执行；原中断warmup保持旧冻结输入，离线确认可命中其已付费基线响应。没有启动新付费采样。以后只对_v2目录执行run，命令见轨迹说明。

本机/服务器13项相关检查通过，训练环境真实模板/处理器SFT与DPO夹具监督范围检查通过。37条旧样本仅用于长度诊断（最长2208），没有混入新协议训练数据。日志、迁移/hash回执与旧代码备份在artifacts/teacher-review-2026-11-02/。GPU步骤、正式新协议质量筛选、SFT、学生自采样与DPO仍待实施。


## 2026-11-02：每游戏持续记忆（当前有效）

用户纠正资源组多次冷启动；当前memory_policy=per_game_split_stage_v1。Teacher每游戏/train一个run，约10条冷启动一次后跨文件/资源组持续更新；DPO同样每游戏独立一次冷启动。资源组仅用于划分/溯源，人物/分支等作用域检查保持。

新计划outputs/cpu_preparation_2026-11-02_per_game：128目标、2个训练stream、20冷启动，合计148步；原train/test ID分配未变。旧teacher_pilot及_v2目录原样保留，不自动合并历史；当前run/export拒绝旧记忆协议。新输出teacher_pilot_2026-11-02_per_game单独准备，不自动运行付费采样；运行命令见 [TEACHER_TRAJECTORIES.md](TEACHER_TRAJECTORIES.md)。DPO采样/评分入口仍待实现。


## 2026-11-02：当前2000条context覆盖目录

新计划cpu_preparation_2026-11-02_2000_context_v2启用--memory-coverage并保留128旧目标；来源绑定与条件检查见MEMORY_COVERAGE_SAMPLING.md。新生成目录teacher_2000_2026-11-02_context由原per_game pilot扩量，沿用原记忆/预算/20次计划冷启动，不运行付费采样。旧teacher_2000目录保留原样，不再作为本轮启动入口。DPO采样/评分仍未实现。


## 2026-11-02：有序并发与断点升级（当前运行入口）

旧context进程已退出，295步完成、843次预算调用、估算0.3540936 USD，其中1条extract_terms为inflight。用户明确接受可能重复计费后授权该条重试；原行/旧预估费用保留，旧context目录6个配置/状态/账本/SQLite哈希未变。

优化代码已同步并通过服务器35项相关检查。输出teacher_2000_2026-11-02_fast保留全部2020条job内容、原记忆和预算，state索引0.632MiB（原55.3MiB）；单条序列化约0.0048s、索引约0.0652s，为孤立CPU测量，不是端到端提速结论。2游戏工作线程、HTTP并发上限4，同游戏顺序与记忆更新顺序不变。

后台启动器已挂起，等待/root/autodl-tmp/.teacher_api_key（600权限）。当前SSH无密钥，等待期间无模型请求；文件就绪后自动运行新fast目录并删除临时密钥。进度看新目录console.log；启动/等待状态在launcher.json。复核及原代码备份在artifacts/teacher-runtime-2026-11-02/。后续实际是否已启动以日志为准。
