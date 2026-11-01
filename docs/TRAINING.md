# 上下文选择器训练协议

状态：协议待训练实现与实测后冻结。已有 [Loong 训练分析与资源方案](LOONG_RL_PLAN.md)，其配置、规模和时间均为建议，尚未执行训练。

**作用**：固定 M4 的学习目标、轨迹构造和验证方法，使组员能复现 Loong 启发的独立选择工具训练。

## 已确定：Teacher 软引导与自主选择（2026-11-01）

- 只在 teacher 构建候选轨迹时加入任务相关提示：专业名称优先查看术语；明确人物的对话优先查看角色笔记；按需求查看近期相关 STM、远距离相关 LTM、主题摘要及图证据。模板见 [teacher_context_selector.txt](../prompts/selection/teacher_context_selector.txt)，完整边界见 [训练方案 §3.3](LOONG_RL_PLAN.md#33-teacher-引导与策略自主选择的边界)。
- 这些是软提示，teacher 可跳过任意软类别、只选图证据或空选择；不要求选齐五类。候选召回覆盖与最终入选配额分开，缺失类别不补造。
- 同一冻结状态最多比较四个方案：固定基线、图优先/仅硬项对照、带引导 teacher、无类别引导 4B。动作经同一翻译环境和离线评分比较，teacher 不默认获胜。
- SFT、DPO 与最终 Agent 共用 [通用选择模板](../prompts/selection/context_selector.txt)。SFT 输入不带 teacher 类别建议，label 为筛选后的完整 rationale＋selected_ids JSON；DPO 在同一通用 prompt 下比较 chosen/rejected。Teacher 生成 prompt 与来源单独审计。
- 偏好依据下游翻译质量，质量相当时按既定规则比较成本；不设置类别配额、类别覆盖奖励或模仿 teacher 的奖励。后续在线 RL 如另行实现，沿用此边界；当前主线仍为 SFT＋DPO。
- 允许 ID、作用域/快照、硬约束与总预算保持程序检查。本次未修改 16 项候选池、0–5 项动作、1024 软 token；总项数是否放宽另行冻结。

新增 teacher 模板尚未接入采样器；STM/LTM 的观察标签、轨迹导出、训练和有/无引导诊断均待实现，不将未审记忆试跑当作合格训练轨迹。

后续落实：基础模型、候选池与冻结翻译环境、观察扩展、采样与评分实现、成本与偏好阈值、数据导出和训练超参数、开发集选模、硬件与预算、产物位置及失败恢复。建议参数见训练方案，不代表已冻结或执行。

记录每次策略/奖励修改的版本，区分“计划”“实际运行”和“验证结论”。测试集不参与偏好构造或调参；初始训练范围见 [README 的 M4](../README.md#m4loong-启发的可训练上下文选择工具)。

## 已确定：简短思考参与训练（2026-11-01 后续修订）

用户要求轨迹包含简短思考，且 DPO 中思考也参与训练。Teacher、学生与部署的统一输出为 rationale 在前、selected_ids 在后的 JSON；rationale 为 1–3 句与候选 ID 关联的决策分析。SFT 对理由和 ID 全部计算 completion loss；DPO 的 chosen/rejected 均保留各自动作的完整理由，所有 completion tokens 参与 log probability，不将理由移入 prompt 或 mask。执行器只按 selected_ids 打包证据，理由不成为翻译事实记忆。

用户进一步明确：分析必须展开为显式自然语言，说明当前翻译需求、候选实际内容与取舍，通常 1–3 句，不能仅写“c02 相关，选 c02”。ID 可以辅助定位，最终 selected_ids 继续用 ID。输入保留候选正文，分析概括关键名称/译法、事实条件或表达依据，不机械复制全池；展开分析随完整轨迹和 chosen/rejected 保存，参与 SFT/DPO。Teacher 类别软指导仍不进入学生 prompt。

Teacher 的类别软提示仍只用于采样输入；实际生成的简短理由则属于监督输出，两者不能混淆。相同 ID 但理由措辞不同不构造质量偏好，不奖励篇幅；程序基线无模型分析时只作对照，不虚构思考。选择器输出预留从原 128 调整为 256 tokens 的试采样起点，尚未执行。

原生 thinking 模式不等于上述显式理由格式：首版可关闭原生长思考，照样在 rationale 中学习简短分析。若采用原生 <think> completion，必须另外固定 chat template 和 loss 范围，并验证预处理保留思考 tokens。Qwen3-8B 是新增可选模型；单 GPU 48GB 的 SFT/DPO 可行性、参考策略与序列长度建议见 [详细方案 §6.1.1](LOONG_RL_PLAN.md#611-qwen3-8b-与-48gb-显存可行性待实测)，尚未租卡或运行训练。
