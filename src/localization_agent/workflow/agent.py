"""M5: LangGraph orchestration of translate -> program check -> review -> repair -> commit.

Roles are separate calls with separate inputs (README M5); sharing a backend is a config choice.
Program checks never replace the Reviewer, and the Reviewer never rewrites the target.
"""
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from ..memory.store import VersionConflict
from ..models.chat_completions import BudgetExceeded, RetryableModelError
from ..models.protocols import ChatModel, ModelReply
from ..models.structured import parse_json_reply
from ..retrieval.service import GraphRetriever
from ..schemas.graph import CandidatePool, ContextPack
from ..schemas.memory import MemoryRef, Occurrence, TranslationCheck, text_hash
from ..schemas.workflow import ReviewIssue, TranslationOutcome

ISSUE_KINDS = {'accuracy', 'terminology', 'style', 'format', 'context', 'unsupported', 'missing_evidence'}
DEFAULT_PROMPTS = Path(__file__).resolve().parents[3] / 'prompts'
Next = Literal['stop', 'retrieve', 'select', 'translate', 'check', 'review', 'repair', 'commit']


@dataclass(frozen=True)
class AgentConfig:
    max_revisions: int = 2
    max_retries: int = 1
    max_retrievals: int = 1
    max_review_calls: int = 3
    reviewer: str = 'agent:reviewer'
    placeholder_pattern: str = (r'\$?\{[^{}\n]+\}|%%|%(?:\d+\$)?[-+#0 ]*(?:\d+|\*)?(?:\.\d+)?[sdif]|<[^>\n]+>')
    prompt_dir: Path = DEFAULT_PROMPTS

    def __post_init__(self):
        if min(self.max_revisions, self.max_retries, self.max_retrievals, self.max_review_calls) < 0:
            raise ValueError('Agent limits cannot be negative')


class TaskState(TypedDict, total=False):
    """One channel per key, so a node returns only what it changed."""
    occurrence: Occurrence
    run_id: str
    snapshot_id: str
    reuse_calls: bool
    next: Next
    pool: CandidatePool
    pack: ContextPack | None
    target: str
    program_issues: tuple[ReviewIssue, ...]
    issues: tuple[ReviewIssue, ...]
    unresolved: tuple[str, ...]
    status: str
    failure: str | None
    revisions: int
    retrievals: int
    review_calls: int
    calls: tuple[str, ...]
    translation: MemoryRef | None
    memories: tuple[MemoryRef, ...]
    expected_version: int


