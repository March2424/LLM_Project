# Qwen3-8B 选择器 SFT：RTX PRO 6000 96GB 方案

更新：2026-11-03。配置为 [qwen3_8b_sft_96gb.yaml](../configs/training/qwen3_8b_sft_96gb.yaml)。已在RTX PRO 6000 96GB完成1966条候选的2轮SFT，共246次更新；实际GPU比较、SwanLab和完整状态恢复均已验证。数据仍未完成全量语义审核，正式翻译质量和DPO尚未评测。

## 训练任务与数据

学生输入是当前原文、任务元数据、固定硬约束、最多16项候选的完整内容与ID，以及选择预算。学生输出是自然语言 rationale 和 selected_ids 的 JSON；完整分析与动作参与 loss，输入 prompt 不参与。选择0–8项，软上下文预算1024 tokens；硬约束由执行器固定保留，外部LLM负责翻译。

本次实际使用1966条训练候选：1967条程序有效选择中，隔离了 jobs/000650.json 的错误分析。原始失败与被隔离条目留在本地审计，不补采样。训练候选中336条继承旧固定基线历史，1630条采用Teacher选中上下文推进记忆；保留逐行来源，仍属 program_only / unreviewed，不将程序通过等同语义质量验收。

每个游戏/train仅一次10条冷启动，之后持续记忆、跨资源组不重置。源ID白名单与数据隔离检查已通过；测试参考不进入训练观察。Teacher类别提示不进入学生输入，不设置最终类别配额。

真实Qwen3 / LLaMA-Factory模板对全1966条检查通过，最长3862 tokens，因此本轮总序列上限4096已经覆盖完整输入与输出。1024是供翻译器的软证据预算，与4096的选择器训练序列长度不同。

## 参数与优化目标

| 项目 | 96GB候选方案 |
| --- | --- |
| 模型/框架 | Qwen3-8B / LLaMA-Factory 0.9.5 |
| 微调 | 非量化基座的BF16 LoRA，all linear targets，rank=16、alpha=32、dropout=0.05 |
| 模板/监督 | qwen3_nothink；显式JSON分析与ID，completion-only loss |
| 总长度 | 4096；动态padding，不做packing |
| 单卡batch/累积 | 8 × 2，有效batch=16 |
| 优化器/学习率 | adamw_torch_fused，1e-4，cosine，warmup_ratio=0.05 |
| 训练计划 | 固定2 epoch，约246个optimizer updates；约13个warmup updates |
| 检查点 | 每25次更新及epoch末保存完整状态，保留最近3份；最终采用第2轮pi_SFT |
| 注意力/缓存 | PyTorch SDPA，训练use_cache=false |
| 显存/数据加载 | 梯度检查点起始开启；预处理与DataLoader各2 workers |
| dev/test | 无dev，不按测试选checkpoint，不进行训练中外部API评分 |

新增显存优先用于更大真实batch和减少激活重算。有效batch保持16，使更换硬件后训练更新规模仍与原方案一致；不因显存增大就增加epoch、LoRA rank或序列上限。约246次更新是按当前数据与单卡batch估算，实际以Trainer日志为准。

BF16基座张量约15.3GiB；训练还需要适配器/优化器、激活、logits、attention workspace和CUDA运行时，不能从基座大小推断训练峰值。暂用已有SDPA，不新增FlashAttention或Liger依赖；实际Blackwell支持仍须通过GPU训练步验证。

## 换卡后的最小调优

只用训练集最长的一小批真实样本比较以下三组，每组约2个optimizer steps，正式训练均从同一原始基座重新开始。记录峰值allocated/reserved显存及稳态tokens/s，压力样本包含已测最长3862 tokens；正式数据不为通过压力检查而裁短。

| 组合 | 单卡batch | 梯度累积 | 梯度检查点 | 用途 |
| --- | --- | --- | --- | --- |
| A（当前起点） | 8 | 2 | 开 | 扩大真实batch，保留激活节省 |
| B | 16 | 1 | 开 | 进一步减少累积，检查logits/激活峰值 |
| C | 4 | 4 | 关 | 利用显存省去重算，比较真实吞吐 |

关闭检查点须同时设置 gradient_checkpointing=false 与 disable_gradient_checkpointing=true；已核对安装版本，后者控制LLaMA-Factory的模型准备逻辑。只改Trainer字段可能仍会启用检查点。

选实际吞吐最高且最长batch有余量的配置。以实际设备报告显存约80–90%作为起始容量目标，留出动态padding、workspace和保存时的波动；不以nvidia-smi显示接近100%作为成功标准。若A仍OOM，退至batch=4、累积4、检查点开；不静默截断数据。探测输出单独保存，不复用到正式训练或DPO。

