# 多领域数据检查与服务器交接

日期：2026-11-03。本次范围为审查本地新代码、提交main、更新指定服务器及上传SFT所需数据，不启动付费采样或模型训练。旧游戏SFT模型、optimizer和scheduler断点保持原目录。

## 当前结论

混合SFT为3165条：Flare1169、Shattered797、GuoFeng Zh→En509、新闻En→Zh450／Zh→En240。全量共同prompt／ID动作、逐条来源及新示范与冻结job对应检查通过；新闻同文件双方向的train／test隔离保持，GuoFeng TEST_2仅报告seen结果。57个校正源图无翻译run，共994有效实体、1720边；图和标签仍为program_only，不代表全量语义批准。

本次29项相关离线检查通过，包含Teacher选择纠正、先选择再翻译、双向恢复、HTTP响应读取和graph-only隔离。完整序列最长5142 tokens，8192配置覆盖；框架级loss mask由服务器CPU检查单独记录，不加载权重、不产生训练更新。GPU当前未分配，8K最长样本显存检查尚未完成，4×4微批方案仍为候选。新训练从原始Qwen3-8B初始化独立LoRA，不续旧游戏optimizer。

采样状态stopped，341历史失败、470 pending保留，STOP随数据上传；不自动恢复。新闻Zh→En尚有360目标未处理，不复制补数。

## 服务器目录

主工作区：`/root/autodl-tmp/LLM_Project_main_20261103`，分支main。旧`LLM_Project_sft_20261102`保留，不覆盖旧模型或SwanLab run。

以下相对路径位于新主工作区：

| 路径 | 用途 |
| --- | --- |
| `outputs/selector_training/multidomain_2026-11-03/` | 3165条混合示范、注册、逐行provenance、哈希和resolved配置 |
| `outputs/selector_training/data/` | 原1966条游戏训练数据及来源 |
| `data/` | 仓库游戏数据、许可和溯源；包含离线参考，不能整目录送给模型 |
| `outputs/multidomain_sft_2026-11-03/source/` | 57个单元的源侧CSV，不含参考 |
| `outputs/multidomain_sft_2026-11-03/aligned_graphs_v1/` | 校正源图快照及纠正审计，各SFT实验可复用的源事实 |
| `outputs/multidomain_sft_2026-11-03/graphs/` | 提取原件、结果和费用审计 |
| `outputs/{flare_full_2026-10-31,shattered_full_2026-11-01,naev_full_2026-11-01}/graph.sqlite` | 三款游戏源图 |
| `outputs/cpu_preparation_2026-11-02_2000_context_v2/` | 游戏划分、源侧身份绑定及单独offline参考 |
| `outputs/teacher_multidomain_2026-11-03/` | 本次Teacher逐条记录、响应缓存和最终运行记忆，仅作审计，不作为新实验初始记忆 |
| `artifacts/retrieval_tokenizer/` | 冻结预算tokenizer |
| `outputs/server_stage_inputs_2026-11-03/` | 可直接定位服务器资源的输入索引及上传验证报告 |

上传文件按路径和SHA-256清单核对。原始冻结JSON中保留本地绝对路径以保持历史hash；新的`stage_inputs.json`和`teacher_plan_server/source_projects.json`才是服务器路径，不能直接用历史config恢复Teacher。没有复制本地虚拟环境、凭据、旧before_*整目录或无关日志。

## SFT边界

SFT只读取混合目录的instruction/input/output投影。所有输入prompt mask为-100；完整rationale、selected_ids及assistant结束标记参与监督。训练命令沿用`run_selector_sft.py`和多领域配置，启动前需完成真实8K GPU检查并确认独立output_dir。完整断点包含adapter、optimizer、scheduler、RNG及Trainer状态；同实验续训才使用--resume。


用户最新指示：当前不进行DPO相关工作，本次不准备偏好数据、奖励参考清单或DPO配置。
