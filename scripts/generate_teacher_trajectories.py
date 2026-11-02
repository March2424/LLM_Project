"""Prepare, collect/resume or export external-teacher trajectories; progress.json + HTML."""
import argparse
from pathlib import Path
from localization_agent.selection.teacher import prepare, run, export, migrate, extend, upgrade, retry_call


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare', help='No paid calls; copy source DBs and freeze a pilot plan')
    p.add_argument('--plan', default='outputs/cpu_preparation_2026-11-02_per_game')
    p.add_argument('--model-config', default='configs/flare.memory_trial.json')
    p.add_argument('--output', required=True)
    p.add_argument('--max-cost', type=float, required=True, help='Explicit estimated USD cap for this run')
    p.add_argument('--max-calls', type=int, default=10000)
    p.add_argument('--max-tokens', type=int, default=10000000)
    p = sub.add_parser('upgrade', help='Copy stopped run to compact checkpoints and concurrent runtime; no API calls')
    p.add_argument('--source', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--retry-call', help='Full unresolved request key; explicitly accepts possible duplicate billing on resume')
    p = sub.add_parser('retry-call', help='Authorize one unresolved request retry; preserve its old estimated charge')
    p.add_argument('--output', required=True)
    p.add_argument('--key', required=True)
    p = sub.add_parser('stop', help='Finish current jobs and stop before starting the next ones')
    p.add_argument('--output', required=True)
    p = sub.add_parser('extend', help='Copy a finished per-game pilot and add targets without restarting memory')
    p.add_argument('--source', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--plan', required=True)
    p.add_argument('--max-cost', type=float, required=True, help='Cumulative estimated USD cap, including pilot spend')
    p = sub.add_parser('migrate', help='Archive a stopped legacy run; resource-group histories remain audit-only')
    p.add_argument('--source', required=True)
    p.add_argument('--output', required=True)
    for command in ('run', 'export'):
        p = sub.add_parser(command)
        p.add_argument('--output', required=True)
        if command == 'export':
            p.add_argument('--dataset-dir', help='Also write the registered LLaMA-Factory SFT dataset')
        else:
            p.add_argument('--game-workers', type=int, default=2)
            p.add_argument('--http-workers', type=int, default=4)
            p.add_argument('--resume', action='store_true', help='Clear a previous cooperative STOP request')
    args = parser.parse_args()
    if args.command == 'prepare':
        if args.max_cost <= 0 or args.max_calls < 1 or args.max_tokens < 1:
            parser.error('Positive explicit budget limits required')
        prepare(args.output, args.plan, args.model_config, args.max_cost, args.max_calls, args.max_tokens)
    elif args.command == 'migrate':
        migrate(args.source, args.output)
    elif args.command == 'extend':
        extend(args.source, args.output, args.plan, args.max_cost)
    elif args.command == 'upgrade':
        upgrade(args.source,args.output,args.retry_call)
    elif args.command == 'retry-call':
        retry_call(args.output,args.key)
    elif args.command == 'stop':
        (Path(args.output)/'STOP').touch()
    elif args.command == 'run':
        if args.resume:
            (Path(args.output)/'STOP').unlink(missing_ok=True)
        result = run(args.output,game_workers=args.game_workers,http_workers=args.http_workers)
        if result['status'] != 'complete':
            raise SystemExit(2)
    else:
        print('Exported program-checked SFT candidates:', export(args.output, args.dataset_dir))


if __name__ == '__main__':
    main()
