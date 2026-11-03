"""Offline source-only graph batching and prompt capacity checks."""
import json
from collections import defaultdict
from hashlib import sha256
from pathlib import Path

from localization_agent.models.chat_completions import estimate_tokens
from localization_agent.schemas.extraction import SCHEMA_VERSION, prompt_vocabulary
from .benchmark import read_benchmark


def preflight(sources: str | Path, config: dict) -> dict:
    root = Path(sources).resolve()
    units = [json.loads(line) for line in (root/'extraction_units.jsonl').read_text().splitlines() if line.strip()]
    manifest = [json.loads(line) for line in (root/'sources_manifest.jsonl').read_text().splitlines() if line.strip()]
    by_project = defaultdict(dict)
    for row in manifest:
        by_project[row['project_id']][row['source_id']] = row
    template_path = Path('prompts/memory/discover_graph.txt')
    template = template_path.read_text()
    batch = config.get('batch', {})
    options = {'batch_size':batch.get('max_rows',64), 'batch_input_tokens':batch.get('input_tokens',5000),
               'batch_output_tokens':batch.get('output_tokens',8192)}
    model = config['model']
    output_limit = model['max_output_tokens']
    catalog_reserve = config.get('catalog_tokens',1200) + 200  # JSON punctuation/estimator overhead.
    if options['batch_output_tokens'] > output_limit:
        raise ValueError('Planned graph output exceeds the model output limit')
    reports, input_total, output_total, rows_total = {}, 0, 0, 0
    batch_lengths, prompt_sizes = [], []
    seen_ids = set()
    for unit in units:
        path = (root/unit['csv']).resolve()
        path.relative_to(root/'source')  # References are never an extraction input.
        occurrences, batches, skipped = read_benchmark([path],unit['project_id'],
            source_lang=unit['source_lang'],target_lang=unit['target_lang'],**options)
        expected = by_project[unit['project_id']]
        if {o.occurrence_id for o in occurrences} != set(expected):
            raise ValueError('Source CSV and manifest IDs differ: '+unit['project_id'])
        if len(occurrences) != unit['source_records']:
            raise ValueError('Source count differs from extraction unit: '+unit['project_id'])
        for occurrence in occurrences:
            row = expected[occurrence.occurrence_id]
            if (sha256(occurrence.source.encode()).hexdigest() != row['source_hash'] or
                (row['source_lang'],row['target_lang'],row['split']) !=
                    (unit['source_lang'],unit['target_lang'],unit['split'])):
                raise ValueError('Source hash/direction/split differs from manifest: '+occurrence.occurrence_id)
            if occurrence.occurrence_id in seen_ids:
                raise ValueError('Duplicate source occurrence ID: '+occurrence.occurrence_id)
            seen_ids.add(occurrence.occurrence_id)
        rows = [{'id':o.occurrence_id,'source_text':o.source,'text_type':o.metadata['text_type'],
                 'context':o.metadata['native_context']} for o in occurrences]
        estimates = []
        for indices in batches:
            selected = [rows[i] for i in indices]
            prompt = template.format(rows=json.dumps(selected,ensure_ascii=False,separators=(',',':')),
                                     catalog='[]',**prompt_vocabulary())
            tokens = estimate_tokens(prompt)
            if tokens + catalog_reserve + output_limit > model['context_tokens']:
                raise ValueError('Full prompt + catalog reserve + output exceeds context: '+unit['project_id'])
            projected_output = sum(192 + estimate_tokens(json.dumps(row,ensure_ascii=False,separators=(',',':'))) // 2
                                   for row in selected)
            estimates.append({'rows':len(indices),'input_tokens_empty_catalog_estimate':tokens,
                              'graph_output_tokens_estimate':projected_output})
            input_total += tokens
            output_total += projected_output
            batch_lengths.append(len(indices))
            prompt_sizes.append(tokens)
        rows_total += len(rows)
        reports[unit['project_id']] = {'source_records':len(rows),'planned_batches':len(batches),
            'source_lang':unit['source_lang'],'target_lang':unit['target_lang'],'split':unit['split'],
            'skipped_formatting_ids':skipped,'source_sha256':sha256(path.read_bytes()).hexdigest(),
            'batches':estimates}
    if seen_ids != {row['source_id'] for row in manifest}:
        raise ValueError('Manifest contains records without an extraction unit')
    total_batches = len(batch_lengths)
    return {'status':'offline_preflight_passed','schema_version':SCHEMA_VERSION,'model_calls':0,
        'source_records':rows_total,'extraction_units':len(units),'planned_batches':total_batches,
        'batch_rows':{'min':min(batch_lengths,default=0),'max':max(batch_lengths,default=0),
                      'mean':round(rows_total / total_batches,2) if total_batches else 0},
        'batch_config':batch,'model_output_limit':output_limit,'model_context_limit':model['context_tokens'],
        'catalog_input_reserve':catalog_reserve,
        'max_prompt_tokens_empty_catalog_estimate':max(prompt_sizes,default=0),
        'min_context_margin_estimate':model['context_tokens']-max(prompt_sizes,default=0)-catalog_reserve-output_limit,
        'input_tokens_empty_catalog_estimate':input_total,'graph_output_tokens_estimate':output_total,
        'prompt_sha256':sha256(template_path.read_bytes()).hexdigest(),
        'source_manifest_sha256':sha256((root/'sources_manifest.jsonl').read_bytes()).hexdigest(),
        'config_sha256':sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),
        'stop_marker_present':(root/'STOP').exists(),'reference_text_access':False,
        'token_estimate_note':'UTF-8 bytes/3 heuristic, not provider tokenization; catalog is reserved, not rendered.',
        'limitations':['No real model quality, throughput or output truncation rate has been measured.',
                      'Initial batch count excludes validation/truncation splits; no semantic deduplication check.'],
        'units':reports}
