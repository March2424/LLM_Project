"""Run the M5 translate/check/review/repair/commit loop against a configurable model service.

Phases are separate so a local or paid model is only asked for what is missing:
prepare builds the source graph, run drives the closed loop with a durable journal,
report summarises a journal without touching the model.
"""
import argparse
import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from localization_agent.data.benchmark import import_benchmark
from localization_agent.memory import MemoryConfig, MemoryService, MemoryStore
from localization_agent.models.chat_completions import CallBudget, ChatCompletionsModel
from localization_agent.retrieval import GraphConfig, GraphRetriever, GraphStore
from localization_agent.retrieval.builder import AutoGraphBuilder
from localization_agent.schemas.memory import Evidence
from localization_agent.workflow.agent import AgentConfig, TranslationAgent
from localization_agent.workflow.journal import RunJournal

ROOT = Path(__file__).resolve().parents[1]


def load(config_path):
    return json.loads(Path(config_path).read_text(encoding='utf-8'))


def services(config, store, graph, chat):
    """One place where retrieval, memory and agent limits come from the same file."""
    memory = MemoryService(store, MemoryConfig(**config.get('memory', {})), chat=chat)
    graph_config = GraphConfig(**config['graph_config'])
    retriever = GraphRetriever(graph, memory, graph_config)
    agent = TranslationAgent(retriever, config=AgentConfig(**config.get('agent', {})))
    return agent, memory, retriever


def budget_path(journal):
    return journal / 'budget.json'


def restore_budget(budget, journal):
    saved = json.loads(budget_path(Path(journal)).read_text(encoding='utf-8')) if budget_path(Path(journal)).exists() else {}
    for key in ('calls', 'tokens', 'cost', 'estimated_usage_calls', 'unpriced_calls'):
        setattr(budget, key, saved.get(key) or 0)


