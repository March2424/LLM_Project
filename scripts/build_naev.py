"""Generate the pinned Naev text resources, then export source CSV and reference states."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from localization_agent.data.naev import export
from localization_agent.data.naev_resources import REVISION, ASSET_REVISION, generate

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--raw-root',type=Path,default=Path('artifacts/game_text_audit_2026-10-30/naev'))
parser.add_argument('--assets-root',type=Path,default=Path('artifacts/game_text_audit_2026-10-30/naev-assets'))
parser.add_argument('--work',type=Path,default=Path('artifacts/naev_resource_build'),help='New or empty build directory')
parser.add_argument('--gettext-bin',type=Path,default=Path(shutil.which('xgettext') or '/opt/homebrew/opt/gettext/bin/xgettext').parent)
parser.add_argument('--prepared-build',type=Path,help='Reuse a successful build with generation.json')
parser.add_argument('--output',type=Path,required=True,help='New or empty output directory')
args=parser.parse_args()
if args.prepared_build:
    source=args.prepared_build/'source'
    metadata=json.loads((args.prepared_build/'generation.json').read_text())
    if (metadata['revision'],metadata['asset_revision'])!=(REVISION,ASSET_REVISION):parser.error('Prepared build has wrong revision')
    for entry in metadata['generated']:
        if hashlib.sha256((source/entry['path']).read_bytes()).hexdigest()!=entry['sha256']:parser.error('Generated resource changed: '+entry['path'])
else:
    source,metadata=generate(args.raw_root,args.assets_root,args.work,args.gettext_bin)
manifest=export(source,metadata,args.output)
print({k:manifest[k] for k in ('source_catalog_entries','source_records','reference_status_records','generated_files')})
