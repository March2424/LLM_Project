# 游戏解包 CSV：结构缺失时怎样导入

状态：2026-10-28，源文导入与机械分块已实现。入口 [csv_graph.py](../src/localization_agent/data/csv_graph.py)，配置示例 [csv_graph.example.json](../configs/csv_graph.example.json)。

## 1. 没有章节或场景时的处理

可以直接导入，不要求先把整份 CSV 人工划完场景。系统保留 CSV key、物理记录序号、独立 occurrence ID 和源版本。没有语义文档标记时，用 namespace 派生的 source-unit document_id 组织一次运行；这只是导入单元。缺失 scene 保留 None，不创建场景。

默认每块最多 8 行、1200 字符；不拆单条源文，超长单条会形成独立块。明确的 document/scene/block/branch/phase 改变时切块。技术块是处理批次，不能据此推断人物关系、剧情连续性或“这些行属于同一场景”。

默认 order_trusted=false，不创建 PREVIOUS，不通过当前技术块自动召回其他行，也关闭局部目标译例/摘要基线。若批准篇章边指向一个技术块，只返回该边明确引用的成员行，避免混入同块的无关 UI 或台词。

当确认导出顺序代表脚本顺序时才设 order_trusted=true；此时允许局部块上下文和兼容块的 PREVIOUS。排列在一起不等于剧情相关，尤其不要对按 key/字母排序的字符串表开启此选项。

## 2. 字段映射与人工补充

默认必须有 id 和 source 列。通过配置映射不同列名：

```json
{
  "namespace": "game-a/dialogue",
  "id_column": "Key",
  "source_column": "English",
  "columns": {
    "speaker_id": "Speaker",
    "branch_id": "Branch"
  },
  "order_trusted": false
}
```

可选语义字段：document、scene、block、branch_id、phase_id、speaker_id、addressee_id、text_type。已知 speaker/addressee 自动建立人物节点及对应结构边。名称使用导出 ID，不猜真实姓名或人设。人物节点首版按文档及分支/阶段范围登记，跨文档统一实体消歧尚未实现。

`annotations` 是按原始 CSV key 索引的已核验元数据，可补缺失字段或覆盖列值：

```json
{
  "dlg_001": {"scene": "evacuation", "block": "briefing", "branch_id": "main"},
  "dlg_002": {"scene": "evacuation", "block": "briefing", "branch_id": "main"}
}
```

只标注有把握的记录；未知保留 null，禁止把字符串 "unknown" 当作确定值。未知 key 或不支持的字段会报错。首版同一人工 block 标签仍可能因容量上限或不连续位置而拆成多个技术块，不会跨文件重排记录。场景划分模型尚未实现；未来输出应先作为候选人工核验。

没有稳定 key 时可显式配置 `"id_column": null`，以 CSV 记录序号生成位置 ID，并返回警告。此模式需冻结该文件版本的 namespace；重排行后应建立新 namespace，不可用旧行号覆盖更新。同文本不合并；重复或空 key、空源文直接报错，不静默丢样本。

同 namespace、同 key、同源版本重复导入要求内容一致。源文/位置/元数据变化时提高 source_version；新源版本使旧证据失效，重新建图快照。namespace 由使用者固定，不从可变文件路径自动猜。导入不是整文件原子事务；中途失败可能已有部分记录写入，修正后按相同版本重试可复用一致记录。

## 3. 命令行与来源隔离

以下命令使用自己的 CSV 路径，按实际列调整配置：

```bash
PYTHONPATH=src python3 scripts/import_graph_csv.py /path/to/source.csv \
  --config configs/csv_graph.example.json \
  --project game-a --db artifacts/game-a.sqlite --snapshot source-graph-v1
```

可加 `--annotations /path/to/annotations.json`。输出记录数、块数、document_id、图快照和警告；每次创建快照需要新的 ID。脚本只创建源侧图，建立 M2 知识快照与 run 的 API 用法见 [GRAPH.md](GRAPH.md) 和 [MEMORY.md](MEMORY.md)。

只读取显式配置的源文和元数据列，其余列不保存到图中。不会自动识别或读取 reference/target 列；调用者仍需正确指定源侧列，不能把测试参考映射到 source 或元数据中。关系证据必须引用登记过的源文。

CLI 已用构造 CSV 验证，不代表任意游戏解包格式都兼容。当前使用逗号分隔、UTF-8（支持 BOM）；其他编码、分隔符应先转换。csv_row 是含表头的记录序号，多行字段情况下不是编辑器物理行号。


## 2026-10-29：无序游戏模式

新增轻量实体图与五类记忆组织方式；`start_run(..., mode="game_resources")` 启用项目范围，默认文档模式保持原行为。DEFINES 反查、候选链接、主题 STM/LTM 和双语概览的实际接口及限制见 [GAME_MEMORY.md](GAME_MEMORY.md)。
