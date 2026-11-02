# 构建 Teacher 轨迹：进度、恢复与命令

当前运行/续跑入口见 [并发运行说明](TEACHER_RUNTIME.md)；旧context目录作为冻结源保留，新输出使用selected目录。

更新：2026-11-02。入口已实现，可在 CPU 服务器调用外部闭源 API；不需要 GPU。已有旧协议付费运行；本轮修复与升级不发起新的付费请求，离线夹具不作为训练收益。学生只训练自然语言分析＋选择 ID，翻译仍由外部 LLM 完成。

## 1. 全新运行的准备（零 API 调用）

旧多资源组运行请先看第5节，不能继续作为当前训练主线。服务器工作目录 /root/autodl-tmp/LLM_Project，使用 .venv-cpu。当前计划目录为outputs/cpu_preparation_2026-11-02_per_game：128目标、2个训练游戏stream、20条冷启动，共148步。每游戏只冷启动一次，跨文件/资源组不重置；资源组仅用于数据划分。

```bash
cd /root/autodl-tmp/LLM_Project
PYTHONPATH=src .venv-cpu/bin/python scripts/generate_teacher_trajectories.py prepare \
  --plan outputs/cpu_preparation_2026-11-02_per_game \
  --model-config configs/flare.memory_trial.json \
  --output outputs/teacher_pilot_2026-11-02_per_game \
  --max-cost 1.0
```

输出目录必须新建。沿用项目 memory_trial 配置中的 endpoint、模型、密钥环境变量名和价格；请在执行付费命令前确认该配置适合当前账号。1.0 为操作者声明的估算美元上限，不是完整生成的费用承诺或服务端账单硬限制。选择输出最多512 tokens，独立的记忆更新翻译及提取沿用原模型配置；所有调用共用调用数、token与费用上限，无自动 API 重试。

准备会复制两款训练游戏的源图库至独立输出，不改原提取库；冻结提示词、源代码、计划和 tokenizer 哈希，不读取 offline/references.jsonl。训练侧 source ID 白名单在图绑定、证据遍历、候选和记忆读取时执行；原生 context 仅使用白名单源记录已有的源侧上下文，不凭空补剧情。Naev 保留组和 test 源记录不作为训练候选。图仍可在本地全库索引，只有允许的源证据进入模型观察。

## 2. 开始付费生成／恢复

旧资源组记忆不能通过migrate转换成一份连续游戏历史。保留旧目录，按第1节准备新的per_game输出；已准备的per_game目录无需重复prepare。不要把密钥写进配置或日志；下面通过终端隐藏输入：

```bash
read -rsp 'LLM_API_KEY: ' LLM_API_KEY
export LLM_API_KEY
PYTHONPATH=src .venv-cpu/bin/python -u scripts/generate_teacher_trajectories.py run \
  --output outputs/teacher_pilot_2026-11-02_per_game
```

再次执行同一个 run 命令会跳过完成/失败的条目，并复用已持久化的成功响应。断开前若请求可能已付费却未保存响应，calls.sqlite 保留 inflight；遇到 inflight/failed 请求会停止并提示人工检查，不盲目重发或伪装成功。预算耗尽返回明确状态，不能通过重启重置费用计数。若需追加预算，检查 budget.json 和已发生费用后明确修改其上限，不能清空账本。run 未完整成功时退出码为2，已保存进度保留。

可选后台运行，日志路径仍是同一目录：

```bash
nohup env PYTHONPATH=src .venv-cpu/bin/python -u scripts/generate_teacher_trajectories.py run \
  --output outputs/teacher_pilot_2026-11-02_per_game \
  > outputs/teacher_pilot_2026-11-02_per_game/console.log 2>&1 &
tail -f outputs/teacher_pilot_2026-11-02_per_game/console.log
```

同一个目录只运行一个采样进程。失败条目保留在统计和记录中，不能当作全量成功；对实际请求异常的重试需先检查已付费/已完成状态，首版不提供自动回填。

## 3. 可视进度

