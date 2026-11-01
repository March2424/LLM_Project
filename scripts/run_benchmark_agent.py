"""Plan or run source graph extraction using configurable model services and budgets."""
import argparse
import importlib
import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from localization_agent.data.benchmark import import_benchmark, read_benchmark
from localization_agent.memory import MemoryStore, MemoryService
from localization_agent.models.chat_completions import ChatCompletionsModel, CallBudget
from localization_agent.models.token_count import LocalTokenCounter
from localization_agent.retrieval import GraphStore, GraphRetriever
from localization_agent.retrieval.builder import AutoGraphBuilder
from localization_agent.schemas.extraction import SCHEMA_VERSION
from localization_agent.workflow.game import GameSession


def factory(value):
    module, name = value.split(':', 1)
    return getattr(importlib.import_module(module), name)()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', nargs='+')
    parser.add_argument('--project', required=True)
    parser.add_argument('--db', help='New database; existing files are protected')
    parser.add_argument('--output', help='New JSON result path; updated after each batch')
    parser.add_argument('--config', help='Model, shared budget and batching JSON')
    parser.add_argument('--dry-run', action='store_true', help='Plan locally without model calls or a database')
    parser.add_argument('--model-factory', help='Alternative module:function returning ChatModel')
    parser.add_argument('--source-lang', default='en')
    parser.add_argument('--target-lang', default='zh')
    parser.add_argument('--batch-size', type=int, help='Override maximum rows per batch')
    parser.add_argument('--catalog-limit', type=int)
    parser.add_argument('--max-batches', type=int, help='Limit pilot batches; 0 means all')
    parser.add_argument('--resume', action='store_true', help='Continue existing extraction checkpoints')
    parser.add_argument('--split-failures', action='store_true', help='Split invalid/truncated batches; shared budget still applies')
    parser.add_argument('--translate', action='store_true', help='Also generate translation drafts for Agent Reviewer; uses the shared budget')
    parser.add_argument('--tokenizer', help='Frozen local tokenizer.json for context budgeting (required for --translate)')
    args = parser.parse_args()
    if args.translate and not args.tokenizer:
        parser.error('--translate requires --tokenizer for context budgeting')
    counter = LocalTokenCounter(args.tokenizer) if args.translate else None
    config = json.loads(Path(args.config).read_text()) if args.config else {}
    batch_config = config.get('batch', {})
    options = dict(source_lang=args.source_lang, target_lang=args.target_lang,
        batch_size=args.batch_size if args.batch_size is not None else batch_config.get('max_rows', 64),
        batch_input_tokens=batch_config.get('input_tokens', 5000),
        batch_output_tokens=batch_config.get('output_tokens', 8192))
    maximum = args.max_batches if args.max_batches is not None else config.get('max_batches', 5)
    if maximum < 0:
        parser.error('max-batches must be zero or positive')
    catalog_limit = args.catalog_limit if args.catalog_limit is not None else config.get('catalog_limit', 48)
    if args.dry_run:
        rows, batches, skipped = read_benchmark(args.csv, args.project, **options)
        selected = batches[:maximum] if maximum else batches
        summary = {'schema_version': SCHEMA_VERSION, 'source_records': len(rows),
            'skipped_formatting_ids': skipped, 'total_batches': len(batches),
            'planned_batches': len(selected), 'planned_rows': sum(map(len, selected)),
            'batch_rows_min': min(map(len, batches), default=0),
            'batch_rows_max': max(map(len, batches), default=0),
            'batch_rows_mean': round(len(rows) / len(batches), 1) if batches else 0,
            'graph_calls_upper_bound': len(selected),
            'token_estimate_note': 'UTF-8 bytes / 3 heuristic; catalog and prompt add input tokens',
            'budget': config.get('budget'), 'model_calls': 0}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    if not args.db or not args.output:
        parser.error('--db and --output required for execution')
    if args.config and args.model_factory:
        parser.error('Use --config or --model-factory, not both modes')
    if not args.config and not args.model_factory:
        parser.error('Provide --config or --model-factory')
    if args.resume:
        if not (Path(args.db).exists() and Path(args.output).exists()):
            parser.error('Resume requires both existing database and output')
        if args.translate:
            parser.error('Resume supports extraction only')
    elif Path(args.db).exists() or Path(args.output).exists():
        parser.error('Database and output must be new paths')
    budget = None
    if args.config:
        budget = CallBudget(**config.get('budget', {}))
        generator = ChatCompletionsModel(**config['model'], budget=budget)
    else:
        generator = factory(args.model_factory)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    prompts = Path(__file__).resolve().parents[1] / 'prompts' / 'memory'
    state = {'status': 'running', 'schema_version': SCHEMA_VERSION,
        'inputs': {str(p): sha256(Path(p).read_bytes()).hexdigest() for p in args.csv},
        'prompt_hashes': {name: sha256((prompts / name).read_bytes()).hexdigest()
                          for name in ('discover_graph.txt',)},
        'configuration': config, 'builds': [], 'translations': []}

    signature = dict(project=args.project, schema=SCHEMA_VERSION, inputs=state['inputs'],
        prompts=state['prompt_hashes'], options=options, catalog_limit=catalog_limit,
        catalog_tokens=config.get('catalog_tokens', 1200), model=config.get('model'),
        factory=args.model_factory)
    state['run_signature'] = signature
    if args.resume:
        previous = json.loads(Path(args.output).read_text())
        if previous.get('run_signature') != signature:
            parser.error('Inputs, prompt, schema, model or batch configuration changed; use new output paths')
        state = previous
        state['status'] = 'running'
        state.pop('error', None)
        state['configuration'] = config
        if budget and previous.get('budget_usage'):
            for key in ('calls', 'tokens', 'cost', 'estimated_usage_calls', 'unpriced_calls'):
                setattr(budget, key, previous['budget_usage'].get(key) or 0)

    def save():
        state['budget_usage'] = asdict(budget) if budget else None
        if budget and budget.unpriced_calls:
            state['budget_usage']['cost'] = None
        output = Path(args.output)
        temporary = output.with_suffix(output.suffix + '.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(output)

    if budget:
        budget.on_change = save
    store = MemoryStore(args.db)
    try:
        graph = GraphStore(store)
        store.db.execute('CREATE TABLE IF NOT EXISTS extraction_batches (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        store.db.commit()
        inputs = import_benchmark(args.csv, args.project, graph, **options)
        memory = MemoryService(store, chat=generator)
        builder = AutoGraphBuilder(graph, memory, reuse_logged_calls=True,
            keep_valid_items=config.get('keep_valid_items', False),
            catalog_limit=catalog_limit, catalog_tokens=config.get('catalog_tokens', 1200))
        batches = inputs.batches[:maximum] if maximum else inputs.batches
        state.update(total_batches=len(inputs.batches), planned_batches=len(batches),
            source_records=len(inputs.occurrences), skipped_formatting_ids=inputs.skipped_formatting_ids)
        from localization_agent.schemas.graph import GraphRef
        for index, batch in enumerate(batches):
            key = f'{index:06d}'
            item = dict(status='pending', refs=[asdict(ref) for ref in batch])
            store.db.execute('INSERT OR IGNORE INTO extraction_batches VALUES (?,?)', (key,json.dumps(item)))
        store.db.commit()

        state['validation_policy'] = 'keep_valid_items' if config.get('keep_valid_items') else 'strict_batch'

        def refresh():
            items = [json.loads(row['payload']) for row in store.db.execute('SELECT payload FROM extraction_batches ORDER BY id')]
            complete = [item for item in items if item['status'] == 'complete']
            state['builds'] = [item['result'] for item in complete]
            state['rejected_items'] = sum(len(item['result'].get('rejected', [])) for item in complete)
            state['completed_rows'] = sum(len(item['refs']) for item in complete)
            state['remaining_batches'] = sum(item['status'] == 'pending' for item in items)
            state['split_batches'] = sum(item['status'] == 'split' for item in items)
            save()
        refresh()
        while True:
            pending = next(((row['id'],json.loads(row['payload'])) for row in
                store.db.execute('SELECT * FROM extraction_batches ORDER BY id')
                if json.loads(row['payload'])['status'] == 'pending'), None)
            if pending is None:
                break
            key, item = pending
            state['active_batch'] = key
            save()
            batch = tuple(GraphRef(**ref) for ref in item['refs'])
            try:
                with graph.atomic():
                    result = builder.build(batch)
                    item.update(status='complete', result=asdict(result))
                    store.db.execute('UPDATE extraction_batches SET payload=? WHERE id=?', (json.dumps(item),key))
            except (ValueError, RuntimeError) as error:
                can_split = isinstance(error, ValueError) or 'output truncated' in str(error)
                if not (args.split_failures and can_split and len(batch) > 1):
                    raise
                item.update(status='split', error=f'{type(error).__name__}: {error}')
                middle = len(batch)//2
                with store.db:
                    store.db.execute('UPDATE extraction_batches SET payload=? WHERE id=?', (json.dumps(item),key))
                    for child, members in enumerate((item['refs'][:middle],item['refs'][middle:])):
                        store.db.execute('INSERT INTO extraction_batches VALUES (?,?)',
                            (key+'/'+str(child),json.dumps(dict(status='pending',refs=members))))
            refresh()
            print(json.dumps({'completed_rows':state['completed_rows'], 'remaining_batches':state['remaining_batches'],
                              'calls': budget.calls if budget else None},ensure_ascii=False), flush=True)
        if not store.db.execute('SELECT 1 FROM graph_snapshots WHERE id=?', ('graph',)).fetchone():
            graph.create_snapshot('graph', args.project)
        if args.translate:
            store.create_snapshot('knowledge', args.project)
            store.start_run('run', args.project, None, args.source_lang, args.target_lang, 'knowledge', mode='game_resources')
            session = GameSession(GraphRetriever(graph, memory, token_counter=counter))
            count = sum(map(len, batches))
            for occurrence in inputs.occurrences[:count]:
                state['translations'].append({'id': occurrence.occurrence_id,
                    **asdict(session.translate_one(occurrence, 'run', 'graph'))})
                save()
        state['status'] = 'complete' if len(batches) == len(inputs.batches) else 'pilot_complete'
        refresh()
        print(json.dumps({'status': state['status'], 'batches': len(state['builds']),
                          'source_records': len(inputs.occurrences),
                          'budget_usage': state['budget_usage']}, ensure_ascii=False))
    except BaseException as error:
        state.update(status='failed', error=f'{type(error).__name__}: {error}')
        save()
        raise
    finally:
        store.close()


if __name__ == '__main__':
    main()
