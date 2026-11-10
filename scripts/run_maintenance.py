"""M6: one change, the translations it actually reached, and the wordings it deliberately left alone.

plan prices a change without asking the model for anything; apply revises only what it reached and
writes the version history; rollback and lock keep a human in charge of wording the agent got wrong.
"""
import argparse
import json
from dataclasses import asdict
from pathlib import Path

from run_agent_workflow import load, services

from localization_agent.maintenance import MaintenanceConfig, MaintenanceService
from localization_agent.memory import MemoryStore
from localization_agent.models.chat_completions import CallBudget, ChatCompletionsModel
from localization_agent.retrieval import GraphStore
from localization_agent.schemas.maintenance import UpdateEvent
from localization_agent.schemas.memory import Evidence, MemoryRef


def event(args, maintenance):
    """An event is either a glossary decision or a record of any kind read from a file."""
    if args.event_json:
        values = json.loads(Path(args.event_json).read_text(encoding='utf-8'))
        if isinstance(values.get('ref'), dict):
            values['ref'] = MemoryRef(**values['ref'])
        if isinstance(values.get('source_change'), dict):
            values['source_change'] = Evidence(**values['source_change'])
        return UpdateEvent(**values)
    if not args.term or '=' not in args.term:
        raise SystemExit('Give --term <record_id>=<new target> or --event-json <file>')
    record_id, target = args.term.split('=', 1)
    current = maintenance.store.get(MemoryRef(record_id, maintenance.store.latest_version(record_id)))
    revised = maintenance.supersede(current.ref, {'target': target}, args.reviewer)
    return UpdateEvent(args.project, current.kind, f'{args.term_reason}', ref=revised.ref,
                       previous={'target': current.content['target']}, current={'target': target})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('plan', 'apply', 'rollback', 'lock', 'unlock'))
    parser.add_argument('--config', required=True, help='Same model, graph and budget file as the M5 run')
    parser.add_argument('--db', required=True, help='Database holding the run being maintained')
    parser.add_argument('--term', help='Glossary decision, for example term:ward=守护')
    parser.add_argument('--term-reason', default='Glossary revision issued by a human reviewer',
                        help='Why the term changed; it is carried into every affected row')
    parser.add_argument('--event-json', help='UpdateEvent file for source, style, summary, graph or rule changes')
    parser.add_argument('--project', help='Defaults to the project named in --config')
    parser.add_argument('--run', help='Run id to maintain; defaults to the configured one')
    parser.add_argument('--snapshot', help='Graph snapshot the re-review retrieves from; defaults to configured')
    parser.add_argument('--reviewer', default='human:glossary', help='Who owns the change being applied')
    parser.add_argument('--discovery', default='dependency_retrieval',
                        choices=('dependency', 'retrieval', 'dependency_retrieval', 'full'))
    parser.add_argument('--max-candidates', type=int, default=200)
    parser.add_argument('--max-cascades', type=int, default=2,
                        help='How deep a revision may follow the rows it retires')
    parser.add_argument('--reuse-logged-calls', action='store_true',
                        help='Replay paid model_calls instead of asking the backend again')
    parser.add_argument('--journal', help='M5 journal folder whose budget.json caps this run as well')
    parser.add_argument('--occurrence', action='append', default=[], help='Row to lock or unlock')
    parser.add_argument('--lock-reviewer', default='human:lead')
    parser.add_argument('--lock-note', default='')
    parser.add_argument('--translation', help='record_id:version to roll back')
    parser.add_argument('--reason', default='Reviewer rejected the newer target')
    parser.add_argument('--output', help='Update report JSON path')
    args = parser.parse_args()

    config = load(args.config)
    args.project = args.project or config['project']
    project = args.project
    run_id = args.run or config['run_id']
    snapshot = args.snapshot or config['graph_snapshot']
    budget = CallBudget(**config.get('budget', {}))
    if args.journal and (Path(args.journal) / 'budget.json').exists():
        saved = json.loads((Path(args.journal) / 'budget.json').read_text(encoding='utf-8'))
        for key in ('calls', 'tokens', 'cost', 'estimated_usage_calls', 'unpriced_calls'):
            setattr(budget, key, saved.get(key) or 0)
    store = MemoryStore(args.db)
    try:
        graph = GraphStore(store)
        chat = ChatCompletionsModel(**config['model'], budget=budget)
        agent, _, retriever = services(config, store, graph, chat)
        maintenance = MaintenanceService(retriever, agent, config=MaintenanceConfig(
            discovery=args.discovery, max_candidates=args.max_candidates, max_cascades=args.max_cascades,
            reviewer=args.reviewer))
        if args.phase == 'lock':
            if not args.occurrence:
                parser.error('lock needs --occurrence')
            for occurrence_id in args.occurrence:
                maintenance.lock(occurrence_id, args.lock_reviewer, project=project, note=args.lock_note)
            print(json.dumps({'status': 'locked', 'occurrences': args.occurrence,
                              'reviewer': args.lock_reviewer}, ensure_ascii=False))
            return
        if args.phase == 'unlock':
            for occurrence_id in args.occurrence:
                maintenance.unlock(occurrence_id, project=project)
            print(json.dumps({'status': 'unlocked', 'occurrences': args.occurrence}, ensure_ascii=False))
            return
        if args.phase == 'rollback':
            if not args.translation or ':' not in args.translation:
                parser.error('rollback needs --translation record_id:version')
            record_id, version = args.translation.rsplit(':', 1)
            restored = maintenance.rollback(MemoryRef(record_id, int(version)), run_id=run_id,
                                            snapshot_id=snapshot, reason=args.reason)
            print(json.dumps({'status': 'rolled_back', 'translation': asdict(restored.ref),
                              'target': restored.content['target']}, ensure_ascii=False))
            return
        value = event(args, maintenance)
        report = maintenance.apply(value, run_id, snapshot, dry_run=args.phase == 'plan',
                                   reuse_calls=args.reuse_logged_calls)
        exported = MaintenanceService.export(report)
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(exported, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        print(json.dumps({'phase': args.phase, 'event': f'{value.kind}: {value.reason}',
                          'candidates': exported['candidates'], 'counts': exported['counts'],
                          'revised': exported['revised'], 'untouched': exported['untouched'],
                          'conflicts': exported['conflicts'], 'model_calls': len(exported['model_calls']),
                          'budget': budget.snapshot(), 'output': args.output}, ensure_ascii=False))
    finally:
        store.close()


if __name__ == '__main__':
    main()