终端持续打印完成数/总数、Teacher 候选数、失败数、API 次数及估算费用。progress.json 另含当前调用任务和源 ID；progress.html 显示进度条及最近30条完成/当前任务的原文、显式分析、所选 ID、展开的上下文正文、记忆更新状态与提示，5秒自动刷新。优化版终端与HTML每5秒刷新，预算仍逐次请求即时保存；完整数据在jobs/单条JSON中。

在服务器开启仅本机可访问的查看端口：

```bash
.venv-cpu/bin/python -m http.server 8765 --bind 127.0.0.1 \
  --directory outputs/teacher_pilot_2026-11-02_per_game
```

本机另开终端建立隧道，再访问 http://127.0.0.1:8765/progress.html：

```bash
ssh -p 19783 -N -L 8765:127.0.0.1:8765 root@connect.westc.seetacloud.com
```

## 4. 实际流程与导出

每款游戏在本阶段/train只有一个run，先处理一次10条warmup，再逐条冻结跨资源组目标的历史、水位、候选和实际上下文；切换文件或资源组不改变run_id。Teacher 只输出自然语言 rationale＋最多8个 selected_ids；selected_contexts和teacher_context保存展开的证据与完整来源。删除额外teacher_translation请求，当前目标译文不用于生成SFT轨迹。

随后由外部 LLM 使用 Teacher 选中的 ContextPack 翻译当前资源，只用于更新记忆。先选择、后翻译，两步不能并行；选择无效时不翻译、不写回。历史每步推进一次，不按参考分挑译文；前序译例可成为后续任务的候选记忆。有效选择的selection_checks_passed独立于memory_status，翻译/记忆失败不删除已有效的选择。预算或请求状态不确定时暂停，不能自动重复付费。

上述翻译结果通过占位符/硬术语等程序检查后，记录为本 run 的 unreviewed 译例，再独立更新术语、摘要和角色。衍生记忆也保留未审标记；某一类普通解析错误只记录提示，不抹掉其他类别。选择错误不会被伪装成语义检查通过，程序检查通过不等于语义验收。

```bash
PYTHONPATH=src .venv-cpu/bin/python scripts/generate_teacher_trajectories.py export \
  --output outputs/teacher_pilot_2026-11-02_per_game \
  --dataset-dir outputs/selector_training/data
```

导出 selector_sft_candidates.json，只有 student_prompt 和完整分析＋动作，当前记忆翻译不进 label；export.json 明确为 program_only 候选，未做独立语义或参考评分。先抽查HTML中的分析与证据，再落实正式筛选，不能把候选数量直接报为高质量训练样本数。

本轮先用128目标验证真实成功率和成本。扩量时用 prepare_training_data.py 的 --pilot-size 指定更多训练侧目标并使用新计划/输出目录；约2,000条指筛选后的有效示范数，不是固定原文处理数。SFT 固定2轮、第2轮为 pi_SFT，无独立 dev；SFT 后再由学生自采样构建 DPO。


## 5. 旧资源组运行的保留与新计划

原outputs/teacher_pilot_2026-11-02及后缀_v2目录按资源组建立多个run，与当前要求不同。已付费响应、译例、状态和费用全部保留，不能合并或改标签冒充“每游戏只冷启动一次”的历史。migrate仅保留旧协议审计，不把多run历史转换成当前主线；当前run/export会明确拒绝旧memory_policy。

新计划只处理每游戏10条冷启动，并在游戏内持续更新。用独立新目录准备、采样，不删除旧账本；新输出的预算只属于本次新运行，累计研究成本仍须加上旧目录支出。新输入与旧调用不完全相同，不能保证全部复用旧缓存，也不能声称新运行免费。

CPU准备（零模型调用）：

```bash
PYTHONPATH=src .venv-cpu/bin/python scripts/prepare_training_data.py --output outputs/cpu_preparation_2026-11-02_per_game
```

计划已存在时无需重复执行。然后使用第1节prepare，再执行第2节付费run；本轮只准备，不自动启动真实采样。

## 6. 训练前实际数据预检

export --dataset-dir 将有效当前协议选择写入注册的selector_sft_train.json，同时保存来源、哈希和program_only质量标记。可在CPU训练环境执行：

