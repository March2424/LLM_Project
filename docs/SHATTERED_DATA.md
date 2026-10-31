# 破碎的像素地牢四列文本接入

已整理固定版本 v4.0.2，提交 `57a4e06a4caf162446d1c28caa7983f0493fecf0`。[数据目录](../data/shattered/v4.0.2/README.md) 可直接使用，无需运行游戏。

## 产物与覆盖

模型只读取 [shattered_entity_input.csv](../data/shattered/v4.0.2/shattered_entity_input.csv)：

```csv
id,source_text,text_type,context
```

共 5,607 条源侧记录，覆盖九组语言资源全部 4,973 个非空英文键。包括物品、装备、技能／天赋／法术、NPC、日志、关卡以及 UI／窗口等。不同语言键即使原文相同也不合并；同键能静态确认的不同调用位置也不合并。2,536 条对应已解析的字面量调用，3,071 条来自尚无可确认调用的资源定义。3 个原文本身为空的键单独留在后台，不伪造空白待翻译行。

[references.jsonl](../data/shattered/v4.0.2/references.jsonl) 单独保存 5,606 条中文参考，通过 `record_id` 对应 CSV 的 `id`，不进入模型源侧。缺译键 `items.quest.escapecrystal.injure_confirm` 仍有完整源侧记录，后台标记 `missing_reference`；没有把英文回退当中文，也没有自动补译。原始中文资源、参考 JSONL 都不能用于在线知识提取、检索、生成或审校。

格式符、强调标记及控制符保留；Java Properties 的 `\n` 解码为实际单元格换行，按标准 CSV 引号保存。text_type 依据原生字段及明确调用用途映射；无法确定的保持 `unknown`，不是漏导出。上下文不复制相邻文本、物品名与说明配对、文件分组或中文参考。

## 原生上下文与位置

适配器使用 Java AST 识别 `Messages.get(...)`，分别处理显式类、当前命名类、外部类 `.this` 和完整字面量键。匿名类中的裸 `this` 不冒充外部 NPC。每次调用按文件和字节位置生成独立稳定 ID；资源定义按文件和原生键生成 ID。ID 不依赖参考译文。

context 使用英文标签保存明确包围调用的 if/else 条件、switch 选择表达式及 case，以及直接 `yell`、NPC `tell`、`GLog` 显示方式。例如铁匠按职业选择的台词包含 `condition=(!Quest.given)`、`switch=(Dungeon.hero.heroClass)`、`case=case WARRIOR`。这些是原生表达式，不解释成无条件剧情事实；没有证明说话人、位置或状态时不补造。条件中求值的 Messages 调用不附加该条件作为已成立事实。

[后台溯源](../data/shattered/v4.0.2/background/provenance.jsonl) 保留原生键、定义文件行号、源码调用及上下文证据、版本、缺译状态。文件／键等技术定位不作为额外 CSV 列或实体分组提示。`dynamic_this` 表示接收者可能动态分派，不能当作具体角色已确认。

## 已知边界

当前覆盖游戏运行时加载的全部九组国际化资源中的非空英文文本模板，未穷举每一种运行时文本组合。动态键、动态对象接收者和继承回退仍有 366 个调用未解析，逐条保存在 [unresolved_calls.jsonl](../data/shattered/v4.0.2/background/unresolved_calls.jsonl)。这些调用对应的语言资源定义照常导出，不能据此宣称全部运行时 occurrence、调用条件和说话人均已恢复。

尚未审计源码中未国际化的硬编码启动报错、图片文字等。当前范围不是“所有屏幕文字穷举”。原生分支表达式仅为证据，未转换成冻结的跨模块 scope／强制规则。数据 split=unassigned；中文保留上游原样，尚未人工评级、建立批准 TM 或完成正式评测划分。

## 复现与实际验证

需要 Python 3.10+；本次使用 Python 3.12.15。数据处理专用依赖记录在 [requirements-shattered.txt](../configs/requirements-shattered.txt)，tree-sitter 0.25.2、tree-sitter-java 0.23.5，与训练／推理依赖分开。可安装到数据处理环境；本机实际安装在忽略目录 `artifacts/shattered_adapter_deps/`。

已有该固定源码 checkout 时，在仓库根目录运行（输出目录必须新建或为空）：

```bash
PYTHONPATH=src:artifacts/shattered_adapter_deps python3 scripts/build_shattered.py --raw-root artifacts/game_text_audit_2026-10-30/shattered --output outputs/shattered_rebuild
```

脚本核对 checkout 的提交号，源文件实际哈希与适配器哈希写入 manifest；不执行游戏脚本。原始 checkout 需具备 `core/src/main/assets/messages/`、Java 调用源码及根目录 `LICENSE.txt`。该命令不是自动下载入口。

实际完成：3 项针对性检查覆盖 Properties 转义／换行、分支及匿名类边界、不同调用位置分离、缺译保留和中文修改不影响源侧结果；对全量产物核对 4,973 个非空键无遗漏、每条原文和中文均与原生键对齐、ID 唯一及文件哈希一致；使用现有 `import_benchmark` 完整导入。未运行真实模型或游戏游玩验证。来源可获取性和此前语言键统计见 [资源核查报告](SHATTERED_NAEV_RESOURCE_AUDIT.md)。
