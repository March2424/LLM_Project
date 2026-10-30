"""Rebuild the fixed Flare preview from already downloaded local source files."""
import argparse
import json
from pathlib import Path
from localization_agent.data.flare_sample import build_sample

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--raw-root", type=Path, default=root / "artifacts/flare_sample_v0.1/raw")
parser.add_argument("--catalog-root", type=Path, default=root / "artifacts/data_source_audit_2026-10-29/raw")
parser.add_argument("--output", type=Path, default=root / "outputs/flare_sample_v0_1/data")
args = parser.parse_args()
print(json.dumps(build_sample(args.raw_root, args.catalog_root, args.output)["counts"], ensure_ascii=False, indent=2))