class TranslationAgent:
    def __init__(self, retriever: GraphRetriever, *, config: AgentConfig | None = None,
                 selector: Callable[[CandidatePool, tuple[str, ...]], tuple[str, ...] | None] | None = None):
        self.retriever = retriever
        self.memory = retriever.memory
        self.store = retriever.memory.store
        self.config = config or AgentConfig()
        self.placeholders = _placeholder_finder(self.config.placeholder_pattern)
        # M4 owns the trained policy; None keeps M3's fixed fill so the loop runs today.
        self.selector = selector

    # Shared model plumbing -------------------------------------------------------------

    @property
    def chat(self) -> ChatModel:
        if self.memory.chat is None:
            raise RuntimeError('The translation loop needs a configured ChatModel')
        return self.memory.chat

    def _ask(self, state: TaskState, task: str, template: str, **values) -> tuple[str, tuple[str, ...]]:
        """Reuse an identical logged call on resume, so a paid call is never replayed blind."""
        prompt = (self.config.prompt_dir / template).read_text(encoding='utf-8').format(**values)
        digest = text_hash(prompt)
        if state.get('reuse_calls'):
            row = self.store.db.execute(
                'SELECT id, response FROM model_calls WHERE task=? AND prompt_hash=? AND error IS NULL'
                ' ORDER BY rowid DESC LIMIT 1', (task, digest)).fetchone()
            if row and row['response'] and (text := json.loads(row['response']).get('text')):
                return text.strip(), state['calls'] + (row['id'],)
        attempts = self.config.max_retries + 1
        for attempt in range(attempts):
            try:
                reply: ModelReply = self.chat(task, prompt)
                break
            except RetryableModelError as error:
                self.store.log_call(task, prompt, None, f'{type(error).__name__}: {error}')
                if attempt + 1 == attempts:
                    raise
        call_id = self.store.log_call(task, prompt, asdict(reply))
        if not reply.text.strip():
            raise ValueError(f'{task} returned an empty response')
        return reply.text.strip(), state['calls'] + (call_id,)

    # Nodes ---------------------------------------------------------------------------

    def load(self, state: TaskState) -> TaskState:
        occurrence = state['occurrence']
        self.store.check_run(occurrence, self.store.run(state['run_id']))
        return TaskState(status='', failure=None, unresolved=(), calls=(), revisions=0, retrievals=0,
                         review_calls=0, issues=(), program_issues=(), target='', translation=None,
                         memories=(), pack=None, next='retrieve')

    def retrieve(self, state: TaskState) -> TaskState:
        pool = self.retriever.retrieve(state['occurrence'], self.store.view(state['run_id']),
                                       state['snapshot_id'])
        # Only a hard conflict stops the run; truncation and unknown scope are notes for the Reviewer.
        if pool.conflicts:
            return TaskState(state, pool=pool, unresolved=(), next='stop', status='conflict',
                             failure='; '.join(pool.conflicts))
        return TaskState(state, pool=pool, unresolved=pool.unresolved, next='select', failure=None)

    def select(self, state: TaskState) -> TaskState:
        hint = tuple(i.detail for i in state['issues'] if i.kind == 'missing_evidence')
        try:
            pack = self.retriever.build_context(state['pool'], self.selector(state['pool'], hint)
                                                if self.selector else None)
        except ValueError as error:
            return TaskState(state, next='stop', status='missing_evidence', failure=str(error))
        if pack.status != 'ready':
            return TaskState(state, pack=pack, next='stop', status='conflict' if pack.status == 'budget_conflict'
                             else 'missing_evidence', failure='; '.join(pack.pool.conflicts) or pack.status)
        if state['pack'] is not None and pack.context_hash == state['pack'].context_hash:
            # Identical input cannot produce new evidence; stop for a human instead of re-paying.
            return TaskState(state, next='stop', status='missing_evidence',
                             failure='Supplementary retrieval produced no new context')
        return TaskState(state, pack=pack, next='translate')

    def translate(self, state: TaskState) -> TaskState:
        occurrence, pack = state['occurrence'], state['pack']
        target, calls = self._ask(state, 'translate_game', 'memory/translate_game.txt', source=occurrence.source,
                                  source_lang=occurrence.source_lang, target_lang=occurrence.target_lang,
                                  contexts=pack.model_context,
                                  source_context=pack.manifest['current_source_context'])
        return TaskState(state, target=target.strip(), calls=calls, next='check')

    def check(self, state: TaskState) -> TaskState:
        """Program-side hard constraints: never a semantic verdict, never a rewrite."""
        occurrence, pack, target = state['occurrence'], state['pack'], state['target']
        issues = []
        if sorted(self.placeholders(occurrence.source)) != sorted(self.placeholders(target)):
            issues.append(ReviewIssue('format', 'Placeholder or tag set differs from the source'))
        for item in pack.selected:
            if item.mandatory and item.category == 'term':
                term = self.store.get(item.memory_ref)
                if term.content['target'] not in target:
                    issues.append(ReviewIssue('terminology', f'Mandatory term absent: '
                                    f'{term.content["source"]} -> {term.content["target"]}', candidate_ids=(item.id,)))
        return TaskState(state, program_issues=tuple(issues), next='review')

    def review(self, state: TaskState) -> TaskState:
        if state['review_calls'] >= self.config.max_review_calls:
            return TaskState(state, next='stop', status='needs_revision', failure='Reviewer call limit reached')
        occurrence, pack = state['occurrence'], state['pack']
        reply, calls = self._ask(state, 'review_agent', 'workflow/review_agent.txt', source=occurrence.source,
                                 source_lang=occurrence.source_lang, target_lang=occurrence.target_lang,
                                 target=state['target'], contexts=pack.model_context,
                                 source_context=pack.manifest['current_source_context'],
                                 warnings='; '.join([i.detail for i in state['program_issues']]
                                                     + list(state['unresolved'])) or 'none')
        try:
            verdict = parse_json_reply(reply)
        except (json.JSONDecodeError, ValueError) as error:
            return TaskState(state, next='stop', status='failed', calls=calls,
                             failure=f'Reviewer output is not parsable JSON: {error}')
        if not isinstance(verdict, dict) or not isinstance(verdict.get('issues'), list) \
                or not isinstance(verdict.get('passed'), bool):
            return TaskState(state, next='stop', status='failed', calls=calls,
                             failure='Reviewer reply lacks a boolean passed and an issues list')
        supplied = {item.id for item in pack.selected}
        issues = list(state['program_issues'])
        for raw in verdict['issues']:
            if not isinstance(raw, dict) or raw.get('kind') not in ISSUE_KINDS or not str(raw.get('detail', '')).strip():
                return TaskState(state, next='stop', status='failed', calls=calls,
                                 failure=f'Reviewer issue is outside the agreed schema: {raw!r}')
            issues.append(ReviewIssue(raw['kind'], str(raw['detail']).strip(),
                                      bool(raw.get('repairable', raw['kind'] != 'missing_evidence')),
                                      tuple(i for i in (raw.get('candidate_ids') or ()) if i in supplied)))
        blocked = tuple(i for i in issues if not i.repairable)
        base = TaskState(state, calls=calls, review_calls=state['review_calls'] + 1,
                        program_issues=(), issues=())
        if blocked:
            if any(i.kind == 'missing_evidence' for i in blocked):
                if state['retrievals'] >= self.config.max_retrievals:
                    return TaskState(base, issues=blocked, next='stop', status='missing_evidence',
                                     failure='Evidence request cannot be satisfied within the retrieval budget')
                return TaskState(base, issues=blocked, retrievals=state['retrievals'] + 1, next='retrieve')
            return TaskState(base, issues=blocked, next='stop', status='needs_revision',
                             failure='; '.join(i.detail for i in blocked))
        if issues:
            if state['revisions'] >= self.config.max_revisions:
                return TaskState(base, issues=tuple(issues), next='stop', status='needs_revision',
                                 failure='Revision budget exhausted')
            return TaskState(base, issues=tuple(issues), next='repair')
        if not verdict['passed']:
            return TaskState(base, next='stop', status='needs_revision',
                             failure='Reviewer reported no issue but did not pass')
        return TaskState(base, next='commit')

    def repair(self, state: TaskState) -> TaskState:
        occurrence, pack = state['occurrence'], state['pack']
        target, calls = self._ask(state, 'repair', 'workflow/repair.txt', source=occurrence.source,
                                  source_lang=occurrence.source_lang, target_lang=occurrence.target_lang,
                                  issues=json.dumps([asdict(i) for i in state['issues']], ensure_ascii=False),
                                  contexts=pack.model_context, target=state['target'],
                                  source_context=pack.manifest['current_source_context'])
        return TaskState(state, target=target.strip(), calls=calls, issues=(), review_calls=0,
                         revisions=state['revisions'] + 1, next='check')

    def commit(self, state: TaskState) -> TaskState:
        occurrence, pack = state['occurrence'], state['pack']
        check = TranslationCheck(occurrence.occurrence_id, occurrence.source_version, text_hash(state['target']),
                                 pack.context_hash, self.config.reviewer, True)
        # M6 passes the version it inspected; a stale revision attempt must fail, not fork.
        return TaskState(state, translation=self.retriever.commit(occurrence, state['target'], check, pack,
                                                                 expected_version=state.get('expected_version', 0)).ref)

    def remember(self, state: TaskState) -> TaskState:
        """Only a verified translation feeds project memory; a failed note keeps the TM."""
        occurrence, view, errors = state['occurrence'], self.store.view(state['run_id']), []
        approved = []
        for kind in ('term', 'summary', 'style'):
            try:
                drafts = (self.memory.extract_terms(state['translation']) if kind == 'term' else
                          self.retriever.update_memory(occurrence, view, state['snapshot_id'], kind=kind))
                approved += [self.store.approve(item.ref, self.config.reviewer).ref for item in drafts]
            except (ValueError, VersionConflict, KeyError, BudgetExceeded, RetryableModelError) as error:
                errors.append(f'{kind}: {type(error).__name__}: {error}')
        return TaskState(state, memories=tuple(approved), failure='; '.join(errors) or None)

    # Graph ---------------------------------------------------------------------------

    def build(self):
        routes = {'stop': END, 'retrieve': 'retrieve', 'select': 'select', 'translate': 'translate',
                  'check': 'check', 'review': 'review', 'repair': 'repair', 'commit': 'commit'}
        builder = StateGraph(TaskState)
        for name, node in (('load', self.load), ('retrieve', self.retrieve), ('select', self.select),
                           ('translate', self.translate), ('check', self.check), ('review', self.review),
                           ('repair', self.repair), ('commit', self.commit), ('remember', self.remember)):
            builder.add_node(name, node)
        builder.add_edge(START, 'load')
        for name in ('load', 'retrieve', 'select', 'translate', 'check'):
            builder.add_conditional_edges(name, lambda state: state['next'], routes)
        builder.add_conditional_edges('review', lambda state: state['next'], routes)
        builder.add_edge('repair', 'check')
        builder.add_edge('commit', 'remember')
        builder.add_edge('remember', END)
        return builder.compile()

    def run(self, occurrence: Occurrence, run_id: str, snapshot_id: str, *,
            reuse_calls: bool = False, expected_version: int = 0) -> TranslationOutcome:
        state = TaskState(occurrence=occurrence, run_id=run_id, snapshot_id=snapshot_id,
                          reuse_calls=reuse_calls, next='stop', calls=(), pack=None, issues=(), unresolved=(),
                          expected_version=expected_version)
        try:
            final = self.build().invoke(state)
        except BudgetExceeded as error:
            return self._outcome(occurrence, 'budget_exhausted', failure=str(error))
        except (RetryableModelError, VersionConflict, ValueError, KeyError, RuntimeError) as error:
            status = 'conflict' if isinstance(error, VersionConflict) else 'failed'
            return self._outcome(occurrence, status, failure=f'{type(error).__name__}: {error}')
        pack = final.get('pack')
        return TranslationOutcome(occurrence.occurrence_id, occurrence.source_version,
                                  final.get('status') or 'committed', target=final.get('target', ''),
                                  translation=final.get('translation'),
                                  context_hash=pack.context_hash if pack else '', issues=final.get('issues', ()),
                                  unresolved=final.get('unresolved', ()), revisions=final.get('revisions', 0),
                                  model_calls=tuple(final.get('calls', ())), memories=final.get('memories', ()),
                                  budget=_budget(self.chat), failure=final.get('failure'))

    def _outcome(self, occurrence: Occurrence, status: str, *, failure: str | None) -> TranslationOutcome:
        return TranslationOutcome(occurrence.occurrence_id, occurrence.source_version, status,
                                  failure=failure, budget=_budget(self.chat))


def _budget(chat: ChatModel) -> dict:
    return chat.budget.snapshot() if getattr(chat, 'budget', None) else {}


def _placeholder_finder(pattern: str) -> Callable[[str], list[str]]:
    expression = re.compile(pattern)

    def find(text: str) -> list[str]:
        # "50% durability" is prose, not a printf space-flag conversion "% d".
        return [m.group() for m in expression.finditer(text)
                if not (m.group().startswith('%') and ' ' in m.group() and m.end() < len(text)
                        and text[m.end()].isascii() and text[m.end()].isalpha())]
    return find
