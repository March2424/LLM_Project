# 公开语料实体关系提取：本地运行

> 已完成：57/57单元、3213/3213源记录、67次请求，无失败单元；阶段停于graphs_complete_teacher_not_started。产出1023实体节点、1759边，语义问题及Teacher对齐缺口见[检查结论](MULTIDOMAIN_GRAPH_REVIEW.md)，不能把完成当语义验收。

日期：2026-11-03。用户已明确要求开始实体关系提取；仅运行graph阶段，不运行Teacher或SFT。配置与输入检查见[运行前说明](MULTIDOMAIN_EXTRACTION_PREFLIGHT.md)。

工作目录为`/Users/march/Desktop/LLM Project/LLM_Project_main`。输入3213条、57个独立源图单元，初始计划67批；最多64条／8000输入预算／14000预计输出、最大输出16384 tokens，两个单元并发，单元内按批次串行。先检查新闻两个方向和GuoFeng各一个单元，再继续其余分片。

后台使用相邻旧项目的CPU解释器和已有`.env.extraction`，不复制或打印密钥；`caffeinate`维持运行时唤醒。本轮费用不设货币上限，调用与估算费用持续记录；数据范围固定，不增加语料或运行SFT。旧STOP与launcher均已改名归档，旧小批结果和费用不覆盖。

首轮新提示词小批解决了中文新闻空图，但抽查发现贸易协定被归为quest、职业被归为skill等错误。补充明确反例后重做三个单元；首轮结果保存在`pilot_before_type_clarification_graphs/`，费用计入归档累计，不冒充修正后的结果。当前主线提示词、模型和批量配置由实际run_signature记录，运行中不热改提示词。

重新验证后，三个单元共199条源文均完成程序检查，仍有5个skill／quest类型样例待语义复核（包括协定／项目和语言名）。记录于`pilot-review.json`，未宣称反例已消除全部误判，也未将其升级为金标准。保留program_only候选后已启动剩余54单元的双路提取；具体进度以实时文件为准。

## 查看实时进度

下面命令只读，不调用模型；按Ctrl+C只退出监控，不停止提取：

```bash
cd '/Users/march/Desktop/LLM Project/LLM_Project_main'
python3 scripts/report_multidomain_extraction.py --watch 5
```

只看一次：

```bash
python3 scripts/report_multidomain_extraction.py
```

输出包含完成单元／总单元、完成源记录／3213、完成子批、API尝试、当前及归档累计费用估算、活动／失败单元。API回调在请求前记录预留费用，结果返回后按usage结算，运行中费用可能含在途预留；均不是账户账单。

## 查看控制台与停止

```bash
tail -f outputs/multidomain_sft_2026-11-03/pipeline-console.log
```

单元的详细日志位于`outputs/multidomain_sft_2026-11-03/graphs/<project_id>/console.log`，对应结果为`result.json`、源图为`graph.sqlite`。

请求安全停止：

```bash
touch outputs/multidomain_sft_2026-11-03/STOP
```

当前正在运行的单元完成后，不再开始新单元；单元内可能仍有剩余批次，因此不保证触碰STOP后立刻停止API调用。不建议直接kill，否则请求结果可能未知。停止／错误状态查看`pipeline.json`，已完成图与成功响应保留，不能把未知失败自动当免费重试。

## 结果与阶段边界

全部图完成后状态为`graphs_complete_teacher_not_started`，进程退出。实体关系仍标记program_only，完成并不代表经过人工语义审核。跨项目不合并人物身份，不导入offline参考。

进度说明和命令已经本地实际执行检查；本次没有连接服务器、没有新建翻译记忆，也没有启动教师选择轨迹或GPU训练。最终完成数／失败数／费用以实时文件为准，不把后台启动当完成。
