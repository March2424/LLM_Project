"""Bounded external-teacher collection with fixed baseline history and durable calls."""
import html
import fcntl
import json
import os
import sqlite3
import time
import threading
import shutil
from concurrent.futures import Future
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from localization_agent.memory import MemoryConfig, MemoryService, MemoryStore
from localization_agent.models.chat_completions import (CallBudget, ChatCompletionsModel, BudgetExceeded,
                                                       RetryableModelError, ModelResponseError)
from localization_agent.models.protocols import ModelReply
from localization_agent.models.structured import parse_json_reply
from localization_agent.models.token_count import LocalTokenCounter
from localization_agent.retrieval import GraphConfig, GraphRetriever, GraphStore
from localization_agent.schemas.memory import ReadView, TranslationCheck, text_hash
from localization_agent.workflow.game import GameSession
from .checkpoints import JobCheckpoints, load_state, write_json


class UncertainCall(RuntimeError):
    pass


class CallFailed(RuntimeError):
    """A bounded request failure; record the affected item and continue the stream."""


def read_lines(path):
    with Path(path).open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def file_hash(path):
    result = sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def progress(folder, state, budget):
    jobs = state['jobs']
    finished = [job for job in jobs if job['status'] in {'complete', 'failed'}]
    targets = [job for job in jobs if job['phase'] == 'target' and job.get('trajectory_schema_version') == 2]
    passed = sum(job.get('selection_checks_passed', False) for job in targets)
    available = Counter(kind for job in targets for kind in {
        c['memory_kind'] for c in job.get('observation',{}).get('candidates',[])})
    chosen = Counter(kind for job in targets for kind in {
        c['memory_kind'] for c in job.get('selected_contexts',[])})
    result = {'status': state['status'], 'finished': len(finished), 'total': len(jobs),
        'memory_policy': state.get('memory_policy', 'legacy_resource_groups'),
        'current_task': state.get('current_task'),
        'current_source_id': state.get('current_source_id'),
        'teacher_candidates': passed, 'teacher_target_count': len(targets),
        'candidate_available_targets_by_kind':{kind:available[kind] for kind in ('term','style','stm','ltm','summary','source','relation')},
        'selected_targets_by_kind':{kind:chosen[kind] for kind in ('term','style','stm','ltm','summary','source','relation')},
        'selection_failed': sum(j.get('selection_checks_passed') is False for j in targets),
        'memory_failed': sum(j.get('memory_status') == 'failed' for j in jobs),
        'legacy_target_count': sum(j['phase']=='target' and j.get('trajectory_schema_version') != 2 for j in jobs),
        'current_by_game':state.get('current_by_game',{}),
        'execution':state.get('execution',{}),
        'worker_errors':dict(state.get('worker_errors',{})),
        'failed': sum(job['status'] == 'failed' for job in jobs), 'budget': budget.snapshot()}
    result['games']={game:{'finished':sum(j['game']==game and j['status'] in {'complete','failed'} for j in jobs),
                          'total':sum(j['game']==game for j in jobs)} for game in sorted({j['game'] for j in jobs})}
    write_json(folder / 'progress.json', result)
    rows = []
    displayed={id(job) for job in sorted(finished,key=lambda j:j.get('completed_at_unix',0))[-30:]}
    displayed.update(id(job) for job in jobs if job['status']=='working')
    for index,job in enumerate(jobs):
        if id(job) not in displayed:
            continue
        checkpoint = folder/'jobs'/f'{index:06d}.json'
        if job['status'] in {'complete','failed'} and checkpoint.exists():
            job = json.loads(checkpoint.read_text())
        action = job.get('action', {})
        values = [job['phase'], job['game'], job['source_id'], job['status'],
            job.get('source', ''), action.get('rationale', ''),
            ', '.join(action.get('selected_ids', [])),
            json.dumps(job.get('selected_contexts', []), ensure_ascii=False),
            job.get('memory_status', 'pending'),
            '; '.join(job.get('issues', []))]
        cells=['<td>'+html.escape(str(v))+'</td>' for v in values]
        if (folder/'jobs'/f'{index:06d}.json').exists():
            cells[2]=f'<td><a href="jobs/{index:06d}.json">'+html.escape(job['source_id'])+'</a></td>'
        rows.append('<tr>'+''.join(cells)+'</tr>')
    page = '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="5">'
    page += '<title>Teacher trajectory progress</title><style>body{font:15px system-ui;margin:28px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;vertical-align:top}td{max-width:360px;overflow-wrap:anywhere}progress{width:100%;height:28px}</style>'
    page += '<h1>Teacher 轨迹进度</h1><p>Teacher 只生成分析与上下文选择；独立翻译仅更新记忆。程序检查不等于语义验收。5 秒刷新。</p>'
    page += '<p>表格显示最近30条完成记录及当前任务；完整逐条检查点见 jobs/，源ID链接打开对应JSON。</p>'
    page += '<progress max="' + str(max(1, len(jobs))) + '" value="' + str(len(finished)) + '"></progress>'
    page += '<pre>' + html.escape(json.dumps(result, ensure_ascii=False, indent=2)) + '</pre>'
    page += '<table><tr>' + ''.join('<th>' + v + '</th>' for v in ['阶段','游戏','ID','状态','原文','自然语言分析','所选 ID','选中上下文','记忆更新','提示']) + '</tr>' + ''.join(rows) + '</table>'
    (folder / 'progress.html').write_text(page, encoding='utf-8')
    games=' '.join(f"{game}:{counts['finished']}/{counts['total']}" for game,counts in result['games'].items())
    print(f"[{len(finished)}/{len(jobs)}] {state['status']} | teacher {passed}/{len(targets)} | failed {result['failed']} | API {result['budget']['calls']} | ${result['budget']['cost']:.6f} | {games}", flush=True)


