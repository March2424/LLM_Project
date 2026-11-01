"""Verify source coverage and write a human-readable extraction report locally."""
import argparse
import csv
import json
import sqlite3
from collections import Counter
from hashlib import sha256
from pathlib import Path
from export_extraction_graph import export_graph


def report(folder: str):
    root=Path(folder)
    state=json.loads((root/'result.json').read_text())
    if state['status']!='complete':
        raise ValueError('Extraction must be complete before final coverage verification')
    summary=export_graph(str(root/'graph.sqlite'),str(root))
    connection=sqlite3.connect(root/'graph.sqlite')
    batches=[json.loads(row[0]) for row in connection.execute('SELECT payload FROM extraction_batches')]
    references=[ref['id'] for b in batches if b['status']=='complete' for ref in b['refs']]
    expected={row[0] for row in connection.execute("SELECT id FROM graph_items WHERE kind='node' AND json_extract(payload,'$.kind')='occurrence'")}
    if len(references)!=len(set(references)) or set(references)!=expected:
        raise ValueError('Checkpoint coverage has duplicates or missing records')
    for path,digest in state['inputs'].items():
        if sha256(Path(path).read_bytes()).hexdigest()!=digest:
            raise ValueError('Source input changed during extraction')
    entities=[json.loads(line) for line in (root/'entities.jsonl').open()]
    labels=Counter((n['attributes']['entity_type'],n['attributes']['name']) for n in entities)
    calls=[json.loads(row[0]) for row in connection.execute("SELECT response FROM model_calls WHERE response != 'null'")]
    issues=[item for b in batches if b['status']=='complete' for item in b['result'].get('rejected',[])]
    summary.update(status=state['status'],source_coverage=len(references),
        skipped_formatting_ids=state['skipped_formatting_ids'],
        exact_type_name_groups=len(labels),same_label_groups=sum(n>1 for n in labels.values()),
        known_usage_cost_estimate_usd=round(sum(v.get('cost') or 0 for v in calls),7),
        cost_including_uncertain_reservations_usd=state['budget_usage']['cost'],
        budget_limit_usd=state['budget_usage']['max_cost'],
        model_calls_attempted=state['budget_usage']['calls'],
        input_tokens=sum(v.get('input_tokens') or 0 for v in calls),
        output_tokens=sum(v.get('output_tokens') or 0 for v in calls),
        rejected_by_error=dict(Counter(item['error'] for item in issues)))
    with (root/'relations.csv').open(newline='') as f:
        if len(list(csv.DictReader(f)))!=summary['relations']:raise ValueError('Export row mismatch')
    (root/'run_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    lines=['# 全量实体关系提取报告','',f"状态：complete。全部 {len(references):,} 条待提取源记录覆盖无重复、无遗漏；输入哈希一致。",'',
        f"- 实体节点：{summary['entities']:,}；关系：{summary['relations']:,}；被拒候选：{len(issues):,}。",
        f"- 类型＋名称精确分组：{len(labels):,} 组；其中 {summary['same_label_groups']} 组存在多个节点。该值不是消歧后的实体数量，本轮不执行实体合并。",
        f"- 已记录 usage 按配置费率估算：{summary['known_usage_cost_estimate_usd']:.7f} USD；含未知请求预留：{summary['cost_including_uncertain_reservations_usd']:.7f} USD。不是账户实际扣款。",
        f"- 未送模型的纯空白格式记录：{len(state['skipped_formatting_ids'])} 条，ID 见 run_summary.json，原 CSV 保留。",'',
        '## 查看结果','',
        '- [relations.csv](relations.csv)：可筛选关系及证据 ID。',
        '- [entities.jsonl](entities.jsonl)、[relations.jsonl](relations.jsonl)：实体与关系结构、版本及证据。',
        '- [rejected_items.jsonl](rejected_items.jsonl)：被拒候选与原因。',
        '- [run_summary.json](run_summary.json)：覆盖、费用和分布统计。',
        '- [result.json](result.json)、[graph.sqlite](graph.sqlite)：运行记录和版本化图。','',
        '结果为 program_only。正文、原生 context 和已发现实体是唯一模型资料，不读取中文参考、不执行翻译，也不把程序校验通过当作语义质量指标。']
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n')
    connection.close()
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder')
    args=parser.parse_args()
    print(json.dumps(report(args.folder),ensure_ascii=False))
