"""Source-only retrieval diagnostics on a disposable SQLite backup. No LLM/reference access."""
import argparse
import json
import sqlite3
import statistics
import time
from dataclasses import asdict
from pathlib import Path

from localization_agent.memory import MemoryStore, MemoryService
from localization_agent.models.token_count import LocalTokenCounter
from localization_agent.retrieval import GraphStore, GraphRetriever, GraphConfig


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--snapshot',default='graph')
    parser.add_argument('--tokenizer',required=True)
    parser.add_argument('--config',default='configs/graph.json')
    parser.add_argument('--cases',help='Optional source-only expected-evidence JSON')
    parser.add_argument('--sample',type=int,default=40)
    parser.add_argument('--output',required=True,help='New directory; never overwrites an earlier run')
    args=parser.parse_args()
    output=Path(args.output)
    output.mkdir(parents=True,exist_ok=False)
    counter=LocalTokenCounter(args.tokenizer)
    config=GraphConfig.from_file(args.config)
    store=MemoryStore()
    try:
        original=sqlite3.connect(Path(args.db).resolve().as_uri()+'?mode=ro',uri=True)
        original.backup(store.db)
        original.close()
        store.create_snapshot('retrieval-diagnostic-knowledge',args.project)
        store.start_run('retrieval-diagnostic-run',args.project,None,'en','zh',
                        'retrieval-diagnostic-knowledge',mode='game_resources')
        retrieval=GraphRetriever(GraphStore(store),MemoryService(store),config,token_counter=counter)
        begin=time.perf_counter()
        index=retrieval.game_index(args.snapshot)
        cold=time.perf_counter()-begin
        cases=json.loads(Path(args.cases).read_text()) if args.cases else []
        used={c['source_id'] for c in cases}
        ids=sorted(index.occurrences)
        for i in range(min(args.sample,len(ids))):
            key=ids[i*len(ids)//min(args.sample,len(ids))]
            if key not in used:cases.append({'id':'sample-'+str(i+1),'source_id':key})
        results=[]
        for case in cases:
            source=index.sources[case['source_id']]
            begin=time.perf_counter()
            pool=retrieval.retrieve(source,store.view('retrieval-diagnostic-run'),args.snapshot)
            observation=retrieval.selector_observation(pool)
            pack=retrieval.build_context(pool)
            elapsed=time.perf_counter()-begin
            sources={c.id for c in pool.candidates if c.category=='source'}
            missing=[key for key in case.get('expected_sources',[]) if 'source:'+key+'@1' not in sources]
            if case.get('expect_lexical') and not any('LEXICAL' in c.relations for c in pool.candidates):
                missing.append('LEXICAL')
            checks={'expected_evidence':not missing,'unique_candidates':len({c.id for c in pool.candidates})==len(pool.candidates),
                'pool_limit':sum(not c.mandatory for c in pool.candidates)<=config.max_candidates,
                'selected_limit':sum(not c.mandatory for c in pack.selected)<=config.max_selected,
                'token_budget':pack.soft_tokens<=config.max_soft_tokens,
                'exact_input':pack.model_context==pack.manifest['model_context'] and counter(pack.model_context)==pack.tokens,
                'ready':pack.status=='ready'}
            results.append({'case':case,'source':asdict(source),'seconds':elapsed,'checks':checks,
                'missing':missing,'oversized_candidates':[c['id'] for c in observation['candidates']
                    if c['standalone_tokens']>config.max_soft_tokens],
                'expected_not_selected':[key for key in case.get('expected_sources',[])
                    if 'source:'+key+'@1' not in {c.id for c in pack.selected}],
                'observation':observation,'pool':asdict(pool),'pack':asdict(pack)})
        summary={'project':args.project,'source_only':True,'paid_calls':0,'config':asdict(config),
            'snapshot':args.snapshot,'db':args.db,'tokenizer':counter.name,
            'snapshot_refs':len(index.snapshot.refs),'valid_refs':len(index.objects),'source_count':len(index.occurrences),
            'cold_index_seconds':cold,'cases':len(results),
            'gold_cases':sum('expected_sources' in c for c in cases),
            'failed_cases':[r['case']['id'] for r in results if not all(r['checks'].values())],
            'median_query_seconds':statistics.median(r['seconds'] for r in results),
            'max_query_seconds':max(r['seconds'] for r in results),
            'max_soft_tokens':max(r['pack']['soft_tokens'] for r in results),
            'oversized_candidates':sum(len(r['oversized_candidates']) for r in results),
            'gold_cases_with_expected_not_selected':[r['case']['id'] for r in results if r['expected_not_selected']],
            'empty_pools':sum(not r['pool']['candidates'] for r in results)}
        (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        (output/'cases.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in results))
        lines=['# 源侧召回诊断','', '未调用翻译模型；不读取参考译文。检查通过不代表语义精度或翻译质量通过。','',
            '```json',json.dumps(summary,ensure_ascii=False,indent=2),'```','',
            '| 样本 | 原文 | 候选 / 选中 | 软 token | 缺少的期望证据 |','| --- | --- | --- | --- | --- |']
        for r in results:
            text=r['source']['source'].replace('|','/').replace('\n',' ')[:90]
            lines.append(f"| {r['case']['id']} | {text} | {len(r['pool']['candidates'])} / {len(r['pack']['selected'])} | {r['pack']['soft_tokens']} | {', '.join(r['missing']) or '—'} |")
        (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
        print(json.dumps(summary,ensure_ascii=False,indent=2))
    finally:
        store.close()


if __name__=='__main__':
    main()
