"""Autonomous reference-free translation review, bounded repair and evidence reselection."""
from dataclasses import asdict
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from localization_agent.schemas.memory import MemoryRef, Occurrence, ReadView, TranslationCheck, text_hash
from .game import GameSession
from .quality import assess_draft, policy_hash, repair_feedback


class WorkflowState(TypedDict, total=False):
    occurrence: dict
    run_id: str
    graph_snapshot: str
    operation_prefix: str
    max_repairs: int
    max_reselections: int
    repair_count: int
    reselection_count: int
    context_manifest: dict
    context_hash: str
    context_history: list[dict]
    policy_hash: str
    draft: dict
    target: str
    warnings: list[str]
    decision: dict
    reviews: list[dict]
    history: list[dict]
    status: str
    outcome: dict
    error: str


def build_workflow(session: GameSession, checkpointer, *, selector: Callable | None = None):
    """Initial selection is fixed/injected; subsequent selection is decided by the reviewer."""
    def prepare(state):
        occurrence = Occurrence(**state['occurrence'])
        pool = session.retriever.retrieve(occurrence, session.memory.store.view(state['run_id']), state['graph_snapshot'])
        pack = session.retriever.build_context(pool, selector(pool) if selector else None)
        return {'context_manifest': pack.manifest, 'context_hash': pack.context_hash,
                'context_history': [pack.manifest], 'policy_hash': policy_hash(session.memory),
                'status': 'prepared' if pack.status == 'ready' else pack.status,
                'error': ', '.join(pool.conflicts)}

    def draft_result(state, result, *, repairs=None, reselections=None):
        entry = {'draft': asdict(result.draft), 'target': result.target, 'context_hash': state['context_hash'],
                 'warnings': list(result.issues), 'repair_count': state.get('repair_count', 0) if repairs is None else repairs,
                 'reselection_count': state.get('reselection_count', 0) if reselections is None else reselections}
        repeated = any(h['target'] == result.target and h.get('context_hash') == state['context_hash']
                       for h in state.get('history', []))
        return {**entry, 'history': state.get('history', []) + [entry],
                'status': 'stalled' if repeated else 'review_pending',
                'error': 'Repeated translation under the same context; no automatic acceptance' if repeated else ''}

    def generate(state):
        occurrence = Occurrence(**state['occurrence'])
        pack = session.restore_context(occurrence, state['context_manifest'], state['context_hash'])
        result = session.generate_draft(occurrence, pack, operation_id=state['operation_prefix'] + ':initial')
        return draft_result(state, result)

    def review(state):
        if state['policy_hash'] != policy_hash(session.memory):
            raise ValueError('Review policy changed; start a new run instead of reusing an old approval')
        decision = assess_draft(session, MemoryRef(**state['draft']))
        action = decision['action']
        status = {'accept': 'approved', 'repair': 'needs_revision',
                  'reselect': 'needs_context', 'stop': 'unresolved'}[action]
        if action == 'repair' and state.get('repair_count', 0) >= state['max_repairs']:
            status = 'repair_limit'
        if action == 'reselect' and state.get('reselection_count', 0) >= state.get('max_reselections', 1):
            status = 'context_limit'
        return {'decision': decision, 'reviews': state.get('reviews', []) + [decision],
                'status': status, 'error': decision['reason'] if status in {'unresolved', 'repair_limit', 'context_limit'} else ''}

    def repair(state):
        count = state.get('repair_count', 0) + 1
        result = session.repair_draft(MemoryRef(**state['draft']), repair_feedback(state['decision']),
            operation_id=state['operation_prefix'] + f':repair:{count}')
        return draft_result(state, result, repairs=count)

    def reselect(state):
        occurrence = Occurrence(**state['occurrence'])
        old = state['context_manifest']
        session.restore_context(occurrence, old, state['context_hash'])
        pool = session.retriever.retrieve(occurrence, ReadView(**old['view']), old['graph_snapshot'])
        pack = session.retriever.select_context(pool, tuple(state['decision']['selected_ids']))
        count = state.get('reselection_count', 0) + 1
        if pack.context_hash == state['context_hash']:
            return {'status': 'unresolved_context', 'error': 'Requested reselection adds no new context',
                    'reselection_count': count}
        result = session.generate_draft(occurrence, pack, previous=MemoryRef(**state['draft']),
            feedback=repair_feedback(state['decision']), allow_context_change=True,
            operation_id=state['operation_prefix'] + f':reselect:{count}')
        return {**draft_result({**state, 'context_hash': pack.context_hash}, result, reselections=count),
                'context_manifest': pack.manifest, 'context_hash': pack.context_hash,
                'context_history': state.get('context_history', []) + [pack.manifest]}

    def commit(state):
        decision = state['decision']
        if (decision['action'] != 'accept' or decision['draft'] != state['draft']
                or decision['target_hash'] != text_hash(state['target'])
                or decision['context_hash'] != state['context_hash']
                or decision['policy_hash'] != policy_hash(session.memory)):
            raise ValueError('Automatic approval does not bind the current draft, context and policy')
        occurrence = Occurrence(**state['occurrence'])
        pack = session.restore_context(occurrence, state['context_manifest'], state['context_hash'])
        if session.check_warnings(occurrence, pack, state['target']):
            raise ValueError('Technical checks still fail; do not submit')
        check = TranslationCheck(occurrence.occurrence_id, occurrence.source_version,
            text_hash(state['target']), state['context_hash'], decision['reviewer'], True)
        result = session.complete_review(MemoryRef(**state['draft']), check)
        return {'outcome': asdict(result), 'status': result.status}

    def guarded(fn):
        def node(state):
            try:
                return fn(state)
            except Exception as error:
                return {'status': 'failed', 'error': f'{type(error).__name__}: {error}'}
        return node

    graph = StateGraph(WorkflowState)
    for name, fn in [('prepare_context', prepare), ('generate_draft', generate), ('auto_review', review),
                     ('repair_draft', repair), ('reselect_context', reselect), ('commit_and_update', commit)]:
        graph.add_node(name, guarded(fn))
    graph.add_edge(START, 'prepare_context')
    graph.add_conditional_edges('prepare_context', lambda s: 'generate_draft' if s['status'] == 'prepared' else END)
    for name in ('generate_draft', 'repair_draft', 'reselect_context'):
        graph.add_conditional_edges(name, lambda s: 'auto_review' if s['status'] == 'review_pending' else END)
    graph.add_conditional_edges('auto_review', lambda s: {'approved': 'commit_and_update',
        'needs_revision': 'repair_draft', 'needs_context': 'reselect_context'}.get(s['status'], END))
    graph.add_edge('commit_and_update', END)
    return graph.compile(checkpointer=checkpointer)
