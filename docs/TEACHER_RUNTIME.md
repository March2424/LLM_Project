# 教师轨迹并发与断点续跑

更新：2026-11-02。本次修正 SFT 采集依赖：Teacher 选择后，外部翻译使用其选中的上下文。候选、提示词、模型参数、记忆更新顺序和每游戏一次冷启动不变。

## 2GB 服务器退出修复

selected 运行在累计1058次调用后进程消失；零API调用的缓存恢复复现 SIGKILL（退出码137）。容器上限2GB，未取得内核OOM证据，不将具体终止来源视为已确认。原启动器只记录启动、不等待子进程，导致页面仍显示 running。

采集器现在只在内存保留已完成任务的进度摘要，完整提示词、候选和上下文仍在原 jobs 检查点；HTML按需加载最近记录，默认完整读取与SFT导出保持原行为。tokenizer在采集器内共享。服务器实际测得状态加载峰值从160.1MiB降至24.8MiB；该数值不是整个采集进程的峰值。

本服务器暂用 `--game-workers 1` 降低同时驻留的检索和记忆对象数量，每游戏历史与选择→翻译→更新依赖不变。`scripts/run_teacher_background.py` 读取标准密钥文件并启动后删除，等待采集器退出，将退出码写入 launcher.json/console.log；异常退出会修正仍为running的状态，不自动重试模型请求。已有响应和费用账本继续复用。

```bash
nohup .venv-cpu/bin/python -u scripts/run_teacher_background.py \
  --output outputs/teacher_2000_2026-11-02_selected \
  --credential-file /root/autodl-tmp/.teacher_api_key --game-workers 1 \
  > outputs/teacher_2000_2026-11-02_selected/supervisor.log 2>&1 < /dev/null &
```

已启动时不要重复执行。恢复前仍须确认没有另一采集进程，密钥通过终端隐藏输入，不能写进项目配置。

## 本地 CPU 续跑（2026-11-02）

云端再次在394/2020步被SIGKILL，账本没有未决请求。按用户要求迁移至本机同名输出目录；云端保留STOP标记，避免两端同时推进。迁移包含两款游戏SQLite、逐条检查点、成功调用缓存、原费用账本与源侧身份绑定，不复制测试参考或模型权重。只重定位配置中的运行路径，校验代码、提示词、tokenizer和身份绑定哈希，原配置保留为cloud-config.json；迁移记录在artifacts/teacher-local-2026-11-02/。

本地使用.venv-cpu与tokenizers==0.22.1，不需要GPU。仍由外部API完成选择/翻译/记忆提取，累计预算沿用云端5美元上限。运行入口为run_teacher_background.py，可使用2个游戏线程；Mac运行时用caffeinate保持系统唤醒。电脑须保持开机联网，合盖或关机仍会中断。

本机查看进度：

```bash
tail -f '/Users/march/Desktop/LLM Project/LLM_Project/outputs/teacher_2000_2026-11-02_selected/console.log'
```

## 执行与持久化

- 每个游戏一个工作线程，独占本游戏的MemoryStore/检索器，严格按原任务顺序执行。
- 同条目标先保存冻结ReadView/输入，依次执行 Teacher 选择→验证动作→使用选中 ContextPack 翻译→检查/写回→术语→摘要→角色更新，然后进入同游戏下一条。选择失败则跳过翻译与记忆更新，绝不回退到默认候选。冷启动仍用固定打包，不调用 Teacher。
- 默认2个游戏工作线程、总HTTP并发上限4。只允许不同游戏的请求重叠，同一游戏内不并行选择与翻译。全局请求缓存由锁串行写入，同输入的同时请求共用一个结果；预算预留/结算也用锁，包含所有未完成请求的预估占用。
- 每个阶段原子写jobs/000000.json等单条检查点。state.json只保留轻量索引，5秒刷新；完整读入请使用selection.checkpoints.load_state，而非直接把索引当完整轨迹。
- progress.json及console.log每5秒更新；HTML展示最近30条完成记录和当前任务，源ID可链接完整单条JSON。budget.json仍在每次请求预留/结算时即时持久化。
- 某游戏出现结果未知的请求时，该游戏停止推进，其他独立游戏可继续。没有自动重试。全局预算耗尽则停止新任务。

旧版约55MiB全量state每次序列化实测约3.7秒，且每目标多次保存；新版避免该成本随已完成数据量放大。HTTP端到端提速须以实际运行统计为准，夹具并发测试不作真实吞吐结果。

## 安全升级（无API调用）

先确认旧目录锁空闲。upgrade复制成功缓存、两款游戏记忆、逐条状态与费用账本；保留原目录及parent文件。旧工作中的冻结输入不变，不重新冷启动。本次允许选择→翻译依赖修正；提示词以及记忆/检索等其他业务代码变化仍会被拒绝。完成的旧记录和费用不重放，旧记忆作为明确标注的继承前缀，不重新冷启动；旧目标保留 legacy_fixed_baseline 标记，后续目标标记 teacher_selected_v1。导出报告分别统计两种来源，不能将继承记录称为全量采用新流程的实验。

```bash
PYTHONPATH=src .venv-cpu/bin/python scripts/generate_teacher_trajectories.py upgrade \
  --source outputs/teacher_2000_2026-11-02_context \
  --output outputs/teacher_2000_2026-11-02_selected
```

本次旧进程已退出，保留295步完成进度，1条extract_terms请求只有inflight。用户明确接受可能重复计费后，升级时使用--retry-call FULL_SHA256_KEY授权该一条重试。旧行进入call_retry_audit，旧预算预留不清除、不退款；真正重试发生在续跑时。成功的Teacher/翻译不会因此重发。

## 续跑与停止

目录已升级时，设置LLM_API_KEY后运行：

```bash
PYTHONPATH=src .venv-cpu/bin/python -u scripts/generate_teacher_trajectories.py run \
  --output outputs/teacher_2000_2026-11-02_selected --resume --game-workers 2 --http-workers 4
```

重复执行会跳过完成/失败的条目，读取逐阶段检查点并复用成功响应。已有解析失败的选择或记忆缺口保持审计状态，不自动花钱重新采样。

```bash
PYTHONPATH=src .venv-cpu/bin/python scripts/generate_teacher_trajectories.py stop \
  --output outputs/teacher_2000_2026-11-02_selected
```

stop/SIGINT/SIGTERM让当前任务完成后停止，不强杀在途请求；下次--resume会清除STOP标记。SIGKILL、断电等仍可能留下未知请求，必须检查，不能自动重发。

查看后台进度：

```bash
tail -f /root/autodl-tmp/LLM_Project/outputs/teacher_2000_2026-11-02_selected/console.log
```

如运行环境缺少密钥，启动器可等待用户写入/root/autodl-tmp/.teacher_api_key（权限600），读取后仅通过子进程环境传递，并删除临时文件；不会打印或写入运行产物。等待期间不调用模型、不增加费用。
