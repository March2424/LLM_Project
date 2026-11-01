"""Prepare/run a source-only, bounded real translation pilot with random evidence selection."""
import argparse
import json
import os
import random
import sqlite3
import time
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from localization_agent.memory import MemoryStore, MemoryService, MemoryConfig
from localization_agent.models.chat_completions import CallBudget, ChatCompletionsModel, BudgetExceeded
from localization_agent.models.token_count import LocalTokenCounter
from localization_agent.retrieval import GraphStore, GraphRetriever, GraphConfig
from localization_agent.schemas.memory import Occurrence
from localization_agent.workflow.game import GameSession
from report_translation_pilot import report


class ObservedMemory(MemoryService):
    """Expose the real read result to diagnostics without reproducing retrieval rules."""
    def read(self, occurrence, view, *, related_ids=None):
        self.last_read=super().read(occurrence,view,related_ids=related_ids)
        return self.last_read


def write_json(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def random_pack(retrieval, pool, seed):
    ids=['c%02d'%(i+1) for i,c in enumerate(c for c in pool.candidates if not c.mandatory)]
    random.Random(f'{seed}:{pool.occurrence_id}').shuffle(ids)
    selected, skipped=[],[]
    for key in ids:
        if len(selected)>=retrieval.config.max_selected:break
        try:
            retrieval.select_context(pool,tuple(selected+[key]))
        except ValueError as error:
            if str(error)!='Selection exceeds context budget':raise
            skipped.append(key)
        else:selected.append(key)
    return retrieval.select_context(pool,tuple(selected)),{'random_order':ids,'selected_short_ids':sorted(selected),
        'skipped_for_budget':skipped,'requested':retrieval.config.max_selected}


def prepare(folder, config_path):
    config=json.loads(Path(config_path).read_text())
    folder.mkdir(parents=True,exist_ok=False)
    store=MemoryStore(folder/'run.sqlite')
    try:
        original=sqlite3.connect(Path(config['source_db']).resolve().as_uri()+'?mode=ro',uri=True)
        original.backup(store.db)
        original.close()
        store.create_snapshot('pilot-knowledge',config['project'])
        store.start_run('translation-pilot',config['project'],None,'en','zh','pilot-knowledge',mode='game_resources')
        inputs=[]
        for item in json.loads(Path(config['samples']).read_text()):
            row=store.db.execute('SELECT payload FROM sources WHERE project=? AND id=? ORDER BY version DESC LIMIT 1',
                                 (config['project'],item['source_id'])).fetchone()
            inputs.append({**item,'source':json.loads(row[0])})
        config['graph']=asdict(GraphConfig.from_file(config['graph_config']))
        config['tokenizer_sha256']=sha256(Path(config['tokenizer']).read_bytes()).hexdigest()
        config['source_db_sha256']=sha256(Path(config['source_db']).read_bytes()).hexdigest()
        config['input_sha256']=sha256(json.dumps(inputs,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        config['prompt_sha256']={str(p):sha256(p.read_bytes()).hexdigest() for p in sorted(Path('prompts/memory').glob('*.txt'))}
        config['code_sha256']={str(p):sha256(p.read_bytes()).hexdigest() for p in
            sorted(Path('src/localization_agent').rglob('*.py'))+[Path(__file__),Path('scripts/report_translation_pilot.py')]}
        config['initial_call_rowid']=store.db.execute('SELECT COALESCE(MAX(rowid),0) FROM model_calls').fetchone()[0]
        write_json(folder/'config.json',config)
        write_json(folder/'inputs.json',inputs)
        write_json(folder/'cases.json',[])
        write_json(folder/'budget.json',asdict(CallBudget(**config['budget'])))
        write_json(folder/'state.json',{'status':'prepared','planned':len(inputs),'completed':0})
        print(json.dumps({'status':'prepared','samples':len(inputs),'budget':config['budget'],'output':str(folder)}))
    finally:store.close()


def run(folder):
    config=json.loads((folder/'config.json').read_text())
    inputs=json.loads((folder/'inputs.json').read_text())
    if any(sha256(Path(p).read_bytes()).hexdigest()!=digest for p,digest in config['prompt_sha256'].items()):
        raise ValueError('Pilot prompts changed after preparation; use a new prepared run')
    if any(sha256(Path(p).read_bytes()).hexdigest()!=digest for p,digest in config.get('code_sha256',{}).items()):
        raise ValueError('Pilot code changed after preparation; use a new prepared run')
    counter=LocalTokenCounter(config['tokenizer'])
    if not counter.name.endswith(config['tokenizer_sha256']):raise ValueError('Pilot tokenizer changed')
    env=Path('.env.extraction')
    if env.exists():
        for line in env.read_text().splitlines():
            key,sep,value=line.strip().removeprefix('export ').partition('=')
            if sep and key and not key.startswith('#'):os.environ.setdefault(key,value.strip().strip('\"\''))
    budget=CallBudget(**json.loads((folder/'budget.json').read_text()))
    budget.on_change=lambda:write_json(folder/'budget.json',asdict(budget))
    model=ChatCompletionsModel(**config['model'],budget=budget)
    store=MemoryStore(folder/'run.sqlite')
    cases=json.loads((folder/'cases.json').read_text())
    status='complete'
    try:
        retrieval=GraphRetriever(GraphStore(store),ObservedMemory(store,MemoryConfig(**config['memory']),chat=model),
                                 GraphConfig(**config['graph']),token_counter=counter)
        session=GameSession(retrieval,require_reviewer=config.get('require_reviewer',True))
        finished={c['source']['occurrence_id'] for c in cases}
        for number,item in enumerate(inputs,1):
            source=Occurrence(**item['source'])
            if source.occurrence_id in finished:continue
            # A crash after a commit must not replay its paid translation blindly.
            existing=[r for r in store.visible(store.view('translation-pilot')) if r.kind=='translation'
                      and r.evidence[0].occurrence_id==source.occurrence_id]
            if existing:raise RuntimeError('Committed row lacks case checkpoint; inspect run.sqlite before resuming')
            pool=retrieval.retrieve(source,store.view('translation-pilot'),config['graph_snapshot'])
            pack,selection=random_pack(retrieval,pool,config['seed'])
            case={'number':number,'purpose':item['purpose'],'source':asdict(source),'selection':selection,
                'pool':asdict(pool),'pack':asdict(pack),'budget_before':asdict(budget),
                'memory_before':asdict(retrieval.memory.last_read)}
            before=store.db.execute('SELECT COALESCE(MAX(rowid),0) FROM model_calls').fetchone()[0]
            start=time.perf_counter()
            try:
                outcome=session.translate_one(source,'translation-pilot',config['graph_snapshot'],
                    selected_ids=tuple(c.id for c in pack.selected if not c.mandatory))
                case['outcome']=asdict(outcome)
                if outcome.translation:
                    saved=store.get(outcome.translation)
                    case['unreviewed_record' if saved.content.get('verification')=='unreviewed' else 'committed_record']=asdict(saved)
                    case['input_consistent']=(json.dumps(saved.content['context_manifest'],sort_keys=True)
                                              ==json.dumps(pack.manifest,sort_keys=True))
                case['new_memories']=[asdict(store.get(ref)) for ref in outcome.memories]
            except BudgetExceeded as error:
                case['outcome']={'status':'budget_exhausted','target':'','issues':[str(error)]}
                status='budget_exhausted'
            except Exception as error:
                case['outcome']={'status':'error','target':'','issues':[f'{type(error).__name__}: {error}']}
            calls=[dict(row) for row in store.db.execute('SELECT * FROM model_calls WHERE rowid>? ORDER BY rowid',(before,))]
            case['calls']=[{'id':r['id'],'task':r['task'],'reply':json.loads(r['response']) if r['response'] else None,
                           'error':r['error'],'prompt_hash':r['prompt_hash']} for r in calls]
            if not case['outcome']['target']:
                generated=next((c['reply']['text'] for c in case['calls'] if c['task']=='translate_game' and c['reply']),None)
                if generated:case['outcome']['target']=generated
            case['seconds']=time.perf_counter()-start
            case['budget_after']=asdict(budget)
            case['placeholder_check']={'source':session.placeholder_pattern.findall(source.source),
                'target':session.placeholder_pattern.findall(case['outcome']['target'])}
            case['placeholder_check']['passed']=Counter(case['placeholder_check']['source'])==Counter(case['placeholder_check']['target'])
            cases.append(case)
            write_json(folder/'cases.json',cases)
            write_json(folder/'state.json',{'status':'running' if status=='complete' else status,
                'planned':len(inputs),'completed':len(cases),'last_id':source.occurrence_id})
            report(folder)
            print(json.dumps({'number':number,'status':case['outcome']['status'],'calls':budget.calls,
                              'estimated_usd':round(budget.cost,6)},ensure_ascii=False),flush=True)
            if status=='budget_exhausted':break
        # Post-run reads incur no calls; prove approved memory can be retrieved again.
        probes=[]
        for item in inputs:
            source=Occurrence(**item['source'])
            pool=retrieval.retrieve(source,store.view('translation-pilot'),config['graph_snapshot'])
            probes.append({'source_id':source.occurrence_id,'pool':asdict(pool),
                           'memory_roles':asdict(retrieval.memory.last_read)})
        write_json(folder/'memory_probes.json',probes)
        if not store.db.execute('SELECT 1 FROM runs WHERE id=?',('isolation-probe',)).fetchone():
            store.start_run('isolation-probe',config['project'],None,'en','zh','pilot-knowledge',mode='game_resources')
        isolated=retrieval.retrieve(Occurrence(**inputs[0]['source']),store.view('isolation-probe'),config['graph_snapshot'])
        write_json(folder/'isolation.json',{'other_run_memory_candidates':[asdict(c) for c in isolated.candidates if c.memory_ref]})
        inventory=[]
        for record in store.visible(store.view('translation-pilot')):
            inventory.append({'ref':asdict(record.ref),'kind':record.kind,'scope':asdict(record.scope),
                'verification':record.content.get('verification','reviewed'),
                'content':{k:v for k,v in record.content.items() if k in {'source','target','note','mandatory'}},
                'evidence':[asdict(e) for e in record.evidence],
                'dependencies':[asdict(ref) for ref in record.dependencies]})
        write_json(folder/'memory_inventory.json',inventory)
        # A separate read-only connection confirms these are persisted records, not just returned objects.
        disk=sqlite3.connect((folder/'run.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
        expected={(ref['record_id'],ref['version']) for c in cases for ref in
            ([c['outcome']['translation']] if c['outcome'].get('translation') else [])+list(c['outcome'].get('memories',[]))}
        missing=[ref for ref in expected if not disk.execute(
            'SELECT 1 FROM records WHERE id=? AND version=?',ref).fetchone()]
        disk.close()
        write_json(folder/'persistence.json',{'checked_refs':len(expected),'missing_refs':missing,'all_saved':not missing})
        if any(c['outcome']['status']=='review_pending' for c in cases):status='review_pending'
    except BaseException:
        status='interrupted_or_failed'
        raise
    finally:
        calls=[dict(row) for row in store.db.execute('SELECT * FROM model_calls WHERE rowid>? ORDER BY rowid',
                                                    (config['initial_call_rowid'],))]
        (folder/'calls.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in calls))
        store.close()
        write_json(folder/'state.json',{'status':status,'planned':len(inputs),'completed':len(cases)})
        report(folder)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='configs/flare.translation_pilot.json')
    parser.add_argument('--output',required=True)
    parser.add_argument('--prepare',action='store_true',help='Create frozen local inputs; no network calls')
    args=parser.parse_args()
    folder=Path(args.output)
    if args.prepare:prepare(folder,args.config)
    else:run(folder)


if __name__=='__main__':main()
