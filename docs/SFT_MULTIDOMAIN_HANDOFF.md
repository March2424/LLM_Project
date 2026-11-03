# 多领域SFT执行交接

> 最新执行指示：用户已授权开始实体关系提取，已在LLM_Project_main启动本地graph小批，检查后继续全量；Teacher／SFT不启动。旧STOP和launcher已归档，新进度／停止命令见[提取运行说明](MULTIDOMAIN_EXTRACTION_RUNTIME.md)。下文旧停止指示已由本次提取指示替代，仅保留历史。

> 新目录交接：2026-11-03当前工作区为`/Users/march/Desktop/LLM Project/LLM_Project_main`。已完成[实体提取运行前检查与修改](MULTIDOMAIN_EXTRACTION_PREFLIGHT.md)，新配置67批、模型调用0；下文旧目录／服务器状态作为历史保留。本轮未启动建图、Teacher或SFT。

更新：2026-11-03。正式设计见 [SFT_MULTIDOMAIN_DESIGN.md](SFT_MULTIDOMAIN_DESIGN.md)。

**用户最后指示：本次只完成代码和Markdown，不建图、不采样、不做SFT。执行已停止；不得根据下文历史启动命令自动恢复实验。**

## 本轮决定

- 游戏＋News Commentary双向＋GuoFeng TEST_2 Zh→En训练；公开数据统一按无序资源处理。
- GuoFeng TEST_2同时训练和评测，仅报告训练内seen结果；游戏test和News Commentary文件留出仍独立。
- 源图按分片一次提取、固定快照跨阶段复用；运行生成的TM、术语、摘要与角色笔记按方法／阶段／方向／单元隔离。
- 不增加专项诊断，不重新生成旧游戏轨迹，不覆盖旧SFT模型。

## 本地已完成

工作目录：`/Users/march/Desktop/LLM Project/LLM_Project`。在已有大量未提交修改的main上追加本轮文件；未提交、推送或重置。

数据来自相邻的`LoongDocMT-main/data/news_commentary_v18.1`及`DocMTAgent-main/data/guofeng_v1/TEST_2`。`scripts/prepare_multidomain_sources.py`已执行，输出`outputs/multidomain_sft_2026-11-03/`：新闻训练En→Zh1044、Zh→En1042，新闻留出En→Zh258，GuoFeng869；共57个提取单元。源文与离线参考分离，GuoFeng不额外导入后缀副本。

57单元现有提取入口dry-run通过，模型调用0；6项Teacher运行测试通过，含新增Zh→En初始化与恢复验证。已补`prepare_multidomain_teacher.py`和`run_multidomain_collection.py`，复用已有提取和采样实现；不增加公共图schema或独立记忆系统。

此前按“不需要上限”执行了三个源图小批，共4次调用，估算0.0194304 USD。中文新闻小批返回空实体图；已改用通用领域提示措辞，但用户停止后未做新的真实模型验证。全部旧小批、原始响应和费用归档到`pilot_game_prompt_graphs/`及`archived_graph_usage.json`，没有删除或重标为新提示词结果。

已核对PID59523退出，本轮提取／轨迹进程不存在。当前`pipeline.json`和`launcher.json`写明`stopped_by_user_code_docs_only`；没有后台自动续跑。入口默认仅dry-run，付费模式需显式`--execute`且移除本轮STOP标记，未来必须先获得新的执行指示。没有新增Teacher轨迹或GPU训练。

提示词改动后的6项Teacher运行验证、9项自动建图验证通过；不代表通用提示词已通过真实模型验收。后续旧游戏SFT输入首句需按新通用学生模板从观察重建并保留转换审计，rationale／selected_ids及旧记录本身不修改。

## 服务器镜像

目标为用户指定PRO6000机器的`/root/autodl-tmp/LLM_Project_sft_20261102/docs/`，文件为本交接和`SFT_MULTIDOMAIN_DESIGN.md`。对应代码另以`artifacts/multidomain-sft-20261103-code-docs-stopped/`快照保存；工作目录只新增两份md和无API语料准备脚本，其他运行源码不覆盖。源语料、参考正文和现有实验数据不在同步包内。传输及SHA-256核对记录保存在本地`artifacts/multidomain-sft-2026-11-03/`。

不覆盖服务器旧配置、数据集、输出、SwanLab run或训练代码。文档中的本地数据路径不是服务器已拥有该语料的证明。服务器侧副本是交接材料，不代表新方案已训练。

## 待办和实际限制

1. 本次执行已由用户取消，后续只保留代码／文档交付；不继续使用此前无费用上限的执行指示。
2. 未来若重新要求执行，先验证新通用提示词，再提取分片；`game-graph/1.1`无需扩词表，源图库和既有游戏输出保持不变。
3. Teacher准备层已接通分片路径，collector已按源Occurrence初始化方向。继续监测真实方向、同项目记忆、Teacher选中上下文及失败状态；离线测试不当作真实质量验收。
4. 新混合训练目标约3866有效示范，公开语料冷启动约520条；数量不是质量合格承诺。完成实际语义抽查与模板长度检查后才确定最终条数和显存参数。
5. News Commentary只核实上游文件边界，未核实原始文章身份；GuoFeng保留上游BOOK/CHAPTER标签但未完成原著归属核验。不得将文件留出写成已证明的书籍／原始文章留出。

后续执行者必须读取设计及最新`summary.json`，保留原始数据与已付费响应；不在文档里写入API或SSH凭据。


## 2026-11-03：本次main同步

本次主线交付纳入服务器代码快照与文档；快照中的旧重试实现已与最新main三方合并，保留有界重试、费用审计和失败隔离。原始快照仍留服务器artifacts，完整语料、参考和实验库不入本次提交。用户当前仅要求同步仓库与本地目录，不恢复之前已停止的实验。
