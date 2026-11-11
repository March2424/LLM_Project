"""Run or inspect an autonomous sequential translation batch; no manual review input."""
import os
import sys
from pathlib import Path

# The editor's "Run Current File" action may use Conda base and omit package context.
# Delegate to the same project launcher before importing any third-party dependencies.
if __name__ == '__main__' and not __package__:
    launcher = Path(__file__).resolve().parents[3] / 'run_workflow.py'
    os.execv(sys.executable, [sys.executable, str(launcher), *sys.argv[1:]])

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import html
import json
import sqlite3
from uuid import uuid4

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver

from localization_agent.data.benchmark import import_benchmark, read_benchmark
from localization_agent.memory import MemoryConfig, MemoryService, MemoryStore
from localization_agent.models.chat_completions import CallBudget, ChatCompletionsModel
from localization_agent.models.token_count import LocalTokenCounter
from localization_agent.retrieval import GraphConfig, GraphRetriever, GraphStore
from .game import GameSession
from .langgraph_game import build_workflow
from .quality import WORKFLOW_VERSION, POLICY_VERSION, policy_hash

ROOT = Path(__file__).resolve().parents[3]


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def initialize(args):
    folder = args.directory.resolve()
    if folder.exists():
        raise ValueError('Output directory already exists; choose a new directory to preserve previous runs')
    tokenizer = LocalTokenCounter(args.tokenizer)
    # Validate source and model configuration before creating the run directory.
    rows, _, _ = read_benchmark([args.csv], args.project)
    model_config = read_json(args.model_config)
    if min(args.max_repairs, args.max_reselections) < 0:
        raise ValueError('Repair and reselection limits must be nonnegative')
    folder.mkdir(parents=True)
    if args.source_db:
        with sqlite3.connect(f'file:{args.source_db.resolve()}?mode=ro', uri=True) as source:
            with sqlite3.connect(folder / 'memory.sqlite') as destination:
                source.backup(destination)
    store = MemoryStore(folder / 'memory.sqlite')
    try:
        graph = GraphStore(store)
        if args.source_db:
            if not args.graph_snapshot:
                raise ValueError('An existing source database needs --graph-snapshot')
            # Select tasks from registered records, never replace their original source metadata.
            selected = []
            for row in rows:
                found = store.db.execute('SELECT payload FROM sources WHERE project=? AND id=? ORDER BY version DESC LIMIT 1',
                                         (args.project, row.occurrence_id)).fetchone()
                if not found:
                    raise ValueError('Task ID is absent from the supplied source database: ' + row.occurrence_id)
                data = json.loads(found['payload'])
                if data['source'] != row.source:
                    raise ValueError('Task CSV differs from registered source: ' + row.occurrence_id)
                selected.append(data)
            graph_snapshot = args.graph_snapshot
        else:
            imported = import_benchmark([args.csv], args.project, graph)
            selected = [asdict(o) for o in imported.occurrences]
            graph_snapshot = 'workflow-source'
            graph.create_snapshot(graph_snapshot, args.project)
        run_id = 'workflow-' + uuid4().hex[:12]
        knowledge = run_id + '-knowledge'
        store.create_snapshot(knowledge, args.project)
        store.start_run(run_id, args.project, None, 'en', 'zh', knowledge, mode='game_resources')
        config = {'run_id': run_id, 'project': args.project, 'graph_snapshot': graph_snapshot,
            'rows': selected, 'max_repairs': args.max_repairs, 'max_reselections': args.max_reselections,
            'workflow_version': WORKFLOW_VERSION, 'review_policy': POLICY_VERSION,
            'review_policy_hash': policy_hash(MemoryService(store)), 'model': model_config,
            'tokenizer': str(args.tokenizer.resolve()), 'tokenizer_identity': tokenizer.name,
            'initial_model_calls': store.db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0],
            'source_mode': 'existing_graph' if args.source_db else 'source_only_lexical',
            'note': 'Automatic model review and bounded evidence reselection enabled; RL disabled. Source-only import has no extracted entity edges.'}
        write_json(folder / 'run.json', config)
    finally:
        store.close()
    print('已准备运行：', folder)
    print('下一步：python -m localization_agent.workflow.cli run --directory "' + str(folder) + '"')


