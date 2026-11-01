"""Run the accepted Flare source extraction with a bounded budget and checkpoints."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = ROOT / '.env.extraction'
    if env.exists():
        for line in env.read_text().splitlines():
            name, sep, value = line.strip().removeprefix('export ').partition('=')
            if sep and name and not name.startswith('#'):
                os.environ.setdefault(name, value.strip().strip('\"\''))
    sys.argv = [str(ROOT / 'scripts/run_benchmark_agent.py'),
        str(ROOT / 'data/flare/flare_entity_input_v0.2.csv'), '--project', 'flare',
        '--config', str(ROOT / 'configs/flare.extraction.json'),
        '--db', str(ROOT / 'outputs/flare_full_2026-10-31/graph.sqlite'),
        '--output', str(ROOT / 'outputs/flare_full_2026-10-31/result.json'),
        '--max-batches', '0', '--split-failures', *sys.argv[1:]]
    runpy.run_path(sys.argv[0], run_name='__main__')


if __name__ == '__main__':
    main()
