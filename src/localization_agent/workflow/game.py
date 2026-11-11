"""Generate once; the Agent Reviewer owns semantic approval and the subsequent commit."""
from dataclasses import asdict, dataclass
import json
import re

from localization_agent.schemas.memory import Evidence, MemoryRef, Occurrence, ReadView, TranslationCheck, text_hash
from localization_agent.retrieval.service import GraphRetriever
from localization_agent.schemas.graph import ContextPack
from .quality import format_checks


@dataclass(frozen=True)
class GameOutcome:
    status: str
    target: str
    translation: MemoryRef | None = None
    memories: tuple[MemoryRef, ...] = ()
    issues: tuple[str, ...] = ()
    draft: MemoryRef | None = None


class GameSession:
    def __init__(self, retriever: GraphRetriever, *, require_reviewer: bool = True,
                 placeholder_pattern: str = r'\$?\{[^{}\n]+\}|%%|%(?:\d+\$)?[-+#0 ]*(?:\d+|\*)?(?:\.\d+)?[sdif]|<[^>\n]+>'):
        self.retriever = retriever
        self.memory = retriever.memory
        self.placeholder_pattern = re.compile(placeholder_pattern)
        self.require_reviewer=require_reviewer
        with self.memory.store.db:
            self.memory.store.db.execute('''CREATE TABLE IF NOT EXISTS game_review_receipts (
                draft_id TEXT, draft_version INTEGER, check_json TEXT NOT NULL,
                outcome_json TEXT NOT NULL, PRIMARY KEY(draft_id, draft_version))''')

    def prepare_context(self, occurrence: Occurrence, run_id: str, snapshot_id: str,
                        selected_ids: tuple[str, ...] | None = None) -> ContextPack:
        store = self.memory.store
        if store.run(run_id)['mode'] != 'game_resources':
            raise ValueError('GameSession requires game_resources mode')
        return self.retriever.build_context(
            self.retriever.retrieve(occurrence, store.view(run_id), snapshot_id), selected_ids)

    def restore_context(self, occurrence: Occurrence, manifest: dict, context_hash: str) -> ContextPack:
        """Rebuild the exact frozen selection, never silently refresh its read view."""
        pool = self.retriever.retrieve(occurrence, ReadView(**manifest['view']), manifest['graph_snapshot'])
        pack = self.retriever.build_context(pool, tuple(item['id'] for item in manifest['items']))
        if pack.status != 'ready' or pack.context_hash != context_hash:
            raise ValueError('Context changed; prepare and review a new task')
        return pack

    def generate_draft(self, occurrence: Occurrence, pack: ContextPack, *,
                       previous: MemoryRef | None = None, feedback: str = '',
                       operation_id: str | None = None, allow_context_change: bool = False) -> GameOutcome:
        """Generate against one pinned pack. A completed operation reuses its saved draft."""
        if pack.status != 'ready':
            return GameOutcome(pack.status, '', issues=pack.pool.conflicts)
        store = self.memory.store
        self.restore_context(occurrence, pack.manifest, pack.context_hash)
        fingerprint = text_hash(json.dumps([asdict(occurrence), pack.context_hash,
            asdict(previous) if previous else None, feedback], ensure_ascii=False, sort_keys=True))
        record_id = 'workflow-draft:' + text_hash(operation_id) if operation_id else None
        if record_id and store.latest_version(record_id):
            cached = store.get(MemoryRef(record_id, store.latest_version(record_id)))
            if cached.content.get('operation_fingerprint') != fingerprint or not store.is_valid(cached.ref):
                raise ValueError('Draft operation ID was reused for a different or stale input')
            return GameOutcome('review_pending', cached.content['target'],
                               issues=tuple(cached.content['warnings']), draft=cached.ref)
        values = dict(source=occurrence.source, source_lang=occurrence.source_lang,
                      target_lang=occurrence.target_lang, contexts=pack.model_context,
                      source_context=pack.manifest['current_source_context'])
        if previous:
            old = store.get(previous)
            if (old.run_id != pack.pool.view.run_id or old.evidence[0].occurrence_id != occurrence.occurrence_id
                    or old.evidence[0].source_version != occurrence.source_version
                    or (not allow_context_change and old.content['context_hash'] != pack.context_hash)):
                raise ValueError('Repair must use the same run and context as the previous draft')
            values.update(previous=old.content['target'], feedback=feedback)
        task, template = ('repair_game', 'repair_game.txt') if previous else ('translate_game', 'translate_game.txt')
        target, call = self.memory._call(task, template, **values)
        warnings = self.check_warnings(occurrence, pack, target)
        draft = store.draft('translation', store.game_scope(occurrence),
            {'source': occurrence.source, 'target': target,
             'metadata': {**occurrence.metadata, **pack.pool.resolved_metadata},
             'context_manifest': pack.manifest, 'context_hash': pack.context_hash,
             'model_call': call, 'warnings': warnings, 'operation_fingerprint': fingerprint,
             'previous_draft': asdict(previous) if previous else None, 'repair_feedback': feedback,
             'context_changed': bool(previous and allow_context_change)},
            tuple(dict.fromkeys((Evidence(occurrence.occurrence_id, occurrence.source_version, occurrence.source),) +
                                tuple(Evidence(**e) for e in pack.manifest['sources']))),
            dependencies=tuple(c.memory_ref for c in pack.selected if c.memory_ref),
            run_id=pack.pool.view.run_id, position=occurrence.position, record_id=record_id)
        return GameOutcome('review_pending', target, issues=tuple(warnings), draft=draft.ref)

    def check_warnings(self, occurrence: Occurrence, pack: ContextPack, target: str) -> list[str]:
        warnings = format_checks(occurrence.source, target, occurrence.metadata, self.placeholder_pattern)
        for item in pack.selected:
            if item.mandatory and item.category == 'term':
                term = self.memory.store.get(item.memory_ref)
                if term.content['target'] not in target:
                    warnings.append('Mandatory term missing: ' + term.content['source'])
        return warnings

    def repair_draft(self, draft_ref: MemoryRef, feedback: str, *, operation_id: str | None = None) -> GameOutcome:
        if not feedback.strip():
            raise ValueError('Repair requires specific review feedback')
        draft = self.memory.store.get(draft_ref)
        occurrence = self.memory.store.source(draft.scope.project_id, draft.evidence[0])
        pack = self.restore_context(occurrence, draft.content['context_manifest'], draft.content['context_hash'])
        return self.generate_draft(occurrence, pack, previous=draft_ref, feedback=feedback, operation_id=operation_id)

    def translate_one(self, occurrence: Occurrence, run_id: str, snapshot_id: str, *,
                      selected_ids: tuple[str, ...] | None = None) -> GameOutcome:
        """Persist a draft and warnings; do not call a second semantic reviewer here."""
        pack = self.prepare_context(occurrence, run_id, snapshot_id, selected_ids)
        outcome = self.generate_draft(occurrence, pack)
        if self.require_reviewer or outcome.draft is None:
            return outcome
        check=TranslationCheck(occurrence.occurrence_id,occurrence.source_version,text_hash(outcome.target),
                               pack.context_hash,'not-reviewed:memory-trial',False)
        return self._record_draft(outcome.draft,check,allow_unreviewed=True)

    def complete_review(self, draft_ref: MemoryRef, check: TranslationCheck) -> GameOutcome:
        """Consume the Agent Reviewer's existing check, then update memory independently."""
        if not check.passed:
            return GameOutcome('needs_revision',self.memory.store.get(draft_ref).content['target'],draft=draft_ref)
        return self._record_draft(draft_ref,check)

    def _record_draft(self, draft_ref: MemoryRef, check: TranslationCheck, *,
                      allow_unreviewed: bool = False) -> GameOutcome:
        store=self.memory.store
        draft=store.get(draft_ref)
        if draft.kind!='translation' or draft.status!='draft':
            raise ValueError('Expected a translation draft')
        target=draft.content['target']
        occurrence=store.source(draft.scope.project_id,draft.evidence[0])
        manifest=draft.content['context_manifest']
        # A committed TM stores its draft identity in the same transaction as the TM.
        # A receipt records normal completion; the TM identity closes the crash-before-receipt gap.
        receipt = store.db.execute('SELECT * FROM game_review_receipts WHERE draft_id=? AND draft_version=?',
                                   (draft_ref.record_id, draft_ref.version)).fetchone()
        tm_id = 'tm:' + text_hash(json.dumps([draft.run_id, occurrence.occurrence_id], ensure_ascii=False, sort_keys=True))
        latest = store.latest_version(tm_id)
        existing = store.get(MemoryRef(tm_id, latest)) if latest else None
        if existing and existing.content.get('reviewed_draft') == asdict(draft_ref):
            saved = existing
            if saved.content['check'] != asdict(check) or not store.is_valid(saved.ref):
                raise ValueError('Existing submission is stale or belongs to a different review')
            if receipt:
                data = json.loads(receipt['outcome_json'])
                return GameOutcome(data['status'], data['target'], MemoryRef(**data['translation']),
                    tuple(MemoryRef(**r) for r in data['memories']), tuple(data['issues']), MemoryRef(**data['draft']))
            # Do not repeat model memory calls after an ambiguous interruption.
            return GameOutcome('committed_with_warnings', target, saved.ref,
                issues=('Recovered committed translation; memory update completion unknown, not replayed',), draft=draft_ref)
        pack = self.restore_context(occurrence, manifest, draft.content['context_hash'])
        pool = pack.pool
        saved=self.retriever.commit(occurrence,target,check,pack,allow_unreviewed=allow_unreviewed,
                                    reviewed_draft=draft_ref)
        approved,errors=[],[]
        for kind in ('term','summary','style'):
            try:
                drafts=(self.memory.extract_terms(saved.ref) if kind=='term' else
                    self.retriever.update_memory(occurrence,store.view(draft.run_id),pool.graph_snapshot,kind=kind))
                for item in drafts:
                    approved.append(store.approve(item.ref,
                        'program:unreviewed-trial' if allow_unreviewed else 'program:reviewed-source',
                        verification='unreviewed' if allow_unreviewed else None).ref)
            except Exception as error:
                errors.append(f'{kind}: {type(error).__name__}: {error}')
        issues=tuple(draft.content.get('warnings',[]))+tuple(errors)
        status=('recorded_unreviewed' if allow_unreviewed else 'committed')+('_with_warnings' if issues else '')
        outcome = GameOutcome(status,target,saved.ref,tuple(approved),issues,draft_ref)
        with store.db:
            store.db.execute('INSERT INTO game_review_receipts VALUES (?,?,?,?)',
                (draft_ref.record_id,draft_ref.version,json.dumps(asdict(check)),json.dumps(asdict(outcome))))
        return outcome
