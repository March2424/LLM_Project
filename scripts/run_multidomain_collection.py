"""Prepare or extract source graph shards; Teacher collection is a separate opt-in stage."""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from localization_agent.data.extraction_preflight import preflight
from localization_agent.selection.checkpoints import load_state, write_json
from localization_agent.selection.teacher import prepare, run, export, read_lines, file_hash
from localization_agent.schemas.extraction import SCHEMA_VERSION
from prepare_multidomain_teacher import prepare_plan


class ExtractionStopped(RuntimeError):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--credential-env-file', type=Path, default=Path('.env.extraction'))
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--extraction-config', type=Path, default=Path('configs/multidomain.extraction.json'))
    parser.add_argument('--stage', choices=('graph', 'all'), default='graph',
                        help='graph stops after extraction; all also collects Teacher trajectories')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--execute', action='store_true', help='Explicitly enable paid work for the selected stage; default is dry-run')
    parser.add_argument('--continue-after-pilot', action='store_true', help='Continue after inspecting the three source graph pilots')
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('--workers must be positive')
    if args.execute and args.dry_run:
        parser.error('Choose either --execute or --dry-run')
    args.dry_run = not args.execute
    root = args.sources.resolve()
    if not args.dry_run and (root/'STOP').exists():
        raise RuntimeError('Execution was stopped; inspect the handoff and remove STOP only after a new execution instruction')
    if args.credential_env_file.exists() and not args.dry_run:
        for line in args.credential_env_file.read_text().splitlines():
            key, sep, value = line.strip().removeprefix('export ').partition('=')
            if sep and key and not key.startswith('#'):
                os.environ.setdefault(key, value.strip().strip('\"\''))
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    units = read_lines(root/'extraction_units.jsonl')
    extraction = json.loads(args.extraction_config.read_text())
    checked = preflight(root, extraction)
    extraction_config = args.extraction_config.resolve()
    configs = root/'configs'
    translation_config = configs/'teacher_model.json'
    if not args.dry_run:
        key = extraction['model'].get('api_key_env', 'LLM_API_KEY')
        if not os.environ.get(key):
            raise RuntimeError('Missing credential environment variable: '+key)
        configs.mkdir(exist_ok=True)
        extraction_config = configs/'extraction-multidomain.json'
        values = [(extraction_config, extraction)]
        if args.stage == 'all':
            values.append((translation_config, {'model': json.loads(Path('configs/flare.memory_trial.json').read_text())['model']}))
        for path, value in values:
            if path.exists() and json.loads(path.read_text()) != value:
                raise ValueError(f'Frozen configuration changed: {path}')
            if not path.exists():
                write_json(path, value)
    env = dict(os.environ, PYTHONPATH='src')

    def graph(unit):
        if (root/'STOP').exists():
            raise ExtractionStopped('STOP requested; no new source unit started')
        folder = (root/unit['graph_db']).parent
        result_path = root/unit['graph_result']
        command = [sys.executable, '-u', 'scripts/run_benchmark_agent.py', str(root/unit['csv']),
            '--project', unit['project_id'], '--source-lang', unit['source_lang'], '--target-lang', unit['target_lang'],
            '--config', str(extraction_config), '--max-batches', '0', '--split-failures']
        if result_path.exists():
            saved = json.loads(result_path.read_text())
            if saved.get('status') == 'complete':
                batch = extraction.get('batch', {})
                expected = {'project':unit['project_id'], 'schema':SCHEMA_VERSION,
                    'inputs':{str(root/unit['csv']):file_hash(root/unit['csv'])},
                    'prompts':{'discover_graph.txt':file_hash('prompts/memory/discover_graph.txt')},
                    'options':{'source_lang':unit['source_lang'],'target_lang':unit['target_lang'],
                        'batch_size':batch.get('max_rows',64),'batch_input_tokens':batch.get('input_tokens',5000),
                        'batch_output_tokens':batch.get('output_tokens',8192)},
                    'model':extraction['model'], 'catalog_limit':extraction.get('catalog_limit',48),
                    'catalog_tokens':extraction.get('catalog_tokens',1200), 'factory':None}
                if saved.get('run_signature') != expected:
                    raise ValueError('Completed graph source/prompt/model/batching changed; use new output paths: '+unit['project_id'])
                return saved
            # Failed/unknown requests need inspection, never an implicit paid retry.
            raise RuntimeError('Inspect incomplete graph before explicit resume: '+str(result_path))
        folder.mkdir(parents=True, exist_ok=True)
        with (folder/'console.log').open('a') as log:
            result = subprocess.run(command+['--db',str(root/unit['graph_db']), '--output',str(result_path)],
                                    env=env, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError('Graph worker failed; inspect '+str(folder/'console.log'))
        return json.loads(result_path.read_text())

    with (root/'.pipeline.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.dry_run:
            write_json(root/'dry-run.json', checked)
            print(json.dumps({'dry_run_units':len(units),'planned_batches':checked['planned_batches'],'model_calls':0}))
            return
        write_json(root/'preflight.json', checked)
        state = {'status':'extracting_graphs','pid':os.getpid(),'started_at_unix':time.time(),
                 'source_manifest_sha256':file_hash(root/'sources_manifest.jsonl'),
                 'monetary_cap':extraction.get('budget',{}).get('max_cost'),'stage':args.stage,
                 'graph_units':len(units),'completed_graphs':0,'graph_usage':{}}
        archived = root/'archived_graph_usage.json'
        state['archived_graph_usage'] = json.loads(archived.read_text()) if archived.exists() else {}

        def record(unit, result):
            state['completed_graphs'] += 1
            state['graph_usage'][unit['project_id']] = result.get('budget_usage') or {}
            state['graph_cost_estimate_usd'] = sum(v.get('cost') or 0 for usage in
                (state['graph_usage'],state['archived_graph_usage']) for v in usage.values())
            write_json(root/'pipeline.json', state)
            print(f"Graphs {state['completed_graphs']}/{len(units)}; estimated ${state['graph_cost_estimate_usd']:.6f}",flush=True)

        write_json(root/'pipeline.json', state)
        try:
            # Inspect one completed extraction per language/corpus before scaling out.
            first, rest, seen = [], [], set()
            for unit in units:
                kind = (unit['source_lang'],unit['target_lang'],unit['project_id'].split('.')[0])
                if unit['split'] != 'test' and kind not in seen:
                    first.append(unit);seen.add(kind)
                else:
                    rest.append(unit)
            for unit in first:
                record(unit, graph(unit))
            if not args.continue_after_pilot:
                state['status'] = 'source_graph_pilots_ready_for_inspection'
                return
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(graph,unit):unit for unit in rest}
                try:
                    for future in as_completed(futures):
                        record(futures[future], future.result())
                except BaseException:
                    for future in futures:
                        future.cancel()
                    raise
            if (root/'STOP').exists():
                raise ExtractionStopped('STOP requested')
            if args.stage == 'graph':
                state['status'] = 'graphs_complete_teacher_not_started'
                return
            plan, teacher = root/'teacher_plan', root/'teacher'
            if not plan.exists():
                prepare_plan(root, plan)
            if not teacher.exists():
                prepare(teacher, plan, translation_config, None, max_calls=1000000, max_tokens=1000000000000)
            state['status'] = 'collecting_teacher'
            write_json(root/'pipeline.json', state)
            result = run(teacher, game_workers=args.workers, http_workers=4)
            state['teacher_status'] = result['status']
            if result['status'].startswith('complete'):
                state['valid_selection_candidates'] = export(teacher)
                state['status'] = 'collection_'+result['status']
                full = load_state(teacher)
                provenance = [{k:j.get(k) for k in ('game','source_id','source_lang','target_lang','corpus',
                    'origin_split','seen_evaluation_overlap','translation_context_policy','memory_status')}
                    for j in full['jobs'] if j['phase']=='target' and j.get('selection_checks_passed')]
                write_json(teacher/'selector_sft_candidates.provenance.json', provenance)
            else:
                state['status'] = 'teacher_'+result['status']
            state['teacher_budget'] = json.loads((teacher/'budget.json').read_text())
        except ExtractionStopped:
            state['status'] = 'stopped'
        except BaseException as error:
            state.update(status='needs_attention',error=type(error).__name__+': '+str(error))
            raise
        finally:
            state['updated_at_unix'] = time.time()
            write_json(root/'pipeline.json', state)


if __name__ == '__main__':
    main()
