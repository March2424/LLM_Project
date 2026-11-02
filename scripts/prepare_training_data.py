"""Prepare draft resource-group splits and teacher sampling plans without LLM/GPU calls."""
import argparse
import json
from localization_agent.data.training_split import prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--output', required=True, help='New output directory')
    parser.add_argument('--seed', type=int, default=20261101)
    parser.add_argument('--pilot-size', type=int, default=128)
    parser.add_argument('--warmup', type=int, default=10)
    parser.add_argument('--memory-coverage', action='store_true', help='Add native identity bindings and compatible role/topic blocks')
    parser.add_argument('--retain-plan', help='Keep an existing pilot target subset when planning memory coverage')
    args = parser.parse_args()
    if args.pilot_size < 1 or args.warmup < 0:
        parser.error('pilot-size must be positive and warmup nonnegative')
    result = prepare(args.root, args.output, args.seed, args.pilot_size, args.warmup,
                     memory_coverage=args.memory_coverage, retain_plan=args.retain_plan)
    print(json.dumps({k: result[k] for k in ('status', 'counts', 'pilot_targets', 'paid_calls')}, indent=2))


if __name__ == '__main__':
    main()