def prepare(folder, plan, model_config, max_cost, max_calls=10000, max_tokens=10000000):
    folder, plan = Path(folder), Path(plan)
    sequences = read_lines(plan / 'pilot_sequences.jsonl')
    splits = read_lines(plan / 'splits.jsonl')
    if any(s['split'] != 'train' for s in sequences):
        raise ValueError('Teacher collection accepts train sequences only')
    games = [s['game'] for s in sequences]
    if len(games) != len(set(games)) or any(s.get('memory_scope') != 'game_split' for s in sequences):
        raise ValueError('Regenerate the plan: one game/train memory stream and one warmup per game are required')
    allowed = {}
    group_ids = {}
    for row in splits:
        if row['split'] == 'train' and row['source_usable']:
            allowed.setdefault(row['game'], []).append(row['source_id'])
            group_ids[(row['game'], row['source_id'])] = row['group_id']
    model = json.loads(Path(model_config).read_text())['model']
    if any(model.get(k) is None for k in ('input_price_per_million', 'output_price_per_million')):
        raise ValueError('Explicit model token prices are required for the money limit')
    models = {'selection': {**model, 'max_output_tokens': 512}, 'translation': model}
    games = sorted({s['game'] for s in sequences})
    dbs = {'flare': 'outputs/flare_full_2026-10-31/graph.sqlite',
        'shattered': 'outputs/shattered_full_2026-11-01/graph.sqlite',
        'naev': 'outputs/naev_full_2026-11-01/graph.sqlite'}
    projects_path = plan / 'source_projects.json'
    projects = json.loads(projects_path.read_text()) if projects_path.exists() else {}
    dbs.update({key: spec['source_db'] for key, spec in projects.items()})
    folder.mkdir(parents=True, exist_ok=False)
    jobs, assets = [], {}
    tokenizer = Path('artifacts/retrieval_tokenizer/tokenizer.json').resolve()
    assets[str(tokenizer)] = file_hash(tokenizer)
    for game in games:
        source = Path(dbs[game]).resolve()
        assets[str(source)] = file_hash(source)
        original = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
        store = MemoryStore(folder / (game + '.sqlite'))
        try:
            if original.execute('SELECT COUNT(*) FROM runs').fetchone()[0]:
                raise ValueError('Use a source-only extraction database, not a translation trial')
            original.backup(store.db)
            store.create_snapshot('teacher-knowledge', game)
        finally:
            original.close()
            store.close()
    for sequence in sequences:
        run = 'teacher_sft:' + sequence['game'] + ':' + sequence['split']
        for phase, keys in (('warmup', sequence['warmup_ids']), ('target', sequence['target_ids'])):
            for key in keys:
                if key not in allowed[sequence['game']]:
                    raise ValueError('Sequence source is outside train visibility')
                jobs.append({'game': sequence['game'], 'run_id': run, 'source_id': key,
                    'split': sequence['split'], 'group_id': group_ids[(sequence['game'], key)],
                    'phase': phase, 'status': 'pending',
                    'trajectory_schema_version': 2,
                    **{k: sequence[k] for k in ('corpus','source_lang','target_lang','origin_split',
                                                'seen_evaluation_overlap') if k in sequence}})
    prompts = {str(p.resolve()): file_hash(p) for root in ('prompts/memory', 'prompts/selection')
               for p in Path(root).rglob('*.txt')}
    code = {str(p.resolve()): file_hash(p) for p in Path('src/localization_agent').rglob('*.py')}
    config = {'trajectory_schema_version': 2, 'teacher_translation_generated': False,
        'memory_policy': 'per_game_split_stage_v1', 'collection_stage': 'teacher_sft',
        'models': models, 'allowed_source_ids': allowed, 'assets': assets,
        'prompts': prompts, 'code': code, 'tokenizer': str(tokenizer),
        'graph': asdict(GraphConfig.from_file(plan/'graph_config.json' if (plan/'graph_config.json').exists()
                                            else 'configs/graph.json')),
        'memory': {'stm_size': 3, 'ltm_top_k': 2, 'summary_step': 5},
        'source_visibility': 'train_source_ids_and_their_supplied_native_context',
        'reference_text_access': False, 'require_reviewer': False,
        'plan_sha256': {name: file_hash(plan / name) for name in ('splits.jsonl','pilot_sequences.jsonl')},
        'model_config_origin': str(model_config), 'max_cost_is_estimated_not_provider_billing': True}
    if projects:
        config['source_projects'] = projects
        config['plan_sha256']['source_projects.json'] = file_hash(projects_path)
    if (plan/'native_bindings.json').exists():
        config['native_bindings']={'path':str((plan/'native_bindings.json').resolve()),
                                   'sha256':file_hash(plan/'native_bindings.json')}
        config['memory']['style_min_examples']=2
        config['memory_coverage']=json.loads((plan/'summary.json').read_text())['memory_coverage']
    write_json(folder / 'config.json', config)
    budget = CallBudget(max_calls=max_calls, max_tokens=max_tokens, max_cost=max_cost)
    write_json(folder / 'budget.json', asdict(budget))
    state = {'status': 'prepared_no_paid_calls', 'memory_policy': config['memory_policy'], 'jobs': jobs}
    write_json(folder / 'state.json', state)
    progress(folder, state, budget)


