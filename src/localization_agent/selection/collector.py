"""Parallel game streams; each step selects context, translates, then updates memory."""
import json
import signal
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict
from pathlib import Path

from localization_agent.memory import MemoryConfig, MemoryService, MemoryStore
from localization_agent.models.chat_completions import CallBudget, BudgetExceeded
from localization_agent.models.structured import parse_json_reply
from localization_agent.models.token_count import LocalTokenCounter
from localization_agent.retrieval import GraphConfig, GraphRetriever, GraphStore
from localization_agent.schemas.memory import Occurrence, ReadView, TranslationCheck, text_hash
from localization_agent.workflow.game import GameSession
from .checkpoints import JobCheckpoints, compact_job, load_state, write_json
from .teacher import DurableChat, UncertainCall, progress, translation_issues


class Collector:
    def __init__(self, folder, config, annotations, factory, game_workers, http_workers):
        if min(game_workers,http_workers)<1:
            raise ValueError('Positive worker counts required')
        self.folder, self.config, self.annotations = Path(folder), config, annotations
        self.state = load_state(folder, compact_completed=True)
        self.state['execution']={'storage':'per_job_v1','game_workers':game_workers,
                                 'http_workers':http_workers,'teacher_baseline_parallel':False,
                                 'translation_context_policy':'teacher_selected_v1'}
        self.checkpoints = JobCheckpoints(folder, self.state)
        self.budget = CallBudget(**json.loads((self.folder/'budget.json').read_text()))
        # Budget callbacks never serialize trajectories or touch game SQLite connections.
        self.budget.on_change = lambda: write_json(self.folder/'budget.json',self.budget.snapshot())
        self.chat = DurableChat(self.folder,config['models'],self.budget,factory,
            max_attempts=config.get('request_retry',{}).get('max_attempts',8),
            retry_delay=config.get('request_retry',{}).get('initial_delay_seconds',5))
        self.chat.capacity = threading.BoundedSemaphore(http_workers)
        self.game_workers = game_workers
        self.token_counter = LocalTokenCounter(config['tokenizer'])
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.active, self.outcomes = {}, {}

    def call(self, game, job, task, prompt):
        key=(game,task)
        with self.lock:
            self.active[key]={'source_id':job['source_id'],'task':task}
        try:
            return self.chat(task,prompt)
        finally:
            with self.lock:
                self.active.pop(key,None)

    def report(self):
        with self.lock:
            self.state['current_by_game']={game:{'outcome':self.outcomes.get(game,'running'),
                'tasks':[value for (owner,_),value in self.active.items() if owner==game]}
                for game in {job['game'] for job in self.state['jobs']}}
            if self.active:
                current=next(reversed(self.active.values()))
                self.state['current_task']=current['task']
                self.state['current_source_id']=current['source_id']
            else:
                self.state['current_task']=None
                self.state['current_source_id']=None
            self.checkpoints.save_index()
        progress(self.folder,self.state,self.budget)

    def process_job(self, job, store, memory, retriever, session, call):
        payload=store.db.execute('SELECT payload FROM sources WHERE project=? AND id=? ORDER BY version DESC LIMIT 1',
                                 (job['game'],job['source_id'])).fetchone()
        source=Occurrence(**json.loads(payload[0]))
        job.update(source=source.source,status='working')
        job.setdefault('issues',[])
        job.setdefault('view',asdict(store.view(job['run_id'])))
        self.checkpoints.save(job)  # Persist the exact pre-translation view before selection or translation.
        started=time.monotonic()
        try:
            pool=retriever.retrieve(source,ReadView(**job['view']),'graph')
            if job['phase']=='target':
                if 'selection_checks_passed' not in job:
                    observation=retriever.selector_observation(pool)
                    text=json.dumps(observation,ensure_ascii=False)
                    job['observation']=observation
                    job['teacher_prompt']=Path('prompts/selection/teacher_context_selector.txt').read_text().format(observation=text)
                    job['student_prompt']=Path('prompts/selection/context_selector.txt').read_text().format(observation=text)
                    self.checkpoints.save(job)
                    reply=call('teacher_select',job['teacher_prompt'])
                    try:
                        action=parse_json_reply(reply.text)
                        if not isinstance(action.get('rationale'),str) or not action['rationale'].strip():
                            raise ValueError('Teacher must provide explicit natural-language rationale')
                        pack=retriever.select_context(pool,tuple(action['selected_ids']))
                        action={'rationale':action['rationale'],'selected_ids':sorted(action['selected_ids'])}
                        job.update(action=action,selected_contexts=[c for c in observation['candidates'] if c['id'] in action['selected_ids']],
                            teacher_context=asdict(pack),selection_checks_passed=True,
                            teacher_quality='selection_program_only_unreviewed')
                    except Exception as error:
                        job['selection_checks_passed']=False
                        job['issues'].append('teacher: '+type(error).__name__+': '+str(error))
                    self.checkpoints.save(job)
                if not job['selection_checks_passed']:
                    raise ValueError('Invalid Teacher selection; translation and memory update skipped')
                # Rebuild from the same frozen pool on resume, including the zero-soft-item action.
                pack=retriever.select_context(pool,tuple(job['action']['selected_ids']))
                job['translation_context_policy']='teacher_selected_v1'
            else:
                pack=retriever.build_context(pool)
                job['translation_context_policy']='warmup_fixed_baseline'
            if pack.status!='ready':
                raise ValueError('Translation context is not ready: '+pack.status)
            job['translation_context']=asdict(pack)
            existing=[r for r in store.visible(store.view(job['run_id'])) if r.kind=='translation'
                      and r.evidence[0].occurrence_id==source.occurrence_id]
            if existing:
                saved=existing[0]
            else:
                job['translation_prompt']=(memory.config.prompt_dir/'translate_game.txt').read_text().format(
                    source=source.source,source_lang=source.source_lang,target_lang=source.target_lang,
                    contexts=pack.model_context,source_context=pack.manifest['current_source_context'])
                self.checkpoints.save(job)
                reply=call('translate_game',job['translation_prompt'])
                store.log_call('translate_game',job['translation_prompt'],asdict(reply))
                target=reply.text.strip()
                issues=translation_issues(source,target,pack,session)
                if issues:
                    raise ValueError('Translation not recorded: '+'; '.join(issues))
                check=TranslationCheck(source.occurrence_id,source.source_version,text_hash(target),pack.context_hash,
                                       'program:teacher-collection-unreviewed',False)
                saved=retriever.commit(source,target,check,pack,allow_unreviewed=True)
            job.update(baseline_translation_ref=asdict(saved.ref),memory_status='translation_recorded_unreviewed')
            job.setdefault('memory_updates_done',[])
            self.checkpoints.save(job)
            # Preserve existing within-game update order and its exact visibility semantics.
            for kind in ('term','summary','style'):
                if kind in job['memory_updates_done']:
                    continue
                try:
                    drafts=memory.extract_terms(saved.ref) if kind=='term' else retriever.update_memory(
                        source,store.view(job['run_id']),'graph',kind=kind)
                    for draft in drafts:
                        store.approve(draft.ref,'program:unreviewed-collection',verification='unreviewed')
                    job['memory_updates_done'].append(kind)
                    self.checkpoints.save(job)
                except (BudgetExceeded,UncertainCall):
                    raise
                except Exception as error:
                    job['issues'].append(kind+': '+str(error))
            job['memory_status']='updated_unreviewed' if len(job['memory_updates_done'])==3 else 'partial_unreviewed'
            job['status']='complete'
        except BudgetExceeded:
            self.stop.set()
            return 'budget_exhausted'
        except UncertainCall as error:
            job['issues'].append(str(error))
            return 'needs_attention_uncertain_call'
        except Exception as error:
            job.update(status='failed',memory_status='failed')
            job['issues'].append(type(error).__name__+': '+str(error))
        finally:
            job['elapsed_seconds']=job.get('elapsed_seconds',0)+time.monotonic()-started
            if job['status'] in {'complete','failed'}:
                job['completed_at_unix']=time.time()
            self.checkpoints.save(job)
        return None

    def game(self, game, jobs):
        # Each SQLite game store and retriever belongs to exactly one worker thread.
        store=MemoryStore(self.folder/(game+'.sqlite'))
        services={}
        outcome='complete'
        try:
            for job in jobs:
                if job['status'] in {'complete','failed'}:
                    continue
                if self.stop.is_set() or (self.folder/'STOP').exists():
                    outcome='stopped'
                    break
                if not store.db.execute('SELECT 1 FROM runs WHERE id=?',(job['run_id'],)).fetchone():
                    payload=store.db.execute('SELECT payload FROM sources WHERE project=? AND id=? ORDER BY version DESC LIMIT 1',
                                             (game,job['source_id'])).fetchone()
                    source=Occurrence(**json.loads(payload[0]))
                    store.start_run(job['run_id'],game,None,source.source_lang,source.target_lang,
                                    'teacher-knowledge',mode='game_resources')
                graph=job.get('graph_config',self.config['graph'])
                key=json.dumps(graph,sort_keys=True)
                if key not in services:
                    # Use the current job through a small mutable holder, never a different game's state.
                    holder=[job]
                    bound=lambda task,prompt,holder=holder:self.call(game,holder[0],task,prompt)
                    memory=MemoryService(store,MemoryConfig(**self.config['memory']),chat=bound)
                    retriever=GraphRetriever(GraphStore(store),memory,GraphConfig(**graph),
                        token_counter=self.token_counter,
                        allowed_source_ids=frozenset(self.config['allowed_source_ids'][game]),
                        source_annotations=self.annotations.get(game))
                    services[key]=(memory,retriever,GameSession(retriever,require_reviewer=False),bound,holder)
                memory,retriever,session,bound,holder=services[key]
                holder[0]=job
                result=self.process_job(job,store,memory,retriever,session,bound)
                if job['status'] in {'complete','failed'}:
                    compact_job(job)
                if result:
                    outcome=result
                    break
        except Exception as error:
            outcome='worker_failed'
            with self.lock:
                self.state.setdefault('worker_errors',{})[game]=type(error).__name__+': '+str(error)
        finally:
            store.close()
            with self.lock:
                self.outcomes[game]=outcome
        return outcome

    def run(self):
        groups=defaultdict(list)
        for job in self.state['jobs']:
            groups[job['game']].append(job)
        self.state['status']='running'
        handlers={}
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT,signal.SIGTERM):
                handlers[sig]=signal.signal(sig,lambda *_:self.stop.set())
        try:
            with ThreadPoolExecutor(max_workers=self.game_workers,thread_name_prefix='teacher-game') as workers:
                futures=[workers.submit(self.game,game,jobs) for game,jobs in groups.items()]
                self.report()
                while not all(f.done() for f in futures):
                    wait(futures,timeout=5)
                    self.report()
                for future in futures:
                    future.result()
            outcomes=set(self.outcomes.values())
            if 'needs_attention_uncertain_call' in outcomes:
                self.state['status']='needs_attention_uncertain_call'
            elif 'worker_failed' in outcomes:
                self.state['status']='worker_failed'
            elif 'budget_exhausted' in outcomes:
                self.state['status']='budget_exhausted'
            elif 'stopped' in outcomes:
                self.state['status']='stopped'
            else:
                failed=any(j['status']=='failed' or (j['phase']=='target' and not j.get('selection_checks_passed')) for j in self.state['jobs'])
                warnings=any(j.get('issues') or j.get('memory_status')=='partial_unreviewed' for j in self.state['jobs'])
                self.state['status']='complete_with_failures' if failed else 'complete_with_warnings' if warnings else 'complete'
        finally:
            self.chat.db.close()
            self.report()
            for sig,handler in handlers.items():
                signal.signal(sig,handler)
        return self.state


def collect(folder, config, annotations, factory, game_workers=2, http_workers=4):
    return Collector(folder,config,annotations,factory,game_workers,http_workers).run()
