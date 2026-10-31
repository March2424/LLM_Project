"""Build source-only four-column CSV and separate Chinese references from a pinned checkout."""
import argparse
from pathlib import Path
import subprocess

from localization_agent.data.shattered import REVISION, build

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--raw-root', type=Path, default=Path('artifacts/game_text_audit_2026-10-30/shattered'))
parser.add_argument('--output', type=Path, required=True, help='New or empty directory')
args = parser.parse_args()
actual = subprocess.check_output(['git', '-C', str(args.raw_root), 'rev-parse', 'HEAD'], text=True).strip()
if actual != REVISION:
    parser.error(f'Expected fixed revision {REVISION}; got {actual}')
result = build(args.raw_root, args.output)
print({key: result[key] for key in ('source_records', 'reference_records', 'missing_reference_records',
                                  'literal_call_records', 'resource_definition_records', 'unresolved_calls')})
