# 新数据集实体关系提取：本地运行前检查

> 后续执行更新：用户已明确要求开始实体关系提取。已归档旧STOP并在本地启动graph阶段，先三个训练侧小批，再在检查后续跑其余分片；不启动Teacher／SFT。实时状态以输出目录的pipeline.json及report_multidomain_extraction.py为准，下文“本轮仅预检”描述为前一轮交付记录。

日期：2026-11-03。当前项目目录：`/Users/march/Desktop/LLM Project/LLM_Project_main`。

**本轮只检查并修改运行前代码、提示词和配置，没有执行真实API请求、实体提取、Teacher采样或SFT。** 原始语料、参考、旧提取库及STOP标记均保留。

## 保持不变的协议与数据范围

继续使用 [game-graph/1.1](EXTRACTION_SCHEMA.md)，Python schema 与 [JSON Schema](../configs/extraction.schema.json) 一致；不增加实体类型或关系，不改输出字段。

- 六种实体：character、skill、item、location、organization、quest。
- 九种关系：MENTIONS、DEFINES、SPOKEN_BY、ADDRESSED_TO、MEMBER_OF、LOCATED_IN、COUNTERS、USES、PART_OF。
- 输入严格为四列`id,source_text,text_type,context`。稳定出现位置ID不合并，缺少角色、场景或分支资料时保留unknown。
- 每个新闻上游文件／小说章节仍为独立project；批次不跨project、语言方向或划分。无序处理，不用邻接行证明剧情顺序或实体关系。
- 只读取`source/`、源清单和提取清单；`offline/references.jsonl`不参与提示词渲染或提取。GuoFeng仍按既定adaptation_seen用途登记。

准备目录为`outputs/multidomain_sft_2026-11-03/`，本轮已核对全部3213条源记录的ID、源文哈希、方向和划分：

| 分片 | 源记录 | 提取单元 | 新配置初始批次 |
| --- | ---: | ---: | ---: |
| News Commentary train En→Zh | 1044 | 20 | 20 |
| News Commentary train Zh→En | 1042 | 20 | 20 |
| News Commentary留出 En→Zh | 258 | 5 | 5 |
| GuoFeng TEST_2 Zh→En | 869 | 12 | 22 |
| 合计 | 3213 | 57 | 67 |

留出新闻只提取英文源图，未来不生成训练Teacher标签，也不把其中文参考反转成训练源文。上游原始文章／原著身份尚未补充核实，现有边界仍如实标为预处理文件／上游标签。

## 提示词修改

[discover_graph.txt](../prompts/memory/discover_graph.txt) 在已有通用化基础上补齐新闻和小说约定：

1. 中文和英文原文都适用；真实人物仍映射character，机构／地名／实物使用既有类型，不要求存在游戏世界。
2. 政策、抽象概念、事件和经济指标不强行变为skill、quest或item；可留作源文，后续由软术语记忆处理。
3. 人名、地名使用实际源文表面形式；不凭外部常识补全姓名、国籍或任职关系，不输出译名。
4. 新闻中提到某人不等于该人说话；SPOKEN_BY／ADDRESSED_TO必须有当前发话角色的源文或上下文依据。
5. 保留合理局部语义推断及basis/reason、证据定位、否定／假设／历史事实约束和既有原生条件处理。
6. 使用简短充分引文，去掉重复实体、重复证据和重复边。某文本已经用DEFINES连接实体时，不再为同一对端点重复MENTIONS；其他出现位置仍保留自身的MENTIONS。

提示词保留中文新闻、中文小说和游戏的示例；词表、端点方向仍由Python schema动态注入，不维护第二套定义。减少冗余不意味着只抽实体名或放弃关系证据。

## 一次进入多少句子

独立配置：[multidomain.extraction.json](../configs/multidomain.extraction.json)，不修改Flare／像素地牢／Naev历史配置。

| 参数 | 当前起点 |
| --- | --- |
| 单批记录上限 | 64条原始记录；不额外拆句或合并occurrence |
| 记录输入估算预算 | 8000 tokens |
| 图输出估算预算 | 14000 tokens，沿用现有`192 + row_tokens/2`启发式 |
| 模型最大输出 | 16384 tokens，关闭thinking |
| 项目总上下文上限 | 32768 tokens；是项目主动限制 |
| Existing catalog | 最多48项、1200 tokens，预检另加200 tokens序列化余量 |
| 单元并发 | 默认2；同project的批次串行，保留实体复用 |
| 校验和拆批 | 保留keep_valid_items及既有格式错误／截断拆半机制 |