class DurableChat:
    """Cache successes, retry transient failures, and stop on interrupted unknown requests."""
    def __init__(self, folder, models, budget, factory=ChatCompletionsModel, on_task=None,
                 max_attempts=8, retry_delay=5):
        self.db = sqlite3.connect(folder / 'calls.sqlite', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS calls (key TEXT PRIMARY KEY, task TEXT, prompt TEXT, status TEXT, reply TEXT, error TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS call_attempts (key TEXT,attempt INTEGER,status TEXT,error TEXT,at_unix REAL)')
        self.models = {key: factory(**value, budget=budget) for key, value in models.items()}
        self.on_task = on_task
        self.lock = threading.RLock()
        self.pending = {}
        self.capacity = threading.BoundedSemaphore(4)
        if max_attempts < 1 or retry_delay < 0:
            raise ValueError('Invalid request retry limits')
        self.max_attempts, self.retry_delay = max_attempts, retry_delay

    def request(self, key, task, prompt):
        for attempt in range(1, self.max_attempts + 1):
            try:
                with self.capacity:
                    reply = self.models['selection' if task == 'teacher_select' else 'translation'](task, prompt)
            except (RetryableModelError, ModelResponseError) as error:
                with self.lock:
                    self.db.execute('INSERT INTO call_attempts VALUES (?,?,?,?,?)',
                                    (key,attempt,'failed',str(error),time.time()))
                    self.db.commit()
                if isinstance(error, ModelResponseError) or attempt == self.max_attempts:
                    raise CallFailed(f'{task} failed after {attempt} attempt(s): {error}') from error
                delay = min(60, self.retry_delay * 2 ** (attempt - 1))
                print(f'[retry] {task} {key[:12]} attempt {attempt}/{self.max_attempts}: '
                      f'{error}; waiting {delay}s', flush=True)
                time.sleep(delay)
            else:
                with self.lock:
                    self.db.execute('INSERT INTO call_attempts VALUES (?,?,?,?,?)',
                                    (key,attempt,'complete',None,time.time()))
                    self.db.commit()
                return reply

    def __call__(self, task, prompt):
        key = text_hash(task + '\n' + prompt)
        with self.lock:
            row = self.db.execute('SELECT status,reply,error FROM calls WHERE key=?', (key,)).fetchone()
            if row and row[0] == 'complete':
                return ModelReply(**json.loads(row[1]))
            if key in self.pending:
                future, owner = self.pending[key], False
            elif row:
                if row[0] == 'failed' and (row[2] or '').startswith('handled_failure: '):
                    raise CallFailed(row[2])
                raise UncertainCall(f'Call {key[:12]} is {row[0]}; inspect calls.sqlite before any manual retry')
            else:
                future, owner = Future(), True
                self.pending[key] = future
                self.db.execute('INSERT INTO calls VALUES (?,?,?, ?,NULL,NULL)', (key, task, prompt, 'inflight'))
                self.db.commit()
        if not owner:
            return future.result()
        try:
            if self.on_task is not None:
                self.on_task(task)
            reply = self.request(key, task, prompt)
        except BudgetExceeded as error:
            with self.lock:
                self.db.execute('DELETE FROM calls WHERE key=?', (key,))
                self.db.commit()
                self.pending.pop(key)
                future.set_exception(error)
            raise
        except CallFailed as error:
            with self.lock:
                self.db.execute('UPDATE calls SET status=?,error=? WHERE key=?',
                                ('failed','handled_failure: '+str(error),key))
                self.db.commit()
                self.pending.pop(key)
                future.set_exception(error)
            raise
        except Exception as error:
            uncertain = UncertainCall('External call failed; inspect calls.sqlite before retry: ' + key[:12])
            with self.lock:
                self.db.execute('UPDATE calls SET status=?,error=? WHERE key=?', ('failed', str(error), key))
                self.db.commit()
                self.pending.pop(key)
                future.set_exception(uncertain)
            raise uncertain from error
        with self.lock:
            self.db.execute('UPDATE calls SET status=?,reply=? WHERE key=?', ('complete', json.dumps(asdict(reply)), key))
            self.db.commit()
            self.pending.pop(key)
            future.set_result(reply)
        return reply


def translation_issues(source, target, pack, session):
    issues = []
    if Counter(session.placeholders(source.source)) != Counter(session.placeholders(target)):
        issues.append('Placeholder/tag mismatch')
    for item in pack.selected:
        if item.mandatory and item.category == 'term':
            term = session.memory.store.get(item.memory_ref)
            if term.content['target'] not in target:
                issues.append('Mandatory term missing: ' + term.content['source'])
    return issues


def run(folder, factory=ChatCompletionsModel, *, game_workers=2, http_workers=4):
    folder = Path(folder)
    with (folder / '.run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another collection process is using this output directory') from None
        return _run(folder, factory, game_workers, http_workers)


def _run(folder, factory, game_workers=2, http_workers=4):
    folder = Path(folder)
    config = json.loads((folder / 'config.json').read_text())
    if config.get('trajectory_schema_version') != 2:
        raise ValueError('Legacy collection: preserve it and prepare a new per-game plan/output')
    if config.get('memory_policy') != 'per_game_split_stage_v1':
        raise ValueError('Legacy resource-group memories cannot become a single game history; prepare a new per-game plan/output')
    for category in ('prompts', 'code'):
        for path, expected in config[category].items():
            if file_hash(path) != expected:
                raise ValueError('Prepared ' + category + ' changed; prepare a new output directory')
    if file_hash(config['tokenizer']) != config['assets'][config['tokenizer']]:
        raise ValueError('Budget tokenizer changed after preparation')
    annotations={}
    if config.get('native_bindings'):
        binding=config['native_bindings']
        if file_hash(binding['path'])!=binding['sha256']:
            raise ValueError('Native source bindings changed after preparation')
        annotations=json.loads(Path(binding['path']).read_text())
    if factory is ChatCompletionsModel:
        for spec in config['models'].values():
            key_name = spec.get('api_key_env', 'LLM_API_KEY')
            if not os.environ.get(key_name):
                raise RuntimeError('Missing credential environment variable: ' + key_name)
    from .collector import collect
    return collect(folder, config, annotations, factory, game_workers, http_workers)


def export(folder, dataset_dir=None):
    folder = Path(folder)
    config = json.loads((folder / 'config.json').read_text())
    if config.get('memory_policy') != 'per_game_split_stage_v1':
        raise ValueError('Legacy resource-group trajectories are audit data, not the current per-game training dataset')
    state = load_state(folder)
    rows = [{'instruction': job['student_prompt'], 'input': '',
             'output': json.dumps(job['action'], ensure_ascii=False)} for job in state['jobs']
            if job['phase']=='target' and job.get('trajectory_schema_version') == 2 and job.get('selection_checks_passed')]
    availability=Counter(kind for job in state['jobs'] if job['phase']=='target' and job.get('selection_checks_passed')
                         for kind in {c['memory_kind'] for c in job['observation']['candidates']})
    path = folder / 'selector_sft_candidates.json'
    write_json(path, rows)
    write_json(folder / 'export.json', {'rows':len(rows), 'path':str(path),
        'semantic_reviewed':False, 'quality':'program_only_candidates_not_gold',
        'teacher_translation_generated':False, 'translation_is_label':False,
        'memory_policy':config['memory_policy'],
        'translation_context_policy_counts':dict(Counter(j.get('translation_context_policy','legacy_fixed_baseline')
            for j in state['jobs'] if j['phase']=='target' and j.get('selection_checks_passed'))),
        'candidate_available_targets_by_kind':dict(availability),
        'legacy_targets_excluded':sum(j['phase']=='target' and j.get('trajectory_schema_version') != 2 for j in state['jobs'])})
    if dataset_dir is not None:
        if config.get('native_bindings') and not all(availability[kind]>0 for kind in ('style','summary')):
            raise ValueError('Inspect coverage first: no style/summary candidate exposure; raw candidates remain exported for audit')
        if not rows:
            raise ValueError('No valid current-protocol selections to export as training data')
        directory = Path(dataset_dir)
        directory.mkdir(parents=True, exist_ok=True)
        registry = json.loads(Path('configs/training/dataset_info.json').read_text())
        target = directory / 'dataset_info.json'
        if target.exists() and json.loads(target.read_text()) != registry:
            raise ValueError('Dataset registry differs; inspect before replacing it')
        write_json(target, registry)
        training_file = directory / registry['selector_sft_train']['file_name']
        write_json(training_file, rows)
        write_json(directory / 'selector_sft_train.metadata.json', {
            'source_run':str(folder.resolve()), 'rows':len(rows), 'sha256':file_hash(training_file),
            'quality':'selection_program_only_unreviewed', 'trajectory_schema_version':2,
            'translation_is_label':False, 'max_selected':8, 'dev_enabled':False,
            'memory_policy':config['memory_policy']})
    return len(rows)


def extend(source, output, plan, max_cost):
    """Continue a stopped per-game pilot in a copy, preserving memory, calls and warmup."""
    source, output, plan = Path(source), Path(output), Path(plan)
    with (source / '.run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = json.loads((source / 'config.json').read_text())
        state = load_state(source)
        if config.get('memory_policy') != 'per_game_split_stage_v1':
            raise ValueError('Extension requires the per-game memory policy')
        if any(j['status'] not in {'complete','failed'} for j in state['jobs']):
            raise ValueError('Finish or inspect the pilot before extending it')
        if file_hash(plan / 'splits.jsonl') != config['plan_sha256']['splits.jsonl']:
            raise ValueError('Extension must preserve the same source split')
        for path, expected in config['prompts'].items():
            if file_hash(path) != expected:
                raise ValueError('Prompts changed; extension would change the sampling protocol')
        sequences = read_lines(plan / 'pilot_sequences.jsonl')
        seen = {(j['game'],j['source_id']) for j in state['jobs'] if j['phase']=='target'}
        requested = {(s['game'],key) for s in sequences for key in s['target_ids']}
        if not seen < requested:
            raise ValueError('Expanded targets must include the pilot and add new targets')
        groups = {(r['game'],r['source_id']):r['group_id'] for r in read_lines(plan / 'splits.jsonl')}
        additions = []
        for sequence in sequences:
            game = sequence['game']
            old = [j for j in state['jobs'] if j['game']==game]
            if (sequence.get('memory_scope') != 'game_split' or sequence['split'] != 'train'
                    or sequence['warmup_ids'] != [j['source_id'] for j in old if j['phase']=='warmup']):
                raise ValueError('Keep the same games, train streams and one original warmup')
            run_ids = {j['run_id'] for j in old}
            if len(run_ids) != 1:
                raise ValueError('Expected one existing memory run per game')
            for key in sequence['target_ids']:
                if key not in config['allowed_source_ids'][game]:
                    raise ValueError('Expanded target is outside train visibility')
                if (game,key) not in seen:
                    additions.append({'game':game,'run_id':next(iter(run_ids)),'source_id':key,
                        'split':'train','group_id':groups[(game,key)],'phase':'target','status':'pending',
                        'trajectory_schema_version':2})
        budget = json.loads((source / 'budget.json').read_text())
        if max_cost <= budget['cost']:
            raise ValueError('Expanded cumulative budget must exceed the amount already spent')
        output.mkdir(parents=True, exist_ok=False)
        for name in ('config.json','state.json','budget.json'):
            write_json(output / ('parent-' + name), json.loads((source / name).read_text()))
        for path in source.glob('*.sqlite'):
            original = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
            target = sqlite3.connect(output / path.name)
            try:
                original.backup(target)
            finally:
                original.close()
                target.close()
        config['extension'] = {'source':str(source.resolve()),'retained_targets':len(seen),
            'added_targets':len(additions),'new_warmup':0,'old_frozen_states_preserved':True}
        config['plan_sha256'] = {name:file_hash(plan / name) for name in ('splits.jsonl','pilot_sequences.jsonl')}
        config['code'] = {str(p.resolve()):file_hash(p) for p in Path('src/localization_agent').rglob('*.py')}
        if (plan/'native_bindings.json').exists():
            config['native_bindings']={'path':str((plan/'native_bindings.json').resolve()),
                                       'sha256':file_hash(plan/'native_bindings.json')}
            config['memory']['style_min_examples']=2
            config['memory_coverage']=json.loads((plan/'summary.json').read_text())['memory_coverage']
            config['extension']['native_bindings_apply_to']='new_targets_only_original_observations_unchanged'
        budget['max_cost'] = max_cost
        state['jobs'].extend(additions)
        state['status'] = 'extended_no_new_paid_calls'
        state['current_task'] = None
        write_json(output / 'config.json', config)
        write_json(output / 'state.json', state)
        write_json(output / 'budget.json', budget)
        progress(output, state, CallBudget(**budget))


def retry_call(folder, key):
    """Explicitly authorize one new attempt; retain the unknown charge and original row."""
    folder=Path(folder)
    with (folder/'.run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        db=sqlite3.connect(folder/'calls.sqlite')
        try:
            row=db.execute('SELECT key,task,prompt,status,reply,error FROM calls WHERE key=?',(key,)).fetchone()
            if not row or row[3] not in {'inflight','failed'}:
                raise ValueError('Retry requires the full key of an unresolved request')
            db.execute('CREATE TABLE IF NOT EXISTS call_retry_audit (key TEXT,task TEXT,prompt TEXT,old_status TEXT,reply TEXT,error TEXT,authorized_at REAL,resolution TEXT)')
            db.execute('INSERT INTO call_retry_audit VALUES (?,?,?,?,?,?,?,?)',(*row,time.time(),'user_authorized_possible_duplicate_charge'))
            db.execute('DELETE FROM calls WHERE key=?',(key,))
            db.commit()
        finally:
            db.close()


def upgrade(source, output, retry_key=None):
    """Copy a stopped run into per-job storage, preserving calls, views, memory and costs."""
    source,output=Path(source),Path(output)
    with (source/'.run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        config=json.loads((source/'config.json').read_text())
        if config.get('memory_policy')!='per_game_split_stage_v1':
            raise ValueError('Runtime upgrade requires the existing per-game policy')
        for path,expected in config['prompts'].items():
            if file_hash(path)!=expected:
                raise ValueError('Prompts changed; do not silently change the sampling protocol')
        runtime_files={'selection/teacher.py','selection/collector.py','selection/checkpoints.py','models/chat_completions.py'}
        for path,expected in config['code'].items():
            relative=str(Path(path).relative_to(Path('src/localization_agent').resolve()))
            if relative not in runtime_files and file_hash(path)!=expected:
                raise ValueError('Non-runtime code changed; inspect before reusing frozen histories: '+relative)
        state=load_state(source)
        output.mkdir(parents=True,exist_ok=False)
        for name in ('config.json','state.json','budget.json'):
            shutil.copyfile(source/name,output/('parent-'+name))
        for path in source.glob('*.sqlite'):
            original=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
            target=sqlite3.connect(output/path.name)
            try:
                original.backup(target)
            finally:
                original.close()
                target.close()
        # Preserve paid legacy history; never relabel its baseline translations as Teacher-selected.
        for job in state['jobs']:
            if job['status'] in {'complete','failed'} or job.get('baseline_translation_ref'):
                job.setdefault('translation_context_policy','legacy_fixed_baseline')
        config['translation_context_policy']='teacher_selected_v1'
        config['inherited_translation_context_counts']=dict(Counter(
            j['translation_context_policy'] for j in state['jobs'] if 'translation_context_policy' in j))
        config['runtime_upgrade']={'source':str(source.resolve()),'storage':'per_job_v1',
            'game_workers':2,'http_workers':4,'teacher_baseline_parallel':False,
            'translation_context_policy':'teacher_selected_v1',
            'memory_update_order':['term','summary','style'],
            'finished_preserved':sum(j['status'] in {'complete','failed'} for j in state['jobs']),
            'parent_code':config['code']}
        config['code']={str(p.resolve()):file_hash(p) for p in Path('src/localization_agent').rglob('*.py')}
        write_json(output/'config.json',config)
        shutil.copyfile(source/'budget.json',output/'budget.json')
        state['status']='upgraded_ready_no_new_paid_calls'
        state['current_task']=None
        write_json(output/'state.json',state)
        JobCheckpoints(output,state)
        if retry_key:
            retry_call(output,retry_key)
            write_json(output/'retry_authorization.json',{'key':retry_key,'action':'retry_once',
                'source':'explicit_user_authorization','old_charge_preserved':True,'new_paid_calls':0})
        progress(output,state,CallBudget(**json.loads((output/'budget.json').read_text())))


def migrate(source, output):
    """Copy a stopped v1 run, retain its paid responses/history and upgrade pending jobs."""
    source, output = Path(source), Path(output)
    with (source / '.run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Stop the source sampler before migration') from None
        old_config = json.loads((source / 'config.json').read_text())
        old_state = load_state(source)
        if old_config.get('trajectory_schema_version') == 2:
            raise ValueError('This run already uses the selection-only protocol')
        for path, expected in old_config['prompts'].items():
            if Path(path).parent.name == 'memory' and file_hash(path) != expected:
                raise ValueError('Memory prompts changed; cannot safely replay the interrupted baseline')
        if (source / 'calls.sqlite').exists():
            db = sqlite3.connect((source / 'calls.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
            try:
                unresolved = db.execute("SELECT COUNT(*) FROM calls WHERE status!='complete'").fetchone()[0]
                if unresolved:
                    raise UncertainCall('Source has unresolved external requests; inspect before migrating')
            finally:
                db.close()
        output.mkdir(parents=True, exist_ok=False)
        for name in ('config.json','state.json','budget.json'):
            write_json(output / ('legacy-' + name), json.loads((source / name).read_text()))
        for path in source.glob('*.sqlite'):
            original = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
            target = sqlite3.connect(output / path.name)
            try:
                original.backup(target)
            finally:
                target.close()
                original.close()
        config = {**old_config, 'trajectory_schema_version': 2,
            'memory_policy': old_config.get('memory_policy', 'legacy_resource_groups'),
            'teacher_translation_generated': False,
            'graph': {**old_config['graph'], 'max_selected': 8},
            'migration': {'source':str(source.resolve()), 'legacy_complete_jobs':sum(
                j['status'] in {'complete','failed'} for j in old_state['jobs'])}}
        config['prompts'] = {str(p.resolve()):file_hash(p) for root in ('prompts/memory','prompts/selection')
                             for p in Path(root).rglob('*.txt')}
        config['code'] = {str(p.resolve()):file_hash(p) for p in Path('src/localization_agent').rglob('*.py')}
        for job in old_state['jobs']:
            started = 'view' in job or job['status'] in {'complete','failed'}
            job['trajectory_schema_version'] = 1 if started and job['phase']=='target' else 2
            if started:
                # The interrupted baseline must reproduce its original prompt for cache replay.
                job['graph_config'] = old_config['graph']
                job['selection_checks_passed'] = bool(job.get('teacher_checks_passed', False))
                if job['status']=='complete':
                    job['memory_status'] = 'legacy_recorded_unreviewed'
            for key in ('teacher_translation','translation_origin','teacher_checks_passed'):
                job.pop(key, None)
        old_state['memory_policy'] = config['memory_policy']
        old_state['status'] = ('migrated_no_new_paid_calls' if config['memory_policy'] == 'per_game_split_stage_v1'
                               else 'archived_legacy_resource_groups_not_current_training')
        budget_data = json.loads((source / 'budget.json').read_text())
        write_json(output / 'config.json', config)
        write_json(output / 'state.json', old_state)
        write_json(output / 'budget.json', budget_data)
        progress(output, old_state, CallBudget(**budget_data))
