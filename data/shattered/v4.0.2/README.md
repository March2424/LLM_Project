# 破碎的像素地牢四列源侧数据 v4.0.2

固定上游提交：`57a4e06a4caf162446d1c28caa7983f0493fecf0`。采用 `localization-text/0.2` 的四列提取视图。

- [shattered_entity_input.csv](shattered_entity_input.csv)：模型源侧输入，仅有 `id,source_text,text_type,context` 四列，5,607 条独立记录。
- [references.jsonl](references.jsonl)：离线中文参考，仅有 `record_id,reference_text` 两个字段，5,606 条；使用 CSV 的 id 关联。
- [background/provenance.jsonl](background/provenance.jsonl)：每条记录的语言键、定义位置、调用位置、原生上下文证据及缺译状态。
- [background/unresolved_calls.jsonl](background/unresolved_calls.jsonl)：动态键、动态接收者、继承回退等尚未静态解析的 366 个 Messages.get 调用，不是运行失败样本。
- [background/empty_source_keys.jsonl](background/empty_source_keys.jsonl)：3 个原文本身为空的资源键，保留在后台，不制造空的待翻译记录。
- [manifest.json](manifest.json)：数量、输入哈希、版本、覆盖边界与本目录文件哈希。

范围是游戏运行时加载的九组英文语言资源：UI、窗口、物品、角色、技能/天赋/法术、NPC 文本、日志等。全部 4,973 个非空英文键均有记录；重复原文不合并。可静态定位的同键不同调用点各有独立 ID；其余从资源定义保留一条，不能解读为只在游戏中出现一次。尚未穷举动态继承分派、动态键及运行时所有角色/场景组合，不承诺全部运行时 occurrence 已恢复。

context 只含 Java AST 明确包围调用的 if/else 条件、switch 分支及直接显示调用（yell/tell/GLog）。保留原始表达式，不把字段名当剧情事实，也不从类名猜测人物身份。调用本身位于条件求值中时不附加该条件；资源定义没有确定的调用上下文时留空。text_type 按原生语言字段和调用用途映射，无法确定时为 unknown；不会给未知项编造类型或背景。

文本保留 Java Properties 解码后的原文，包括换行、格式符和标记；参考缺失仍保留源侧记录。中文未做人工质量评级，不能作为批准 TM。仅为流程样本，split=unassigned，尚非正式评测集。参考文件与中文原始属性文件不得进入提取、检索、生成或在线审校。

复现和验证方式见 [接入说明](../../../docs/SHATTERED_DATA.md)。上游来源与许可见 [ATTRIBUTION.txt](ATTRIBUTION.txt) 和 [LICENSE.txt](LICENSE.txt)。本目录不包含游戏程序、图片或音频。
