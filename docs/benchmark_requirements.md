# 实体关系提取输入：四列独立记录

```csv
id,source_text,text_type,context
```
依旧使用简化本地化契约：localization-text/0.2的内容，只是需要改一下csv的内容。

每行独立，不设置 group_id，不增加外层分组或替代字段。按批次大小输入，行序和同批关系不作为实体归属、说话人或场景的证据；正文及 context 无法确定关系时允许不连边。

context 仅保留原生线索，标签使用英文；文件/key、版本和使用位置留在后台。完整案例见 [轻量方案第 4 节](MODULAR_GAME_MEMORY_PLAN.md#4-实体和关系怎样提取只做有用的部分)。

| 字段          | 内容                                                | 作用                                                         |
| :------------ | --------------------------------------------------- | ------------------------------------------------------------ |
| `id`          | 每条文本的唯一 ID                                   | 必填，模型输出的实体提及和关系证据引用它                     |
| `source_text` | 原始文本                                            | 实体和关系提取的主要依据，必填；不混入背景说明、参考译文或模型改写 |
| `text_type`   | 对话、物品名称、物品描述、技能名称、技能描述、UI 等 | 优先按原生字段映射；无法确定时填 unknown                     |
| `context`     | 原始材料提供的少量附加信息                          | 保留发话标记、出现条件、明确引用等；没有就留空，不需要添加上下文是什么 |

Context: 统一容纳各类型的少量特殊信息，标签使用英文处理

| 类型         | `context` 示例                                              | 用途                                  |
| ------------ | ----------------------------------------------------------- | ------------------------------------- |
| NPC 台词     | `npc_name=Abasi, Traveling Merchant; utterance_role=npc`    | 判断谁说话                            |
| 玩家台词     | `npc_name=Abasi, Traveling Merchant; utterance_role=player` | 防止把 NPC 文件里的所有台词都归给 NPC |
| 有条件的台词 | `requires_status=emp_journal_finish`                        | 防止把条件性信息抽成无条件事实        |
| 物品名称     | `book=books/merchants_journal.txt`                          | 保留原生跨资源线索                    |
| UI 标签      | `panel=audio; option=SFX`                                   | 帮助理解短文本用途                    |

在falre的raw文件，原文好像是按模块来组织的，然后后面你提取的本地化上下文就有点像context的内容。

## Flare 现有快照导出

[四列源侧 CSV](../data/flare/flare_entity_input_v0.2.csv) 包含固定快照的 4,416 条使用位置，`id` 沿用 `source.jsonl` 的 `record_id`；重复原文不合并。CSV 导出只读取源文和 [原生上下文证据](../data/flare/entity_input_background.jsonl)，不读取中文参考。来源文件、行号、版本在后台证据中保存，不增加模型输入列。

在仓库根目录从已发布的后台证据重新生成 CSV（仅需标准库）：

```bash
python3 scripts/export_flare_entity_input.py
```

若本地已有固定版本 raw，可先从 raw 重建证据再导出（适配器及现有公共层需 Python 3.10+）：

```bash
PYTHONPATH=src python3 scripts/export_flare_entity_input.py --raw-root artifacts/flare_full_v0.2/raw
```

复用原适配器的 INCLUDE/APPEND、模块回退及使用位置解析；重建时检查源侧记录与发布快照完全一致，不一致则停止。重建复用全量整理器，因此其临时离线产物也含参考侧数据；CSV 及后台原生线索仅从源字段构造，临时产物不进入模型输入。

| 原生线索 | CSV 表达与边界 |
| --- | --- |
| 发话信息 | 资源头 `name` → `npc_name`；`you` → `utterance_role=player`，`him/her` → `utterance_role=npc`。不根据文件名猜测说话人。 |
| 出现条件 | 保留当前资源块所有 `requires_*` 原值、顺序及重复键，不用已整理的自然语言条件替代。 |
| 明确引用 | 保留原生 `book`、`npc` 引用，不将引用直接当成批准实体关系。 |
| 执行事件 | 保留 `set_status`、`unset_status`、`pickup_status` 原字段名；它们是同资源块或物品定义中的事件声明，不表示当前台词已执行该事件，也不作为出现条件。 |
| 界面用途 | 从包含该原文的 `cfg_tabs[...].setOption...` 调用提取 `panel`、`option`，例如 `panel=audio; option=SFX`。没有明确调用关联时留空。 |

不复制相邻文本、名称/说明配对、推断地点、模块分组或背景总结。不存在适用线索时 context 留空；空值不证明文本无条件或无关系。键值文本供模型阅读，不是新定义的通用解析语言；CSV 引号、逗号及换行通过标准 CSV writer 转义。

本次检查：1,819 个 raw 文件与本地固定下载清单哈希一致；全部 4,416 条源侧记录复现一致。相比初版 CSV，551 条记录增加原生线索，其中书籍引用 22 条、界面面板/选项 44 条。原生证据可逐条定位至文件、行号和固定版本；ID、原文与 text_type 保持不变。14 项相关离线测试通过，并用现有 `import_benchmark` 完整导入 4,416 条记录、368 个批次。

当前覆盖固定快照适配器中的可翻译使用位置及上表所列原生语义线索，不宣称解析全部引擎控制流或任意动态 C++ 表达式。实际游玩路径、真实模型抽取质量及训练/开发/测试划分仍待后续工作。文本许可与上游署名沿用 [Flare 数据说明](../data/flare/v0.2/README.md#来源变换与许可)。
