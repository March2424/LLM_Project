"""Check source manifests, unchanged schema and extraction capacity without API calls."""
import argparse
import json
import os
from pathlib import Path

from localization_agent.data.extraction_preflight import preflight
from localization_agent.schemas.extraction import extraction_schema


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',type=Path,required=True)
    parser.add_argument('--config',type=Path,default=Path('configs/multidomain.extraction.json'))
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if json.loads(Path('configs/extraction.schema.json').read_text()) != extraction_schema():
        raise ValueError('Published extraction schema differs from Python schema')
    report = preflight(args.sources,config)
    old = json.loads(Path('configs/shattered.extraction.json').read_text())
    smaller = {**config,'batch':{**config['batch'],'max_rows':32}}
    baseline = preflight(args.sources,old)
    small_report = preflight(args.sources,smaller)
    report['batch_comparison'] = {name:{k:r[k] for k in ('planned_batches','batch_rows',
        'input_tokens_empty_catalog_estimate')} for name,r in
        [('old_game_config',baseline),('max_32_rows',small_report),('selected',report)]}
    report['credential_environment_present'] = bool(os.environ.get(config['model'].get('api_key_env','LLM_API_KEY')))
    report['default_credential_file_present'] = Path('.env.extraction').exists()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','schema_version','source_records','extraction_units',
        'planned_batches','batch_rows','min_context_margin_estimate','model_calls','batch_comparison',
        'credential_environment_present','default_credential_file_present','stop_marker_present')},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
