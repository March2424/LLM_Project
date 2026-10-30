"""Build an automatic source graph, optionally translate using injected model factories."""
import argparse
import importlib
import json
from dataclasses import asdict
from pathlib import Path
from localization_agent.data.benchmark import import_benchmark
from localization_agent.memory import MemoryStore, MemoryService
from localization_agent.retrieval import GraphStore, GraphRetriever
from localization_agent.retrieval.builder import AutoGraphBuilder
from localization_agent.workflow.game import GameSession


def factory(value):
    module, name = value.split(':', 1)
    return getattr(importlib.import_module(module), name)()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', nargs='+')
    parser.add_argument('--project', required=True)
    parser.add_argument('--db', required=True, help='New database; existing files are protected')
    parser.add_argument('--output', required=True, help='New JSON result path')
    parser.add_argument('--model-factory', required=True, help='module:function returning a ChatModel adapter')
    parser.add_argument('--reviewer-factory', required=True, help='module:function returning a review ChatModel adapter')
    parser.add_argument('--source-lang', default='en')
    parser.add_argument('--target-lang', default='zh')
    parser.add_argument('--batch-size', type=int, default=12)
    parser.add_argument('--catalog-limit', type=int, default=80)
    parser.add_argument('--translate', action='store_true', help='Also generate, review, commit and update memories')
    args = parser.parse_args()
    if Path(args.db).exists() or Path(args.output).exists():
        parser.error('Database and output must be new paths')
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(args.db)
    try:
        graph = GraphStore(store)
        inputs = import_benchmark(args.csv, args.project, graph, source_lang=args.source_lang,
                                  target_lang=args.target_lang, batch_size=args.batch_size)
        generator, reviewer = factory(args.model_factory), factory(args.reviewer_factory)
        # Route independent review tasks to the designated review adapter.
        review_tasks = {'review_graph', 'review_style', 'review_memory', 'check_game_translation'}
        def route(task, prompt):
            return (reviewer if task in review_tasks else generator)(task, prompt)
        memory = MemoryService(store, chat=route)
        builder = AutoGraphBuilder(graph, memory, reviewer=MemoryService(store, chat=reviewer),
                                   catalog_limit=args.catalog_limit)
        builds = [builder.build(batch) for batch in inputs.batches]
        graph.create_snapshot('graph', args.project)
        outcomes = []
        if args.translate:
            store.create_snapshot('knowledge', args.project)
            store.start_run('run', args.project, None, args.source_lang, args.target_lang, 'knowledge', mode='game_resources')
            session = GameSession(GraphRetriever(graph, memory))
            for occurrence in inputs.occurrences:
                outcomes.append({'id': occurrence.occurrence_id,
                    **asdict(session.translate_one(occurrence, 'run', 'graph'))})
        Path(args.output).write_text(json.dumps({'builds': [asdict(r) for r in builds], 'translations': outcomes},
                                               ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'batches': len(builds), 'source_records': len(inputs.occurrences),
                          'translation_statuses': [r['status'] for r in outcomes]}, ensure_ascii=False))
    finally:
        store.close()


if __name__ == '__main__':
    main()
