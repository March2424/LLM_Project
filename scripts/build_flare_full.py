"""Build the complete simplified Flare localization dataset from local snapshots."""
import argparse
import json
from pathlib import Path
from localization_agent.data.flare_full import build_full

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--raw-root',type=Path,default=root/'artifacts/flare_full_v0.2/raw')
parser.add_argument('--output',type=Path,default=root/'outputs/flare_full_v0_2/data')
a=parser.parse_args()
r=build_full(a.raw_root,a.output)
print(json.dumps({k:r[k] for k in ['source_records','bilingual_records','untranslated_records','by_type','by_module','alignment_status','issues']},ensure_ascii=False,indent=2))
print('Catalog coverage:',[(c['domain'],c['represented_keys'],c['template_keys']) for c in r['catalogs']])