@contextmanager
def runtime(folder, *, execute=False):
    config = read_json(folder / 'run.json')
    if execute and config.get('workflow_version') != WORKFLOW_VERSION:
        raise ValueError('This is a historical manual-review run. Use status to read it, or initialize a new autonomous directory.')
    load_dotenv(ROOT / '.env', override=False)
    # LangGraph executes synchronous nodes on worker threads; this runner is deliberately sequential.
    store = MemoryStore(folder / 'memory.sqlite', check_same_thread=False)
    try:
        options = dict(config['model'])
        backend = options.pop('backend')
        cap = options.pop('max_calls', 30)
        if backend == 'ollama':
            # Historical runs remain readable, but must not trigger local model calls.
            model = None
        elif backend == 'http':
            model = ChatCompletionsModel(**options, budget=CallBudget(max_calls=cap))
        else:
            raise ValueError('backend must be ollama or http')

        def bounded_model(task, prompt):
            if model is None:
                raise RuntimeError('Local model execution is disabled. Create a new run with configs/workflow.deepseek.json')
            calls = store.db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0] - config['initial_model_calls']
            if calls >= cap:
                raise RuntimeError('Persistent run-wide model call limit reached')
            return model(task, prompt)

        memory = MemoryService(store, MemoryConfig(), chat=bounded_model)
        if execute and config.get('review_policy_hash') != policy_hash(memory):
            raise ValueError('Review policy changed since initialization; create a new run directory')
        tokenizer = LocalTokenCounter(config['tokenizer'])
        if tokenizer.name != config['tokenizer_identity']:
            raise ValueError('Budget tokenizer changed since run initialization')
        retriever = GraphRetriever(GraphStore(store), memory, GraphConfig(), token_counter=tokenizer)
        session = GameSession(retriever)
        with SqliteSaver.from_conn_string(str(folder / 'checkpoints.sqlite')) as saver:
            app = build_workflow(session, saver)
            yield config, store, app
    finally:
        store.close()


def thread_config(config, row):
    return {'configurable': {'thread_id': config['run_id'] + ':' + row['occurrence_id']},
            'max_concurrency': 1, 'recursion_limit': 100}


