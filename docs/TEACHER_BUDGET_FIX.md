# Teacher失败处理、预算与SFT长度对齐

日期：2026-11-03。按用户要求完成基本测试后交付，不扩大验证范围；已恢复数据采样，不启动模型训练。

## 原因与现场保留

安全停止时已完成1371步：270冷启动、1101目标，760个程序有效选择、341个失败。339个为选择超过1024软token预算，2个缺selected_ids；有效选择均未因后续记忆失败丢失。不是主要由网络故障导致。

对339个预算失败按各自原ReadView重建候选，序列化对齐后未发现冻结状态差异。原动作中221个可落入1536预算、286个可落入2048预算；若仍保留6000字符限制，其中5个依然会失败。3072可覆盖334个，但本轮采用用户许可的适度扩大至2048；上述是离线边界分析，不是回改过去记录或新的质量结果。

旧运行完整归档为`outputs/teacher_multidomain_2026-11-03_before_budgetfix_<时间>/`。继续使用原逻辑目录`outputs/teacher_multidomain_2026-11-03/`，所有SQLite、ReadView、成功响应、已完成条目及费用累计保留；不回放341个旧失败，不倒填过去记忆，也不将原1024观察改成2048。

## 后续采样修复

- 软预算2048，配套字符上限12000，选择上限仍8项，硬约束保持固定。
- 只给Teacher增加预算可行性说明，明确单条也放不下的候选ID不能选，并要求保守核算standalone_tokens之和；不改变学生prompt模板或观察字段。
- 非法JSON字段／ID／超预算动作最多有一次验证反馈纠正，在同一冻结状态下重写完整rationale＋selected_ids。两次响应、实际成本反馈和调用费用都留审计；不直接裁剪ID，不套用旧rationale。
- Teacher输出上限512→1024，实际选择仍完整JSON；翻译／记忆模型和参数不变。HTTP收到length终止或JSON不完整仍失败记录，不将截断内容当标签。
- 进度同时显示historical_failed与since_upgrade，避免把341个历史失败当成修复后新增失败。历史原样保留，不能通过清零来展示改善。

新增同状态纠正／不提前翻译检查及原有运行检查共9项通过，未增加大规模质量评测。实验新阶段为budget_feedback_v1，模型、数据、费用和代码变化记录在config、parent配置及budgetfix-audit.json。

## 两批数据的prompt与Loss Masking

旧1966条已对齐游戏示范与当前760条有效新选择，共2726条，已用同一个schema／学生模板检查。观察字段一致、输出均为完整rationale＋selected_ids；预算值是任务条件，可以同时存在旧1024和新2048，不更改旧动作的原始条件。Teacher独有预算指导和纠正反馈不进入学生输入。

统一保持qwen3_nothink、train_on_prompt=false、mask_history=false、packing=false。监督对象是全部rationale／ID及assistant结束标记；输入prompt的labels必须全部为-100。`check_training_setup.py --verify-tokenization`新增逐token边界断言，框架处理器必须保留完整response并屏蔽完整prompt。

本地轻量长度核对沿用同一Qwen tokenizer及[LLaMA-Factory v0.9.5 qwen3_nothink文本模板](https://github.com/hiyouga/LLaMA-Factory/blob/v0.9.5/src/llamafactory/data/template.py)。目前完整序列最大5142 tokens，已有92条新样本的纯内容超过4096，旧4096上限不适合直接继续使用。

新增[多领域96GB配置](../configs/training/qwen3_8b_sft_multidomain_96gb.yaml)：`cutoff_len=8192`（本框架对应max_seq_length），候选micro-batch4×累积4，其他LoRA／LR／两轮计划不变。显存需训练前重新测，不声称已经过GPU压力验证。

导出阶段关闭tokenizer截断，逐行检查完整schema与长度，保存sequence-check.json；若最终数据超过8192，resolved_sft_config.yaml自动选择覆盖最大长度的更大上限，并保持有效batch16。不会静默切掉观察或标签。完整数据的实际LLaMA-Factory处理器／loss-mask检查及最长样本GPU检查仍是训练前门槛，不在本次采样中启动。

当前本地没有transformers／LLaMA-Factory训练环境，因此轻量文本模板核对不冒充框架级mask检查已通过；metadata明确framework_loss_mask_verified=false及gpu_profile_required。已有旧数据最大长度与此前真实模板报告相符，但不替代新混合数据的最终检查。

## 进度

### 1915步停滞修复（2026-11-03）

现场两个HTTP调用占住两个采样worker，接口反复返回503；其中单次调用持续数分钟，超过配置的90秒。旧`urlopen(timeout=90)`只约束socket空闲，`json.load(response)`遇到持续保活数据仍可一直等待。

适配器增加`total_timeout=180`秒：响应体按`read1`读取，每次以单调时钟检查总时限，并将socket读取超时收紧为剩余时间与原空闲超时的较小值。截止后关闭连接，按现有最多8次的瞬时故障重试处理，耗尽后保留失败；不无限等待，也不将保活内容当JSON标签。90秒空闲超时仍保留。仅为请求恢复，不改2048选择预算、模型、prompt、标签或记忆顺序。

协作停止无法结束原在途调用，受控终止旧worker后完整归档运行，再用既有upgrade／retry_call接口恢复未完成调用。原成功缓存、ReadView、记忆、历史失败和费用全部保留；被取消调用的服务端完成状态未知，可能重复计费，原估算不扣减，重试另行记录。归档路径及重试key写入运行目录`timeoutfix-audit.json`。基本检查覆盖成功响应与持续空白触发总时限，连同原9项恢复检查共11项通过，不扩大质量评测。恢复后首次确认进度已从1915推进至1920，两个原在途请求均已成功返回，历史失败仍为341。

页面保持[process.html](http://127.0.0.1:8765/process.html)，命令不变：

```bash
cd '/Users/march/Desktop/LLM Project/LLM_Project_main'
python3 scripts/report_teacher_trajectories.py --watch 5
```

采样完成后仍只导出有效选择与混合数据，不启动SFT。新阶段的错误和恢复效果以since_upgrade实时计数为准，不承诺全部1900目标都会有效。
