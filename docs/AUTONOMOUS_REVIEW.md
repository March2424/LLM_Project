# 自主审查与路由：研究依据及工程策略

日期：2026-11-10。用户要求删除人工确认／直接翻译步骤，改由大模型自主判断。实现位于 `workflow/quality.py`、`workflow/langgraph_game.py`，提示词为 `prompts/memory/review_autonomous.txt`。模型统一为 DeepSeek Flash；翻译、审查、修复使用独立调用，审查不产出替代译文。

## 查阅的原始资料

| 资料 | 原方法／建议 | 本项目借鉴的内容与边界 |
| --- | --- | --- |
| [TEaR，NAACL Findings 2025](https://aclanthology.org/2025.findings-naacl.218/)；[论文全文](https://aclanthology.org/2025.findings-naacl.218.pdf) | Translate、Estimate、Refine，将错误反馈送回翻译阶段 | 先识别具体错误再决定是否修复，使用类别与严重程度；不宣称完整复现论文或获得其实验收益 |
| [LLMRefine，NAACL Findings 2024](https://aclanthology.org/2024.findings-naacl.92/)；[论文全文](https://aclanthology.org/2024.findings-naacl.92.pdf) | 细粒度可执行反馈；原论文使用学习的反馈模型和模拟退火搜索 | 采用原文／译文错误片段与纠正指令，不仅返回一个分数或好坏；本项目没有训练该反馈模型、没有实现模拟退火 |
| [Self-RAG](https://arxiv.org/abs/2310.11511) | 训练反思标记，自适应检索、生成及批判 | 只借鉴按信息需要选择证据的思路；本实现为提示式候选重选，没有复现反思标记训练或 RL |
| [IGDA 游戏本地化实践指南](https://igda.org/resources-archive/best-practices-for-game-localization/)；[指南 PDF](https://igda-website.s3.us-east-2.amazonaws.com/wp-content/uploads/2021/04/09142137/Best-Practices-for-Game-Localization-v22.pdf) | 熟悉游戏、术语与风格指南、语境和语言 QA | 检查术语／角色／机制与上下文一致性；指南为 2012 年第二稿、2021 年重新发布，属于行业实践建议，不是强制标准 |
| [Microsoft：消息格式与占位符](https://learn.microsoft.com/en-us/globalization/internationalization/message-formatting)；[准备本地化](https://learn.microsoft.com/en-us/windows/apps/design/globalizing/prepare-your-app-for-localization) | 独立语境、完整资源、变量与标记处理 | 保留原生上下文、占位符和技术限制；不凭经验给所有游戏 UI 套一个固定字符上限 |

这些资料没有给出适用于所有游戏的自动放行分数。下面的动作协议、次数限制、格式兜底和停止条件是本项目的工程选择，不是论文原有统一标准。

## 程序检查与语义审查分工

程序检查：占位符／标签数量和内容、未编号 printf 参数类型／顺序、标签嵌套、提供了才检查的 `metadata.max_length`（Unicode 字符数），以及现有已批准硬术语。编号／命名参数允许合法换序；`%%` 为转义字面百分号，不作为参数。没有检查游戏画面实际溢出、字体宽度或运行时引擎行为。

模型检查：遗漏、误译、否定、数量和机制条件、适用术语、人物口吻、文本类型、语言流畅性及无依据的增补。没有批准术语表时不编造官方译名，不因合理音译／同义表达／个人措辞偏好强制修改。不在上下文中出现的角色经历或剧情关系保持未知。

## 四种自动动作

| 动作 | 触发情况 | 程序执行 |
| --- | --- | --- |
| accept | 没有具体缺陷，技术检查也通过 | 构造绑定本草稿的 TranslationCheck，提交译例并独立更新软记忆 |
| repair | 已有源文／资料足以纠正明确错误 | 将定位片段、类别、严重程度、纠正指令送修复模型，新草稿重新审查 |
| reselect | 有明确证据需求，或当前选择造成误导 | 在固定 ReadView／图快照的最多 16 个软候选中选择 0–5 项，保留所有硬项并遵守 1024 软 token；用新输入重生成并重新审查 |
| stop | 关键事实缺失或矛盾，且不能忠实保留歧义 | 以 unresolved 结束当前条目，不写正式 TM，继续其余样例 |

`reselect` 不是全局重新搜索：它重新执行原快照检索以校验候选，再改变选用子集。空选可移除误导软资料。没有新资料变化时返回 unresolved_context，不能用相同输入无限循环。超出池的 ID／重复 ID／超数量／超 token 动作由现有 M3 接口拒绝。初次选择继续用固定策略，未来训练选择器仍有独立入口。

审查器可以看到两部分，分别标注：译者实际使用的 `actual_translator_context`，以及仅用于重选的 `reselection_observation`。后者的未选证据不冒充已输入译者的资料；完整审查提示词在 model_calls 中留存，实际翻译依赖仍只记录真正选中的项。

## 可执行反馈与放行条件

返回 JSON：`action`、简短 `reason`、`issues`、`selected_ids`。每项问题需要 category、severity、当前原文／译文中的逐字引文及 instruction。至少一个定位引文非空；引文必须存在，其他来源不能伪装成本句引文。程序校验定位不等于证明语义判断正确。

如果模型返回 accept，但有具体问题或程序技术检查失败，实际动作会改成 repair，记录原模型动作以供核查。无缺陷的 repair、无效 JSON、非法资料选择会明确失败，不能回退成通过。没有使用自报置信度或任意质量分数作为放行保证。

## 有限循环、恢复和失效

默认两次 repair、一次 reselect。达到上限分别返回 repair_limit／context_limit；同一上下文下重复历史译文返回 stalled。每个新草稿／新上下文都必须重新审查，不能继承原通过结论。

审查绑定 draft ID/版本、目标 hash、context hash、策略提示词 hash；提交前重新核对技术规则和来源有效性。精确相同审查请求可复用已记录响应，正常提交沿用现有回执防重。API 响应到持久化之前崩溃仍可能导致恢复时重新请求，不能承诺外部 API 恰好一次。

新运行保存 `workflow_version=autonomous/1`、策略版本／hash和两个次数上限。旧人工运行只允许 status 查看；不把历史人工通过伪装成自动判断。运行期间换审查提示词要求新建运行，避免混用策略。

## 已删除及保留

删除当前执行入口中的 human_review／interrupt／Command(resume)、交互审稿菜单、review/interactive 命令、手填译文参数和 GameSession.edit_draft。共享的 complete_review／TranslationCheck 仍用于自动审查的批准提交，保持 M2/M3 与已有其他流程兼容。旧运行数据库、原始调用和历史说明不删除。

## 限制

同一 DeepSeek 服务承担多个独立调用，仍存在同源偏差。自动通过不等于独立评测或人工验收。没有引入 COMET／测试参考、训练奖励、人工参考译文、完整游戏 LQA 或发布审批。当前源侧 CSV 演示仍没有全量实体关系边。正式质量收益需要另行冻结数据与评测协议。