def export(folder, config, store, app):
    states = []
    for row in config['rows']:
        snapshot = app.get_state(thread_config(config, row))
        states.append(dict(snapshot.values) or {'occurrence': row, 'status': 'not_started'})
    calls = [dict(row) for row in store.db.execute('SELECT id,task,response,error FROM model_calls ORDER BY rowid')]
    calls = calls[config['initial_model_calls']:]
    responses = [json.loads(c['response']) for c in calls if c['response']]
    report = {'run_id': config['run_id'], 'source_mode': config['source_mode'], 'model': config['model'],
              'workflow_version': config.get('workflow_version', 'legacy-external-review'),
              'review_policy': config.get('review_policy', 'historical external review'),
              'rl_enabled': False, 'states': states, 'model_calls_this_run': len(calls), 'calls': calls,
              'usage': {'input_tokens': sum(c.get('input_tokens') or 0 for c in responses),
                        'output_tokens': sum(c.get('output_tokens') or 0 for c in responses),
                        'configured_cost_usd': sum(c.get('cost') or 0 for c in responses),
                        'usage_missing_calls': sum(c.get('input_tokens') is None for c in responses)}}
    write_json(folder / 'report.json', report)
    with (folder / 'translations.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        import csv
        writer = csv.writer(handle)
        writer.writerow(['id', 'source_text', 'target_text', 'status', 'reviewer'])
        for state in states:
            writer.writerow([state['occurrence']['occurrence_id'], state['occurrence']['source'], state.get('target', ''),
                             state['status'], state.get('decision', {}).get('reviewer', '')])
    cards = []
    e = lambda value: html.escape(str(value))
    for state in states:
        history = ''.join('<li><b>草稿 %s</b><p>%s</p><small>%s</small></li>' %
            (i + 1, e(h['target']), e('; '.join(h['warnings']))) for i, h in enumerate(state.get('history', [])))
        reviews = ''.join('<li><b>%s</b> — %s<pre>%s</pre></li>' %
            (e(r['action']), e(r.get('reason', r.get('feedback', ''))),
             e(json.dumps(r.get('issues', []) + r.get('technical_issues', []), ensure_ascii=False, indent=2)))
            for r in state.get('reviews', []))
        cards.append(f'<article><span class="status">{e(state["status"])}</span><h2>{e(state["occurrence"]["source"])}</h2>'
            f'<p class="target">{e(state.get("target", "尚未生成"))}</p><small>{e(state["occurrence"]["occurrence_id"])}</small>'
            f'<h3>草稿与修复</h3><ol>{history}</ol><h3>审查决定</h3><ul>{reviews}</ul>'
            f'<p>{e(state.get("error", ""))}</p><details><summary>实际上下文与来源</summary>'
            f'<pre>{e(json.dumps(state.get("context_manifest", {}), ensure_ascii=False, indent=2))}</pre></details></article>')
    automatic = config.get('workflow_version') == WORKFLOW_VERSION
    title = '游戏翻译 · 自主审查闭环' if automatic else '游戏翻译 · 历史外部审阅记录'
    description = ('检索与固定初选 → 翻译 → 自动审查 → 按需修复或重选资料 → 复审 → 提交记忆。'
                   'RL 未启用；模型审查不等于人工验收。' if automatic else '历史记录仅供查看，不会按新策略自动续跑或重标为自主审查。')
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{font:16px/1.7 system-ui,sans-serif;background:#f5f4ef;color:#20362d;max-width:1000px;margin:48px auto;padding:0 24px}}
h1{{font-size:34px;line-height:1.3}}article{{background:white;border-top:3px solid #49785e;padding:28px;margin:28px 0}}
.status{{float:right;color:#446954;font-size:13px}}.target{{font-size:23px}}small{{color:#69746c}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}
h3{{font-size:15px;margin-bottom:8px}}li p{{margin:4px 0}}</style>
<header><small>M5 / LANGGRAPH WORKFLOW</small><h1>{title}</h1><p>{description}</p></header>'''
    page += '<p>模型：' + e(config['model']['model']) + ' · 源侧模式：' + e(config['source_mode']) + ' · 本次调用：' + str(len(calls)) + '</p>'
    (folder / 'report.html').write_text(page + ''.join(cards) + '</html>', encoding='utf-8')
    return report


def advance(config, app, folder, store):
    for row in config['rows']:
        cfg = thread_config(config, row)
        snapshot = app.get_state(cfg)
        try:
            if not snapshot.values:
                print('自动处理：', row['occurrence_id'], flush=True)
                app.invoke({'occurrence': row, 'run_id': config['run_id'], 'graph_snapshot': config['graph_snapshot'],
                    'operation_prefix': cfg['configurable']['thread_id'], 'max_repairs': config['max_repairs'],
                    'max_reselections': config['max_reselections'], 'repair_count': 0, 'reselection_count': 0,
                    'history': [], 'reviews': [], 'status': 'started'}, cfg)
            elif snapshot.next:
                app.invoke(None, cfg)
        finally:
            # Keep completed/failing rows visible even when the caller interrupts the batch.
            export(folder, config, store, app)
        print('结果：', app.get_state(cfg).values['status'], flush=True)


def main():
    parser = argparse.ArgumentParser(description='LangGraph 自主翻译：自动审查、有限修复／重选、持久化恢复')
    parser.add_argument('command', choices=['init', 'run', 'status'])
    parser.add_argument('--directory', type=Path, default=ROOT / 'outputs/deepseek-autonomous-v2')
    parser.add_argument('--csv', type=Path, default=ROOT / 'examples/workflow_flare.csv')
    parser.add_argument('--project', default='flare-workflow-demo')
    parser.add_argument('--model-config', type=Path, default=ROOT / 'configs/workflow.deepseek.json')
    parser.add_argument('--tokenizer', type=Path, default=ROOT / 'artifacts/workflow-tokenizer/tokenizer.json')
    parser.add_argument('--source-db', type=Path)
    parser.add_argument('--graph-snapshot')
    parser.add_argument('--max-repairs', type=int, default=2)
    parser.add_argument('--max-reselections', type=int, default=1)
    args = parser.parse_args()
    if args.command == 'init':
        initialize(args)
        return
    folder = args.directory.resolve()
    with runtime(folder, execute=args.command == 'run') as (config, store, app):
        if args.command == 'run':
            advance(config, app, folder, store)
        report = export(folder, config, store, app)
        print('状态：', ', '.join(s['status'] for s in report['states']))
        print('报告：', folder / 'report.html')


if __name__ == '__main__':
    main()