实际批大小取记录数、输入预算和预计输出预算三者约束，不是每次强行塞64句。本地数据得到**平均47.96条、最大61条**；45份新闻源视图各一批，12份小说单元共22批。少量小说尾批仅1条，是不跨章节且保留输出预算的结果，不为了填满批次合并不同项目。

对同一份新提示词、同一组源数据的离线比较：

| 配置 | 初始批次 | 空catalog提示词输入估算合计 |
| --- | ---: | ---: |
| 原游戏64条／5000输入／12288预计输出 | 83 | 365000 tokens |
| 新配置但最多32条 | 125 | 439466 tokens |
| 当前64条／8000输入／14000预计输出 | 67 | 336637 tokens |

相对原配置，初始请求数减少约19.3%，空catalog输入估算减少约7.8%。它节省的是请求和重复提示开销，不承诺总费用同比下降：最终计费还取决于实际图输出、cache、时段和拆批。采用现有[官方Flash峰时cache-miss费率](https://api-docs.deepseek.com/quick_start/pricing/)保守记账，输出上限与总上下文满足[API参数约束](https://api-docs.deepseek.com/api/create-chat-completion/)；本轮未发请求验证账号可用性。

预检按完整渲染提示词＋catalog预留＋最大输出检查每一批，最小上下文余量约9066 tokens。所有token数均为现有UTF-8 bytes/3启发式，不冒充服务商精确分词。67是正常初始请求数；截断／解析失败拆批可能增加调用。尚未测新提示词的真实召回、截断率或吞吐，先运行少量真实源图才能确认是否应调整。

## 入口与当前预检报告

新增[离线检查入口](../scripts/check_multidomain_extraction.py)与[复用的预检函数](../src/localization_agent/data/extraction_preflight.py)，不新建一套批处理器。使用新项目源码，暂借相邻旧项目已有CPU解释器，不安装新依赖、不复制密钥：

```bash
cd '/Users/march/Desktop/LLM Project/LLM_Project_main'
PYTHONPATH=src ../LLM_Project/.venv-cpu/bin/python scripts/check_multidomain_extraction.py \
  --sources outputs/multidomain_sft_2026-11-03 \
  --output artifacts/multidomain-preflight-2026-11-03/report.json

PYTHONPATH=src ../LLM_Project/.venv-cpu/bin/python scripts/run_multidomain_collection.py \
  --sources outputs/multidomain_sft_2026-11-03 --dry-run --stage graph
```

两条命令已实际运行，模型调用均为0。详细报告包含逐单元批大小、输入／输出估算、源清单和提示词哈希；当前缺密钥环境变量、缺默认`.env.extraction`，旧STOP仍存在。新目录尚无自己的`.venv-cpu`，上述解释器复用只用于当前本地检查，不把旧项目源码导入为本项目代码。

流水线已作以下修正：

- 默认`--stage graph`，图完成即退出；只有显式`--stage all`才进入Teacher阶段，避免建图命令意外触发轨迹采样。
- 默认dry-run；真实调用仍要求`--execute`，并尊重STOP。当前仅预检的指示不解除STOP，也不加载旧项目密钥。
- 公开提取使用独立配置，不再静默从像素地牢配置覆盖预算；dry-run不写入冻结执行配置。
- 真实执行前同样检查完整源清单和提示词容量；重用完成图时核对源、方向、schema、提示词、模型、批量和catalog配置，旧空图不能因状态complete而跳过新提示词提取。
- 本轮未启动任何后台进程，不自动连接服务器或推进Teacher。

未来获得提取执行指示后，先处理凭据与STOP，再以`--execute --stage graph`运行三个源图小批，检查后加`--continue-after-pilot`扩量；这些真实请求本轮未执行。Teacher阶段另行启动，不是运行前检查的一部分。

## 实际验证及边界

现有实体提取运行检查8项、自动建图检查9项通过；新增1项集成检查确认Zh→En方向正确、offline参考不读取、graph-only不会调用Teacher准备或采样。合计18项均为离线检查，不代表真实模型质量。

跨单元实体身份不归并；不新增模型语义复核，不删除失败候选，不把program_only升级为人工验收。正式启动后的抽查重点是中文新闻不再误空图、人物不被强制设成说话人、小说指代有可定位依据，以及输出是否频繁截断。
