"""Run remaining game datasets independently with the accepted extraction protocol."""
import argparse
import os
from pathlib import Path
import runpy
import sys
from export_extraction_graph import export_graph

ROOT = Path(__file__).resolve().parents[1]
INPUTS = {'shattered':'data/shattered/v4.0.2/shattered_entity_input.csv',
          'naev':'data/naev/snapshot-123fdc1/naev_entity_input.csv'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('game',choices=INPUTS)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    if not args.dry_run and (ROOT/'.env.extraction').exists():
        for line in (ROOT/'.env.extraction').read_text().splitlines():
            name,sep,value=line.strip().removeprefix('export ').partition('=')
            if sep and name and not name.startswith('#'):
                os.environ.setdefault(name,value.strip().strip('\"\''))
    folder=ROOT/f'outputs/{args.game}_full_2026-11-01'
    sys.argv=[str(ROOT/'scripts/run_benchmark_agent.py'),str(ROOT/INPUTS[args.game]),
        '--project',args.game,'--config',str(ROOT/f'configs/{args.game}.extraction.json'),
        '--db',str(folder/'graph.sqlite'),'--output',str(folder/'result.json'),
        '--max-batches','0','--split-failures']
    if args.resume:sys.argv.append('--resume')
    if args.dry_run:sys.argv.append('--dry-run')
    runpy.run_path(sys.argv[0],run_name='__main__')
    if not args.dry_run:
        export_graph(str(folder/'graph.sqlite'),str(folder))


if __name__=='__main__':main()
