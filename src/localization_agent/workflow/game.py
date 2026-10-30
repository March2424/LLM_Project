"""Minimal sequential integration, not the planned LangGraph repair workflow."""
from dataclasses import asdict, dataclass
import json
import re
from collections import Counter
from localization_agent.schemas.memory import MemoryRef, Occurrence, TranslationCheck, text_hash
from localization_agent.retrieval.service import GraphRetriever


@dataclass(frozen=True)
class GameOutcome:
    status: str
    target: str
    translation: MemoryRef | None = None
    memories: tuple[MemoryRef, ...] = ()
    issues: tuple[str, ...] = ()


class GameSession:
    def __init__(self, retriever: GraphRetriever, *, placeholder_pattern: str = r'\{[^{}\n]+\}|%(?:\d+\$)?[-+#0 ]*(?:\d+|\*)?(?:\.\d+)?[sdif]|<[^>\n]+>'):
        self.retriever = retriever
        self.memory = retriever.memory
        self.placeholder_pattern = re.compile(placeholder_pattern)

    def translate_one(self, occurrence: Occurrence, run_id: str, snapshot_id: str) -> GameOutcome:
        store = self.memory.store
        if store.run(run_id)['mode'] != 'game_resources':
            raise ValueError('GameSession requires game_resources mode')
        view = store.view(run_id)
        pack = self.retriever.build_context(self.retriever.retrieve(occurrence, view, snapshot_id))
        if pack.status != 'ready':
            return GameOutcome(pack.status, '', issues=pack.pool.conflicts)
        contexts = json.dumps([{'category': c.category, 'text': c.text} for c in pack.selected], ensure_ascii=False)
        target, _ = self.memory._call('translate_game', 'translate_game.txt', source=occurrence.source,
            source_lang=occurrence.source_lang, target_lang=occurrence.target_lang, contexts=contexts)
        raw, review_call = self.memory._call('check_game_translation', 'check_game.txt', source=occurrence.source,
            target=target, contexts=contexts)
        verdict = json.loads(raw)
        if not isinstance(verdict, dict) or type(verdict.get('passed')) is not bool or not isinstance(verdict.get('issues'), list) or any(
                not isinstance(x, str) for x in verdict['issues']):
            raise ValueError('Translation reviewer must return passed bool and issues string list')
        if Counter(self.placeholder_pattern.findall(occurrence.source)) != Counter(self.placeholder_pattern.findall(target)):
            verdict['issues'].append('Placeholder/tag mismatch')
        for item in pack.selected:
            if item.mandatory and item.category == 'term':
                term = store.get(item.memory_ref)
                if term.content['target'] not in target:
                    verdict['issues'].append('Mandatory term missing: ' + term.content['source'])
        if not verdict['passed'] or verdict['issues']:
            return GameOutcome('failed_check', target, issues=tuple(verdict['issues']))
        check = TranslationCheck(occurrence.occurrence_id, occurrence.source_version, text_hash(target),
                                 pack.context_hash, 'model:' + review_call, True)
        saved = self.retriever.commit(occurrence, target, check, pack)
        approved = []
        try:
            drafts = self.memory.extract_terms(saved.ref)
            drafts += self.retriever.update_memory(occurrence, store.view(run_id), snapshot_id)
            for draft in drafts:
                raw, call = self.memory._call('review_memory', 'review_memory.txt',
                    candidate=json.dumps(asdict(draft), ensure_ascii=False),
                    sources=json.dumps({'dependencies': [asdict(store.get(ref)) for ref in draft.dependencies], 'source_evidence': [{'source_text': store.source(occurrence.project_id, e).source,
                        'native_context': store.source(occurrence.project_id, e).metadata.get('native_context', '')}
                        for e in draft.evidence]}, ensure_ascii=False))
                verdict = json.loads(raw)
                if not isinstance(verdict, dict) or verdict.get('status') not in {'accepted', 'rejected', 'unknown'}:
                    raise ValueError('Memory reviewer must return accepted/rejected/unknown')
                if verdict['status'] == 'accepted':
                    approved.append(store.approve(draft.ref, 'model:' + call).ref)
        except Exception as error:
            # The translation has already committed. Report it explicitly to avoid blind replay.
            return GameOutcome('memory_update_failed', target, saved.ref, tuple(approved),
                               (f'{type(error).__name__}: {error}',))
        return GameOutcome('committed', target, saved.ref, tuple(approved))
