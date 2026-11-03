# 多领域SFT数据停止快照

2026-11-03按用户要求安全停止Teacher采样，状态`stopped`，没有在途调用，不自动续采，未启动混合SFT或DPO。已有数据适合进行第一轮混合领域选择器SFT，尚不能仅凭条数确认效果或充分收敛；新闻Zh→En偏少，训练时可适当平衡方向。语义质量仍为program_only，不冒充人工金标。

## 有效训练数据

| 来源 | 方向 | 有效条数 |
| --- | --- | ---: |
| 旧Flare | En→Zh | 1169 |
| 旧Shattered | En→Zh | 797 |
| GuoFeng TEST_2 | Zh→En | 509 |
| News Commentary v18.1 | En→Zh | 450 |
| News Commentary v18.1 | Zh→En | 240 |
| 合计 | En→Zh 2416；Zh→En 749 | 3165 |

新增1199条加旧游戏1966条。新增处理1950步＝410冷启动＋1540目标，目标中1199有效、341历史失败；冷启动不作为SFT标签。未处理470步＝110冷启动＋360新闻Zh→En目标，保留pending，不用复制样本补齐。新阶段2048预算后的439条选择有效，历史1024条件样本不回改。

GuoFeng TEST_2已参与训练，其后评测只能报告seen／训练内适配表现。新闻留出与游戏test仍独立，参考译文不进入本训练输入。

## 文件位置

主目录：`outputs/selector_training/multidomain_2026-11-03/`。

- `selector_sft_train.json`：实际混合训练数据，3165条JSON数组。
- `dataset_info.json`：LLaMA-Factory数据集注册。
- `provenance.json`：逐条来源、源ID、ReadView、运行及记忆状态，区分existing_game／new_public。
- `selector_sft_train.metadata.json`：数量、哈希、部分采集标记和待验证项。
- `collection-summary.json`：分语料／方向统计、完成／失败／pending数量及费用。
- `sequence-check.json`：共同schema与完整文本模板长度检查结果。
- `resolved_sft_config.yaml`：按当前完整长度生成的训练配置，尚未执行。

仅新增数据：`outputs/teacher_multidomain_2026-11-03/new_data/selector_sft_train.json`（1199条）。运行原始记录：同级`jobs/*.json`、`calls.sqlite`、各源单元SQLite、`config.json`、`state.json`及`budget.json`；包含Teacher尝试、实际翻译、记忆更新、失败及重试审计，不将它们全部拼进SFT输入。旧游戏原文件保留，对齐副本在该运行目录的`alignment/`。

## 每条训练样本

```json
{
  "instruction": "统一选择器提示词 + Observation JSON",
  "input": "",
  "output": "{\"rationale\":\"选择理由\",\"selected_ids\":[\"c01\"]}"
}
```

Observation固定字段：`pool_hash`、`source`、`source_id`、`source_lang`、`target_lang`、`source_version`、`metadata`、`max_selected`、`soft_token_budget`、`hard_context`、`candidates`。输入是当前源文、硬约束和候选上下文；输出是选择理由及候选短ID，最多8项，允许空选。独立翻译用于更新运行记忆，不是SFT监督目标。Teacher预算纠正反馈及额外指导不进入学生输入。

新旧prompt已统一。预算作为观察条件保留：1024条件2726条、2048条件439条；不把历史标签重新解释为2048动作。旧数据中336条继承固定基线历史的来源标记继续保留。

## 基本验收和后续边界

实际导出3165条，逐行共同prompt／schema及完整文本长度检查通过，最长5142 tokens；训练配置`cutoff_len=8192`。保持`qwen3_nothink`、`train_on_prompt=false`、`mask_history=false`、`packing=false`，完整输入mask，完整rationale＋ID＋assistant结束标记参与监督。

当前只完成轻量文本模板检查，框架级Loss Masking与8K显存检查留待正式训练前；没有启动训练，也没有追加质量评测。最终停止采样时累计5436次API尝试，保守估算2.9805603 USD，含未知调用预留，非平台实际账单；源图提取费用另计。

停止标记保留，采样worker已退出。[process.html](http://127.0.0.1:8765/process.html)仍可只读查看最后状态。
