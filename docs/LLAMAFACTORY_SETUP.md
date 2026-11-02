# LLaMA-Factory：无 dev 的选择器训练配置

更新：2026-11-02。LLaMA-Factory已安装，配置解析和离线夹具的精确模板/loss mask检查通过；正式新协议数据与GPU训练步尚未验证。

## 本轮固定方案

- 开源选择器为 Qwen3-8B，目标约 2,000 条有效 Teacher 分析＋动作，SFT 固定 2 个 epoch，使用第 2 轮最终模型作为 pi_SFT。
- 取消独立 dev，不做开发集 API 翻译/选模、early stopping 或 best-model 加载；原 dev 组并入 train，原 test/external_test 资源组不变。测试前冻结模型，不用测试分数反复调参。
- 思考以 JSON rationale 显式自然语言展开，selected_ids 表达最终动作；两者都参与 SFT/DPO completion 训练。使用 qwen3_nothink 模板关闭额外原生长思考，不屏蔽 JSON 中的分析。
- 翻译始终交外部 LLM，当前记忆翻译不是学生标签或人工金标准。取消 dev 不取消 Teacher 筛选、DPO 的训练侧评分及最终测试。

## 文件

| 文件 | 内容 |
| --- | --- |
| configs/training/plan.json | 约 2,000 条、2 epoch、无 dev 的实验意图 |
| configs/training/qwen3_8b_sft.yaml | 单卡 micro-batch=1、累积16、LoRA r=16、BF16、保存每轮、固定2轮 |
| configs/training/qwen3_8b_dpo.yaml | SFT 合并基座上新建 LoRA，初始1轮；无 dev，主 pair 必须来自 pi_SFT |
| configs/training/dataset_info.json | Alpaca SFT/DPO 注册；不含 dev 或虚构训练数据 |
| scripts/check_training_setup.py | CPU 配置/注册/数据结构预检，不加载模型权重或调用 API |

路径以服务器 /root/autodl-tmp/LLM_Project 为工作目录，SFT 模型指向 /root/autodl-tmp/Qwen3-8B；异机运行需显式修改。cutoff_len=4096 是初始配置，不允许默认截断真实观察；必须在正式运行前用相同 tokenizer/template 测量完整数据长度，不够时调整上下文/资源配置再冻结。

DPO 模型路径为 outputs/selector_training/pi_sft_merged，当前尚不存在。必须先把固定第 2 轮的 SFT 模型合并导出到新目录，再以其作为 DPO 基座/reference；不将原始 Qwen3-8B 错当 SFT reference。若有多卡，按实际 world size 调整梯度累积以保持有效 batch，不直接沿用单卡数值。

CPU 预检入口：

```bash
PYTHONPATH=src .venv-cpu/bin/python scripts/check_training_setup.py \
  --config configs/training/qwen3_8b_sft.yaml \
  --output artifacts/llamafactory-setup/sft-config-check.json
```

该入口仅复制注册文件并检查现有数据结构，不生成空数组冒充训练集。缺少真实轨迹时明确返回 real_training_data_not_generated。数据应先在采样/导出阶段完成状态、作用域、ID/预算与来源检查，不能只凭该结构预检宣称可训练。

## 依赖与状态记录

训练框架候选固定为官方 v0.9.5，保留上游 commit/包版本及实际依赖锁。训练环境 .venv-training 与现有 .venv-cpu 分开，可复用系统已有 torch，不修改基础环境。实际安装日志、版本及解析检查保存在 artifacts/llamafactory-setup/；未经过最小 GPU 步骤时，gpu_verified 保持 false。

上游依据：[LLaMA-Factory v0.9.5](https://github.com/hiyouga/LlamaFactory/releases/tag/v0.9.5)、[数据注册](https://llamafactory.readthedocs.io/en/latest/getting_started/data_preparation.html)、[官方 SFT 示例](https://github.com/hiyouga/LlamaFactory/blob/v0.9.5/examples/train_lora/qwen3_lora_sft.yaml)。仅配置上游框架，未将其代码复制进本项目。


## 实际 CPU 验收（2026-11-02）

服务器 .venv-training 已安装 LLaMA-Factory 0.9.5，复用系统 torch 2.8.0+cu128，并将 torchaudio 对齐到2.8.0。CLI 版本与 SFT/DPO 参数解析通过（CPU解析时仅覆盖 bf16=false/use_cpu=true，GPU配置仍保留BF16）。真实本地 Qwen3 tokenizer/template 的离线夹具检查确认 JSON rationale 与 ID 都进入 response；处理器 loss mask 的最终检查结果见 artifacts/llamafactory-setup/loss-mask-check.log。没有加载权重或执行GPU训练；真实数据长度和显存步骤仍待轨迹生成后验证。


2026-11-02审查修复：Teacher不再生成最终译文，独立翻译只推进记忆；候选池16、选择上限8、软预算1024。export --dataset-dir可直接落到注册的数据路径；check_training_setup.py --verify-tokenization对生成后的真实数据验证完整长度和分析loss mask。当前准备/迁移不代表已有足够训练数据，真实GPU步骤和后续DPO采样/评分仍未实现或执行。
