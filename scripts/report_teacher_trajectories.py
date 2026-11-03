"""Print existing Teacher progress; never call a model or change a checkpoint."""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('outputs/teacher_multidomain_2026-11-03'))
    parser.add_argument('--watch',type=float,default=0)
    args = parser.parse_args()
    if args.watch < 0:
        parser.error('--watch cannot be negative')
    try:
        while True:
            p = json.loads((args.output/'progress.json').read_text())
            b = p['budget']
            print(f"[{datetime.now().astimezone().strftime('%H:%M:%S')}] {p['status']} | 完成 {p['finished']}/{p['total']}")
            print(f"冷启动 {p.get('warmup_finished',0)}/{p.get('warmup_total',0)} | 目标完成 {p.get('target_finished',0)}/{p['teacher_target_count']} | 有效选择 {p['teacher_candidates']} | 失败 {p['failed']}")
            print(f"API尝试 {b['calls']} | 费用估算 ${b['cost']:.6f} | 当前任务 {p.get('current_task')} | {p.get('current_source_id')}")
            print('候选可见: '+json.dumps(p.get('candidate_available_targets_by_kind',{}),ensure_ascii=False),flush=True)
            if p.get('since_upgrade'):
                print('历史失败 '+str(p['historical_failed'])+' | 修复后 '+json.dumps(p['since_upgrade'],ensure_ascii=False),flush=True)
            if not args.watch:
                break
            time.sleep(args.watch)
    except KeyboardInterrupt:
        pass
