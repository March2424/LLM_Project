# 第三方来源与许可

## DocMTAgent / DelTA

- 作者仓库：[YutongWang1216/DocMTAgent](https://github.com/YutongWang1216/DocMTAgent)。
- 核验日期：2026-10-28。
- 固定提交：`6dde70acc9e60fd2533c2ca467eb327884d20dab`。
- 论文：[DelTA: An Online Document-Level Translation Agent Based on Multi-Level Memory，ICLR 2025](https://openreview.net/forum?id=hoYFLRNbhc)。
- 上游许可证：Apache License 2.0，完整副本见 [licenses/DocMTAgent-Apache-2.0.txt](licenses/DocMTAgent-Apache-2.0.txt)。该提交未发现单独的 NOTICE 文件。

### 直接移植并修改的文件

上游 `prompts/en-zh/` 和 `prompts/zh-en/` 中的 `src_summary_prompt.txt`、`tgt_summary_prompt.txt`、`src_merge_prompt.txt`、`tgt_merge_prompt.txt`，共八个文件，移至本仓库 [prompts/memory](prompts/memory)。

保留上游提示词正文；本项目增加文件来源说明，以及把输入视为数据、不额外推断、保留不确定性与条件的指令。提示词本身不能保证模型遵守这些要求，生成结果仍需核验。

### 参考后重新组织的实现

参考 [infer/infer_gpt.py](https://github.com/YutongWang1216/DocMTAgent/blob/6dde70acc9e60fd2533c2ca467eb327884d20dab/infer/infer_gpt.py) 的 `Context`、`History`、`Summary`、`RetrieveAgent`、`EmbeddingDict` 和顺序读写流程，并与 `infer/infer_qwen.py` 交叉核对。本项目把这些算法组织到 `memory/service.py`，改用共享类型、SQLite 和注入式模型接口；没有整份复制推理脚本或模型客户端。`terms.txt` 与 `retrieve.txt` 参照相应任务重写为 JSON 输出。

本项目新增作用域、实验运行隔离、审批、快照、版本及失效传播。不能将这些工程扩展描述为 DelTA 原论文的贡献，也不能将本项目词法检索默认配置标为原版 DelTA 复现配置。

此处记录第三方材料适用的许可，不替团队决定整个项目的发布许可证。