```bash
PYTHONPATH=src .venv-training/bin/python scripts/check_training_setup.py \
  --config configs/training/qwen3_8b_sft.yaml \
  --verify-tokenization \
  --output artifacts/llamafactory-setup/sft-real-data-check.json
```

该步骤使用真实LLaMA-Factory模板与处理器验证完整数据长度、rationale和ID的loss范围；超出cutoff_len直接报出，不静默裁掉候选或分析。不加载权重；GPU最小训练步仍需有卡后执行。DPO学生自采样和质量评分仍待后续实现及真实验证。


## 7. 历史扩量方案：未补齐角色/摘要覆盖

当前应使用第8节的新目录；本节保留历史准备方式，不再启动旧2000目录。

先人工查看outputs/teacher_review_2026-11-02_per_game/review.html。2000指目标总数（包含已有128），不是保证筛选后全部合格；语义拒绝或选择失败后需要补足。

准备步骤均无模型调用：

```bash
PYTHONPATH=src .venv-cpu/bin/python scripts/prepare_training_data.py --output outputs/cpu_preparation_2026-11-02_2000 --pilot-size 2000
PYTHONPATH=src .venv-cpu/bin/python scripts/generate_teacher_trajectories.py extend --source outputs/teacher_pilot_2026-11-02_per_game --plan outputs/cpu_preparation_2026-11-02_2000 --output outputs/teacher_2000_2026-11-02 --max-cost 5.0
```

extend复制停止的pilot及其SQLite、成功请求缓存、历史ReadView、动作和费用账本；不改变原目录。只追加1872个目标，保留每游戏同一run及原10次冷启动，不再增加warmup。预算5.0 USD为新目录累计估算上限，包含pilot已发生费用；独立旧资源组试验支出仍另计。两份旧记忆失败不伪装成功、不补写过去观察。

本轮128条结构通过；人工语义审核未完成，role/summary候选均未出现。短名称选很多同族源文、术语与STM重复是主要抽查点。最长真实Qwen模板3122 tokens、rationale及ID均有监督；该长度不保证2000条都小于4096，完整扩量后必须再次预检。另修复“50% durability”被误当printf的检查，保留真正的%s等占位符核验。

启动付费生成时对outputs/teacher_2000_2026-11-02执行run；目录已准备时不要再执行上面的准备步骤。


## 8. 当前2000条：增加同角色与兼容主题分组

计划outputs/cpu_preparation_2026-11-02_2000_context_v2，输出outputs/teacher_2000_2026-11-02_context。保留128条、追加1872条，不重做两款游戏的冷启动。新增17组角色/条件的79条、30组主题/条件的238条，共289个不同目标，计划提供45次角色笔记和88次摘要观察机会；尚未实际生成。说明见 [MEMORY_COVERAGE_SAMPLING.md](MEMORY_COVERAGE_SAMPLING.md)。

CPU准备入口（已准备目录不重复执行）：

```bash
PYTHONPATH=src .venv-cpu/bin/python scripts/prepare_training_data.py --output outputs/cpu_preparation_2026-11-02_2000_context_v2 --pilot-size 2000 --memory-coverage --retain-plan outputs/cpu_preparation_2026-11-02_per_game
PYTHONPATH=src .venv-cpu/bin/python scripts/generate_teacher_trajectories.py extend --source outputs/teacher_pilot_2026-11-02_per_game --plan outputs/cpu_preparation_2026-11-02_2000_context_v2 --output outputs/teacher_2000_2026-11-02_context --max-cost 5.0
```

启动时对新context目录执行run，勿继续旧teacher_2000目录。新输出继承369次调用/0.1491432 USD的pilot账本，5 USD为包含pilot的累计估算上限，不是服务账单硬上限。看进度中的candidate_available_targets_by_kind确认真实style/summary覆盖，再做人工抽查和完整长度预检。


## 9. 当前并发版断点续跑

每游戏串行、游戏间并行；同条Teacher与固定基线翻译并发但共用冻结输入。改用逐条检查点，升级/重试授权/停止/续跑命令见 [TEACHER_RUNTIME.md](TEACHER_RUNTIME.md)。当前输出outputs/teacher_2000_2026-11-02_fast；不改旧context目录，不重新准备2000目标或冷启动。
