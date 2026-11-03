"""Show live source graph progress without opening references or calling a model."""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path


def report(root):
    units = [json.loads(line) for line in (root/'extraction_units.jsonl').read_text().splitlines() if line.strip()]
    pipeline = json.loads((root/'pipeline.json').read_text()) if (root/'pipeline.json').exists() else {}
    results = []
    for unit in units:
        path = root/unit['graph_result']
        if path.exists():
            results.append((unit,json.loads(path.read_text())))
    complete = sum(r.get('status')=='complete' for _,r in results)
    total_rows = sum(u['source_records'] for u in units)
    rows = sum(r.get('completed_rows',0) for _,r in results)
    calls = sum((r.get('budget_usage') or {}).get('calls',0) for _,r in results)
    cost = sum((r.get('budget_usage') or {}).get('cost') or 0 for _,r in results)
    archived_path = root/'archived_graph_usage.json'
    archived = json.loads(archived_path.read_text()) if archived_path.exists() else {}
    old_cost = sum(r.get('cost') or 0 for r in archived.values())
    batches = sum(len(r.get('builds',[])) for _,r in results)
    failed = [(u,r) for u,r in results if r.get('status')=='failed']
    print(f"[{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}] {pipeline.get('status','not_started')}")
    print(f'单元 {complete}/{len(units)} | 源记录 {rows}/{total_rows} ({rows/total_rows:.1%}) | 已完成子批 {batches} | API尝试 {calls}')
    print(f'本轮费用估算 ${cost:.6f} | 含旧归档 ${cost+old_cost:.6f} | 失败单元 {len(failed)} | STOP={ (root/"STOP").exists() }')
    for unit,result in results:
        if result.get('status') not in {'complete'}:
            print(f"  {unit['project_id']}: {result.get('status')} {result.get('completed_rows',0)}/{unit['source_records']} rows; batch={result.get('active_batch','-')}")
            if result.get('error'):
                print('    '+result['error'])
    if pipeline.get('error'):
        print('流水线错误: '+pipeline['error'])
    print('',flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',type=Path,default=Path('outputs/multidomain_sft_2026-11-03'))
    parser.add_argument('--watch',type=float,default=0,help='Refresh interval in seconds; 0 prints once')
    args = parser.parse_args()
    if args.watch < 0:
        parser.error('--watch cannot be negative')
    try:
        while True:
            report(args.sources)
            if not args.watch:
                break
            time.sleep(args.watch)
    except KeyboardInterrupt:
        pass
