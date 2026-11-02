"""Offline concurrency/recovery checks; no external model, weights or tokenizer download."""
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from localization_agent.memory import MemoryStore
from localization_agent.models.chat_completions import CallBudget, BudgetExceeded
from localization_agent.models.protocols import ModelReply
from localization_agent.retrieval import GraphStore, GraphConfig
from localization_agent.schemas.graph import GraphNode
from localization_agent.schemas.memory import Occurrence, Scope, Evidence
from localization_agent.selection.checkpoints import JobCheckpoints, load_state, write_json
from localization_agent.selection.teacher import DurableChat, run, upgrade, export, file_hash, retry_call


def fixture_folder(folder, games=('flare','shattered'), targets=2):
    folder.mkdir()
    tokenizer=folder/'tokenizer.json';tokenizer.write_text('{}')
    jobs=[];allowed={}
    for game in games:
        store=MemoryStore(folder/(game+'.sqlite'));graph=GraphStore(store)
        allowed[game]=[]
        for i in range(targets+1):
            key=game+'-'+str(i)
            row=Occurrence(game,'scene',key,i,key,metadata={'text_type':'dialogue'})
            store.put_source(row)
            graph.put_node(GraphNode(key,'occurrence',Scope(game,'scene'),{'occurrence_id':key},
                                     (Evidence(key,1,key),)))
            jobs.append({'game':game,'run_id':'teacher_sft:'+game+':train','source_id':key,
                         'split':'train','group_id':game+':resource:'+str(i),
                         'phase':'warmup' if i==0 else 'target','status':'pending','trajectory_schema_version':2})
            allowed[game].append(key)
        graph.create_snapshot('graph',game);store.create_snapshot('teacher-knowledge',game);store.close()
    config={'trajectory_schema_version':2,'memory_policy':'per_game_split_stage_v1','collection_stage':'teacher_sft',
            'models':{'selection':{},'translation':{}},'prompts':{},'code':{},'allowed_source_ids':allowed,
            'assets':{str(tokenizer):file_hash(tokenizer)},'tokenizer':str(tokenizer),
            'graph':asdict(GraphConfig()),'memory':{'stm_size':3,'ltm_top_k':2,'summary_step':5}}
    write_json(folder/'config.json',config)
    write_json(folder/'state.json',{'status':'fixture','jobs':jobs})
    write_json(folder/'budget.json',asdict(CallBudget(max_calls=100,max_tokens=10000,max_cost=1)))


class RuntimeModel:
    lock=threading.Lock()
    calls=[]
    barriers={}
    folder=None

    def __init__(self, budget, **kwargs):self.budget=budget

    def __call__(self, task, prompt):
        self.budget.reserve(10,.000001)
        with self.lock:self.calls.append((task,prompt))
        if task=='teacher_select':
            obs=json.loads(prompt.split('Observation: ',1)[1]);key=obs['source_id']
            row=next(j for j in load_state(self.folder)['jobs'] if j['source_id']==key)
            assert row['view'] and row['teacher_prompt']==prompt
            return ModelReply(json.dumps({'rationale':'Current evidence supplies a matching source definition.','selected_ids':[]}), 'fixture')
        if task=='translate_game':
            key=prompt.split('\nSource: ',1)[1].split('\nCurrent resource context:',1)[0]
            if key.endswith('-0'):
                barrier=self.barriers.get('games')
                if barrier:barrier.wait(timeout=5)
            else:
                row=next(j for j in load_state(self.folder)['jobs'] if j['source_id']==key)
                assert row['selection_checks_passed']
                assert row['translation_prompt']==prompt
                assert row['translation_context']==row['teacher_context']
                assert row['translation_context']['model_context'] in prompt
            return ModelReply('译文 '+key,'fixture')
        if task=='extract_terms':return ModelReply('[]','fixture')
        return ModelReply('fixture note','fixture')