def extract(builder, graph, batch, stats, budget, depth=0):
    """One extraction call per batch; a truncated reply is halved rather than discarded."""
    try:
        with graph.atomic():
            result = builder.build(batch)
    except (ValueError, RuntimeError) as error:
        if depth >= 3 or len(batch) < 2:
            raise
        middle = len(batch) // 2
        print(json.dumps({'split': f'{type(error).__name__}: {str(error)[:80]}',
                          'rows': len(batch)}, ensure_ascii=False), flush=True)
        for child in (batch[:middle], batch[middle:]):
            extract(builder, graph, child, stats, budget, depth + 1)
        return
    stats['rows'] += len(batch)
    stats['entities'] += len(result.entities)
    stats['edges'] += len(result.edges)
    stats['rejected'] += len(result.rejected)
    stats['calls'] = budget.calls
    print(json.dumps({'extracted_rows': stats['rows'], 'entities': stats['entities'],
                      'edges': stats['edges'], 'rejected': stats['rejected'],
                      'calls': stats['calls']}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'run', 'report'))
    parser.add_argument('--config', required=True, help='Model, graph, memory and agent JSON')
    parser.add_argument('--db', required=True, help='Run SQLite database')
    parser.add_argument('--journal', required=True, help='Durable workflow state folder')
    parser.add_argument('--csv', nargs='+', help='Source CSVs (prepare); the database remembers them')
    parser.add_argument('--batch-size', type=int, help='Override rows per graph extraction call')
    parser.add_argument('--limit', type=int, help='Stop after this many translated rows')
    parser.add_argument('--reuse-logged-calls', action='store_true',
                        help='Resume by replaying model_calls instead of re-asking the backend')
    parser.add_argument('--report', help='JSON summary path (default: <journal>/report.json)')
    args = parser.parse_args()

    config = load(args.config)
    project, run_id = config['project'], config['run_id']
    snapshot, knowledge = config['graph_snapshot'], config['knowledge_snapshot']
    journal = Path(args.journal)
    journal.mkdir(parents=True, exist_ok=True)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    budget = CallBudget(**config.get('budget', {}))
    restore_budget(budget, journal)
    store = MemoryStore(args.db)
    try:
        graph = GraphStore(store)
        chat = ChatCompletionsModel(**config['model'], budget=budget)
        agent, memory, _ = services(config, store, graph, chat)
        if args.phase == 'prepare':
            if not args.csv:
                parser.error('prepare needs --csv')
            if store.db.execute('SELECT 1 FROM runs LIMIT 1').fetchone():
                parser.error(f'{args.db} already has a run; use a new database')
            options = dict(source_lang=config['source_lang'], target_lang=config['target_lang'],
                           batch_size=args.batch_size or config.get('batch', {}).get('max_rows', 64))
            inputs = import_benchmark(args.csv, project, graph, **options)
            builder = AutoGraphBuilder(graph, memory, reuse_logged_calls=True,
                                       keep_valid_items=config.get('keep_valid_items', False),
                                       catalog_limit=config.get('catalog_limit', 48),
                                       catalog_tokens=config.get('catalog_tokens', 1200))
            stats = {'rows': 0, 'entities': 0, 'edges': 0, 'rejected': 0, 'calls': 0}
            for batch in inputs.batches:
                extract(builder, graph, batch, stats, budget)
            graph.create_snapshot(snapshot, project)
            store.create_snapshot(knowledge, project)
            store.start_run(run_id, project, None, config['source_lang'], config['target_lang'],
                            knowledge, mode='game_resources')
            manifest = {'schema_version': 1, 'project': project, 'run_id': run_id,
                        'inputs': {str(p): sha256(Path(p).read_bytes()).hexdigest() for p in args.csv},
                        'configuration_hash': sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
                        'source_records': len(inputs.occurrences),
                        'skipped_formatting_ids': inputs.skipped_formatting_ids,
                        'occurrence_ids': [o.occurrence_id for o in inputs.occurrences]}
            (journal / 'prepare.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n',
                                                  encoding='utf-8')
            print(json.dumps({'status': 'prepared', 'source_records': len(inputs.occurrences),
                              'budget': budget.snapshot()}, ensure_ascii=False))
        elif args.phase == 'run':
            manifest_path = journal / 'prepare.json'
            if manifest_path.exists():
                occurrence_ids = json.loads(manifest_path.read_text(encoding='utf-8'))['occurrence_ids']
            else:
                occurrence_ids = [row[0] for row in store.db.execute(
                    'SELECT DISTINCT id FROM sources WHERE project=? ORDER BY id', (project,))]
            run_journal = RunJournal(journal, run_id=run_id, config=config, store=store)
            pending = run_journal.pending(occurrence_ids)
            if args.limit:
                pending = pending[:args.limit]
            lookup = {}
            for occurrence_id in occurrence_ids:
                version = store.db.execute('SELECT MAX(version) FROM sources WHERE project=? AND id=?',
                                           (project, occurrence_id)).fetchone()[0]
                lookup[occurrence_id] = store.source(project, Evidence(occurrence_id, version, ''))
            for occurrence_id in pending:
                occurrence = lookup[occurrence_id]
                outcome = agent.run(occurrence, run_id, snapshot, reuse_calls=args.reuse_logged_calls)
                run_journal.record(outcome, budget.snapshot())
                budget_path(journal).write_text(json.dumps(budget.snapshot(), ensure_ascii=False), encoding='utf-8')
                print(json.dumps({'id': occurrence_id, 'status': outcome.status,
                                  'revisions': outcome.revisions, 'calls': budget.calls,
                                  'target': outcome.target[:60]}, ensure_ascii=False), flush=True)
            print(json.dumps({'status': 'complete', 'counts': run_journal.counts(),
                              'budget': budget.snapshot()}, ensure_ascii=False))
        else:
            run_journal = RunJournal(journal, run_id=run_id, config=config)
            summary = {'run_id': run_id, 'counts': run_journal.counts(),
                       'outcomes': run_journal.export(),
                       'prompt_hashes': {p.name: sha256(p.read_bytes()).hexdigest() for p in
                                         (ROOT / 'prompts' / 'workflow').glob('*.txt')}}
            output = Path(args.report) if args.report else journal / 'report.json'
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(summary, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
            print(json.dumps({'status': 'reported', 'path': str(output), 'counts': summary['counts']},
                             ensure_ascii=False))
    finally:
        store.close()


if __name__ == '__main__':
    main()