候选配置已同步到服务器 /root/autodl-tmp/LLM_Project_sft_20261102。现有实际训练入口是 .venv-training/bin/llamafactory-cli train configs/training/qwen3_8b_sft_96gb.yaml；本次不运行该命令。正式启动前先确认GPU可见、BF16支持、数据质量决策及上述最小GPU步骤。

## 训练结束后的顺序

固定第2轮SFT模型并导出冻结pi_SFT，再从同一冻结状态由该学生自采样2–4条不同选择，经固定外部翻译器和训练侧独立评分构造DPO偏好。主DPO两侧都来自pi_SFT，不用Teacher动作直接代替学生偏好；DPO采样/评分入口尚未实现。最终在冻结测试集评测，测试结果不回用于选择SFT checkpoint。

## 依据与验收记录

- 真实数据与CPU监督检查：本地 artifacts/sft-readiness-2026-11-02/，同名服务器目录保留报告。
- [NVIDIA RTX PRO 6000 96GB规格](https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/)。
- [LLaMA-Factory参数](https://llamafactory.readthedocs.io/en/latest/advanced/arguments.html)及[v0.9.5检查点控制实现](https://github.com/hiyouga/LLaMA-Factory/blob/v0.9.5/src/llamafactory/model/model_utils/checkpointing.py)。
- [PyTorch 2.8 CUDA显存管理](https://docs.pytorch.org/docs/2.8/notes/cuda.html#memory-management)。

## 2026-11-02：实际GPU运行与SwanLab续训

新机为RTX PRO 6000 Blackwell Server Edition，设备报告约94.97GiB可用总显存，torch 2.8.0+cu128支持sm_120且BF16矩阵计算通过。保留盘上main ae3fe6b工作区、已核对数据与模型，不重新采集轨迹。

最长16条真实样本的两步压力比较：8×2开启检查点完成，峰值allocated=83,036,484,096 bytes（约77.3GiB）、reserved约93.1GiB，第二步约25.43秒、有效输入/输出约2139 tokens/s；16×1开启检查点和4×4关闭检查点均OOM。选择8×2开启检查点。reserved包含可复用缓存，不能当作活跃张量占用；压力吞吐不等于完整训练吞吐。

新增 scripts/run_selector_sft.py，通过原LLaMA-Factory训练入口运行。每25个optimizer updates、epoch末及正常请求停止时保存；仅在adapter、optimizer、scheduler、RNG和Trainer状态文件齐全后写resume_ready.json。续训只选最新完整标记目录，保留两轮总计划及ignore_data_skip=false，不重置warmup或从头重播训练集。

正式第1步保存后已实际恢复，optimizer moments存在，scheduler.last_epoch=1，与global_step=1一致，恢复LR=7.6923076923e-6。后因用户要求更换监控，在第3步安全停止，LR=2.3076923077e-5，保留checkpoint-3；后续从此继续。检查点只保留最近3份，强制断电最多回退到上次完整保存，不声称零步损失或位级确定性。

W&B API网络403，早期离线片段原样保留。按用户最新要求换为SwanLab 0.10.3，标准凭据存储登录，不在源码/配置写入key。切换只放行report_to从wandb到swanlab，其他训练参数、模型、数据和版本必须一致；run.json保存迁移前后配置哈希和断点。SwanLab固定run ID，首次allow创建，之后must恢复，并以global_step记录指标；早期本地指标在首次SwanLab连接时补入。

服务器工作目录仍为 /root/autodl-tmp/LLM_Project_sft_20261102。正式日志 artifacts/sft-pro6000/train-swanlab.log；进度、完整检查点、固定run ID及SwanLab链接在 outputs/selector_training/sft_96gb/。启动器捕获SIGTERM/SIGINT，在当前optimizer update结束时保存退出；SIGKILL/断电仍需从最近完整检查点恢复。

续训入口为 .venv-training/bin/python -u scripts/run_selector_sft.py --resume。同目录加锁防止重复启动；resume-validation.json核对真实optimizer与学习率恢复。未完成全量语义审核的数据标记继续保留，不把训练loss作为翻译质量结果。

## 2026-11-03：本次SFT完成

服务器progress.status=complete、global_step=max_steps=246、epoch=2.0、最终学习率0；根目录最终LoRA adapter与checkpoint-246均存在，checkpoint包含optimizer/scheduler/RNG/Trainer状态及完整标记。正式运行峰值allocated约77.5GiB，reserved约93.7GiB，包含缓存；上述为训练资源记录，不是翻译质量收益。模型、数据、日志与凭据留在服务器，代码与配置入库。SwanLab的阶段汇总使用summary/命名空间，与逐步train/曲线分开。