class TeacherRuntimeTests(unittest.TestCase):
    @patch('localization_agent.selection.collector.LocalTokenCounter',return_value=len)
    def test_parallel_games_with_selection_before_translation_and_memory(self, _):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'run';fixture_folder(folder)
            RuntimeModel.calls=[];RuntimeModel.folder=folder
            RuntimeModel.barriers={'games':threading.Barrier(2)}
            state=run(folder,RuntimeModel)
            self.assertEqual(state['status'],'complete',state.get('worker_errors'))
            for game in ('flare','shattered'):
                jobs=[j for j in state['jobs'] if j['game']==game]
                self.assertEqual([j['view']['watermark'] for j in jobs],[0,1,2])
                self.assertEqual(len({j['run_id'] for j in jobs}),1)
            index=json.loads((folder/'state.json').read_text())
            self.assertEqual(index['storage_format'],'per_job_v1')
            self.assertTrue(all('teacher_context' not in row for row in index['jobs']))
            self.assertEqual(len(load_state(folder)['jobs']),6)
            before=len(RuntimeModel.calls);run(folder,RuntimeModel)
            self.assertEqual(len(RuntimeModel.calls),before)

    def test_shared_cache_deduplicates_concurrent_identical_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            entered=threading.Event();release=threading.Event();calls=[]
            class Model:
                def __init__(self,budget,**kwargs):self.budget=budget
                def __call__(self,task,prompt):
                    self.budget.reserve(1,.01);calls.append(prompt);entered.set()
                    self.assert_released=release.wait(timeout=5)
                    return ModelReply('same reply','fixture')
            budget=CallBudget(max_calls=1,max_tokens=100,max_cost=1)
            chat=DurableChat(Path(tmp),{'selection':{},'translation':{}},budget,Model)
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(chat,'extract_terms','same prompt');self.assertTrue(entered.wait(5))
                second=pool.submit(chat,'extract_terms','same prompt');release.set()
                self.assertEqual(first.result(),second.result())
            self.assertEqual(calls,['same prompt']);self.assertEqual(budget.calls,1)
            chat.db.close()

    def test_concurrent_budget_reservations_respect_cumulative_cap(self):
        budget=CallBudget(max_calls=100,max_tokens=100,max_cost=1)
        def reserve(_):
            try:budget.reserve(1,.6);return True
            except BudgetExceeded:return False
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(reserve,range(16)))
        self.assertEqual(sum(results),1);self.assertEqual(budget.snapshot()['cost'],.6)
        settled=CallBudget(max_calls=100,max_tokens=10000,max_cost=100)
        def finish(_):
            settled.reserve(10,.1);settled.settle(10,.1,3,.03)
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(finish,range(64)))
        self.assertEqual(settled.tokens,192);self.assertEqual(settled.estimated_usage_calls,0)
        self.assertAlmostEqual(settled.cost,1.92)

    @patch('localization_agent.selection.collector.LocalTokenCounter',return_value=len)
    def test_failed_teacher_prevents_translation_until_successful_retry(self, _):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'run';fixture_folder(folder,games=('flare',),targets=1)
            RuntimeModel.calls=[];RuntimeModel.barriers={};RuntimeModel.folder=folder
            class FailOnce(RuntimeModel):
                fail=True
                def __call__(self,task,prompt):
                    if task=='teacher_select' and FailOnce.fail:
                        FailOnce.fail=False;self.budget.reserve(10,.000001)
                        raise RuntimeError('fixture unknown request outcome')
                    return super().__call__(task,prompt)
            state=run(folder,FailOnce)
            self.assertEqual(state['status'],'needs_attention_uncertain_call')
            db=sqlite3.connect(folder/'calls.sqlite')
            key,=db.execute("SELECT key FROM calls WHERE task='teacher_select' AND status='failed'").fetchone();db.close()
            before=sum(task=='translate_game' for task,_ in RuntimeModel.calls)
            self.assertEqual(before,1)  # Only the warmup was translated.
            retry_call(folder,key)
            result=run(folder,FailOnce)
            self.assertTrue(all(j['status']=='complete' for j in result['jobs']))
            self.assertTrue(result['jobs'][-1]['selection_checks_passed'])
            self.assertEqual(sum(task=='translate_game' for task,_ in RuntimeModel.calls),before+1)

    @patch('localization_agent.selection.collector.LocalTokenCounter',return_value=len)
    def test_upgrade_retries_only_authorized_uncertain_memory_request_and_keeps_charges(self, _):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'old';fixture_folder(source,games=('flare',),targets=1)
            RuntimeModel.calls=[];RuntimeModel.barriers={};RuntimeModel.folder=source
            state=run(source,RuntimeModel)
            job=state['jobs'][-1];job['status']='working';job['memory_updates_done']=[]
            checkpoints=JobCheckpoints(source,state);checkpoints.save(job);checkpoints.save_index()
            db=sqlite3.connect(source/'calls.sqlite')
            key,=db.execute("SELECT key FROM calls WHERE task='extract_terms' ORDER BY rowid DESC LIMIT 1").fetchone()
            db.execute("UPDATE calls SET status='inflight',reply=NULL WHERE key=?",(key,));db.commit();db.close()
            before_budget=(source/'budget.json').read_bytes();before_state=(source/'state.json').read_bytes()
            old_calls=len(RuntimeModel.calls)
            target=Path(tmp)/'fast';upgrade(source,target,retry_key=key)
            self.assertEqual((source/'budget.json').read_bytes(),before_budget)
            self.assertEqual((source/'state.json').read_bytes(),before_state)
            self.assertEqual(json.loads((target/'budget.json').read_text()),json.loads(before_budget))
            db=sqlite3.connect(target/'calls.sqlite');self.assertEqual(db.execute('SELECT count(*) FROM call_retry_audit').fetchone()[0],1);db.close()
            RuntimeModel.folder=target
            result=run(target,RuntimeModel)
            self.assertEqual(result['status'],'complete')
            self.assertEqual([t for t,_ in RuntimeModel.calls[old_calls:]],['extract_terms'])
            self.assertEqual(export(target),1)


if __name__=='__main__':unittest.main()
