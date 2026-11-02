"""Generate once; the Agent Reviewer owns semantic approval and the subsequent commit."""
from dataclasses import dataclass
import re
from collections import Counter

from localization_agent.schemas.memory import Evidence, MemoryRef, Occurrence, ReadView, TranslationCheck, text_hash
from localization_agent.retrieval.service import GraphRetriever


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

    def placeholders(self, text: str) -> list[str]:
        # "50% durability" is prose, not a printf space-flag conversion "% d".
        return [match.group() for match in self.placeholder_pattern.finditer(text)
                if not (match.group().startswith('%') and ' ' in match.group()
                        and match.end() < len(text) and text[match.end()].isascii()
                        and text[match.end()].isalpha())]

    def translate_one(self, occurrence: Occurrence, run_id: str, snapshot_id: str, *,
                      selected_ids: tuple[str, ...] | None = None) -> GameOutcome:
        """Persist a draft and warnings; do not call a second semantic reviewer here."""
        store = self.memory.store
        if store.run(run_id)['mode'] != 'game_resources':
            raise ValueError('GameSession requires game_resources mode')
        pack = self.retriever.build_context(self.retriever.retrieve(occurrence, store.view(run_id), snapshot_id),selected_ids)
        if pack.status != 'ready':
            return GameOutcome(pack.status, '', issues=pack.pool.conflicts)
        target, call = self.memory._call('translate_game', 'translate_game.txt', source=occurrence.source,
            source_lang=occurrence.source_lang, target_lang=occurrence.target_lang, contexts=pack.model_context,
            source_context=pack.manifest['current_source_context'])
        warnings=[]
        if Counter(self.placeholders(occurrence.source)) != Counter(self.placeholders(target)):
            warnings.append('Placeholder/tag mismatch')
        for item in pack.selected:
            if item.mandatory and item.category=='term':
                term=store.get(item.memory_ref)
                if term.content['target'] not in target:warnings.append('Mandatory term missing: '+term.content['source'])
        draft=store.draft('translation',store.game_scope(occurrence),
            {'source':occurrence.source,'target':target,'metadata':{**occurrence.metadata,**pack.pool.resolved_metadata},
             'context_manifest':pack.manifest,'context_hash':pack.context_hash,'model_call':call,'warnings':warnings},
            tuple(dict.fromkeys((Evidence(occurrence.occurrence_id,occurrence.source_version,occurrence.source),)+
                                tuple(Evidence(**e) for e in pack.manifest['sources']))),
            dependencies=tuple(c.memory_ref for c in pack.selected if c.memory_ref),
            run_id=run_id,position=occurrence.position)
        if self.require_reviewer:
            return GameOutcome('review_pending',target,issues=tuple(warnings),draft=draft.ref)
        check=TranslationCheck(occurrence.occurrence_id,occurrence.source_version,text_hash(target),
                               pack.context_hash,'not-reviewed:memory-trial',False)
        return self._record_draft(draft.ref,check,allow_unreviewed=True)

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
        pool=self.retriever.retrieve(occurrence,ReadView(**manifest['view']),manifest['graph_snapshot'])
        pack=self.retriever.build_context(pool,tuple(item['id'] for item in manifest['items']))
        if pack.context_hash!=draft.content['context_hash']:
            raise ValueError('Context changed; send the current input to Reviewer')
        saved=self.retriever.commit(occurrence,target,check,pack,allow_unreviewed=allow_unreviewed)
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
        return GameOutcome(status,target,saved.ref,tuple(approved),issues,draft_ref)
