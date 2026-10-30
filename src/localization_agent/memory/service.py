"""DelTA-style Context / History / Summary / RetrieveAgent, backed by MemoryStore.

Algorithm reference: DocMTAgent @ 6dde70acc9e60fd2533c2ca467eb327884d20dab.
The original model globals and eval-based parsing are replaced by injected providers
and JSON. See docs/MEMORY.md and THIRD_PARTY_NOTICES.md for adaptation details.
"""

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from localization_agent.models.protocols import ChatModel, Embedder
from localization_agent.schemas.memory import (
    Evidence, MemoryBundle, MemoryRecord, MemoryRef, Occurrence, ReadView, Scope,
    scope_match, text_hash,
)
from .store import MemoryStore


@dataclass(frozen=True)
class MemoryConfig:
    stm_size: int = 3
    ltm_window: int = 20
    ltm_top_k: int = 2
    summary_step: int = 20
    retrieval: str = "lexical"
    recency_weight: float = 0.0
    prompt_dir: Path = Path(__file__).resolve().parents[3] / "prompts" / "memory"

    def __post_init__(self):
        if min(self.stm_size, self.ltm_top_k) < 0 or self.summary_step < 1 or self.ltm_window < 1:
            raise ValueError("Invalid memory window or summary interval")
        if self.retrieval not in {"lexical", "agent", "embedding"} or not 0 <= self.recency_weight <= 1:
            raise ValueError("Invalid retrieval mode or recency weight")

    @classmethod
    def from_file(cls, path: str | Path) -> "MemoryConfig":
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if "prompt_dir" in data:
            data["prompt_dir"] = Path(data["prompt_dir"])
        return cls(**data)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\u3400-\u9fff]|[^\W_]+", text.casefold()))


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("Embedding vectors must have the same nonzero dimension")
    norm = math.sqrt(sum(x*x for x in left) * sum(x*x for x in right))
    if not norm:
        raise ValueError("Zero embedding vector")
    return sum(x*y for x, y in zip(left, right)) / norm


class MemoryService:
    def __init__(self, store: MemoryStore, config: MemoryConfig | None = None,
                 *, chat: ChatModel | None = None, embedder: Embedder | None = None):
        self.store = store
        self.config = config or MemoryConfig()
        self.chat = chat
        self.embedder = embedder

    def _call(self, task: str, template: str, **values) -> tuple[str, str]:
        if self.chat is None:
            raise RuntimeError(f"{task} needs a configured ChatModel")
        prompt = (self.config.prompt_dir / template).read_text(encoding="utf-8").format(**values)
        try:
            reply = self.chat(task, prompt)
            if not reply.text.strip():
                raise ValueError("Model returned an empty response")
        except Exception as error:
            self.store.log_call(task, prompt, None, f"{type(error).__name__}: {error}")
            raise
        call_id = self.store.log_call(task, prompt, asdict(reply))
        return reply.text.strip(), call_id

    def eligible(self, occurrence: Occurrence, view: ReadView) -> list[MemoryRecord]:
        run = self.store.run(view.run_id)
        self.store.check_run(occurrence, run)
        game = run['mode'] == 'game_resources'
        result = []
        for record in self.store.visible(view):
            if not game and record.run_id and record.position >= occurrence.position:
                continue
            if scope_match(record.scope, occurrence) != "match":
                continue
            if game and record.kind in {'translation', 'summary'}:
                if not self.store.game_compatible(record, occurrence):
                    continue
                if record.kind == 'translation' and record.evidence[0].occurrence_id == occurrence.occurrence_id:
                    continue
                result.append(record)
                continue
            # Target-side examples are conservative: unknown vs known is not a wildcard.
            if record.kind in {"translation", "summary"} and (
                    record.scope != Scope.for_occurrence(occurrence)
                    or record.content.get("metadata", {}) != occurrence.metadata):
                continue
            result.append(record)
        return result

    def read(self, occurrence: Occurrence, view: ReadView, *, related_ids: set[str] | None = None) -> MemoryBundle:
        records = self.eligible(occurrence, view)
        game = self.store.run(view.run_id)['mode'] == 'game_resources'
        topic = occurrence.metadata.get('topic_id')
        def related(record):
            return bool((topic and record.content.get('metadata', {}).get('topic_id') == topic)
                        or (related_ids and record.evidence[0].occurrence_id in related_ids))
        translations = sorted((r for r in records if r.kind == "translation"), key=lambda r: (r.commit_seq if game else r.position, r.record_id))
        recent = [r for r in translations if related(r)] if game else translations
        stm = recent[-self.config.stm_size:] if self.config.stm_size else []
        stm_ids = {r.record_id for r in stm}
        # One canonical TM; exclude STM before ranking LTM so the slots are not duplicated.
        candidates = [r for r in (translations if game else translations[-self.config.ltm_window:]) if r.record_id not in stm_ids]
        if game:
            linked = [r for r in candidates if related(r)]
            candidates = linked or candidates
        ltm = self._retrieve(occurrence.source, candidates)
        terms = [r for r in records if r.kind == "term" and r.content["source"] in occurrence.source]
        by_source: dict[str, set[str]] = {}
        for term in terms:
            by_source.setdefault(term.content["source"], set()).add(term.content["target"])
        conflicts = tuple(source for source, targets in by_source.items() if len(targets) > 1)
        return MemoryBundle(view, tuple(terms), tuple(r for r in records if r.kind == "summary" and (not game or related(r))),
                            tuple(stm), tuple(ltm), tuple(r for r in records if r.kind == "style"), conflicts)

    def _retrieve(self, query: str, candidates: list[MemoryRecord]) -> list[MemoryRecord]:
        count = min(self.config.ltm_top_k, len(candidates))
        if count == 0:
            return []
        if len(candidates) <= count:
            return candidates
        if self.config.retrieval == "agent":
            reply, _ = self._call("retrieve", "retrieve.txt", top_k=count, query=query,
                                  sentences=json.dumps([{ "id": r.record_id, "source": r.content["source"]}
                                                        for r in candidates], ensure_ascii=False))
            ids = json.loads(reply)
            lookup = {r.record_id: r for r in candidates}
            if (not isinstance(ids, list) or any(not isinstance(i, str) for i in ids)
                    or len(ids) != count or len(set(ids)) != len(ids) or any(i not in lookup for i in ids)):
                raise ValueError("Retriever must return exactly top_k distinct allowed IDs")
            return [lookup[i] for i in ids]
        if self.config.retrieval == "embedding":
            if self.embedder is None:
                raise RuntimeError("Embedding retrieval needs an Embedder")
            query_vector = self.embedder(query)
            similarity = [_cosine(query_vector, self.embedder(r.content["source"])) for r in candidates]
        else:
            query_tokens = _tokens(query)
            similarity = []
            for record in candidates:
                tokens = _tokens(record.content["source"])
                similarity.append(len(query_tokens & tokens) / max(1, len(query_tokens | tokens)))
        weight = self.config.recency_weight
        scores = [(1-weight)*score + weight*idx/max(1, len(candidates)-1) for idx, score in enumerate(similarity)]
        indices = sorted(range(len(candidates)), key=lambda i: (scores[i], candidates[i].position), reverse=True)[:count]
        return [candidates[i] for i in sorted(indices)]

    def extract_terms(self, translation: MemoryRef) -> tuple[MemoryRecord, ...]:
        record = self.store.get(translation)
        if record.kind != "translation" or record.status != "approved" or not self.store.is_valid(translation):
            raise ValueError("Term extraction needs a verified, valid translation")
        reply, call_id = self._call("extract_terms", "terms.txt", source_lang=record.scope.source_lang,
                                   target_lang=record.scope.target_lang, source=record.content["source"],
                                   target=record.content["target"])
        pairs = json.loads(reply)
        if not isinstance(pairs, list):
            raise ValueError("Term extractor must return a JSON array")
        for pair in pairs:
            if (not isinstance(pair, dict) or set(pair) != {"source", "target"}
                    or any(not isinstance(pair[k], str) or not pair[k] for k in pair)
                    or pair["source"] not in record.content["source"]
                    or pair["target"] not in record.content["target"]):
                raise ValueError("Term pair must be grounded in both sides of the verified translation")
        drafts = []
        for pair in pairs:
            key = json.dumps([record.run_id, asdict(record.scope), pair], ensure_ascii=False, sort_keys=True)
            record_id = "term:" + text_hash(key)
            if self.store.latest_version(record_id):
                # Re-extraction of the same proposal is idempotent while it remains valid.
                existing = self.store.get(MemoryRef(record_id, self.store.latest_version(record_id)))
                if self.store.is_valid(existing.ref):
                    continue
            evidence = Evidence(record.evidence[0].occurrence_id, record.evidence[0].source_version, pair["source"])
            drafts.append(self.store.draft("term", record.scope, {**pair, "model_call": call_id},
                                           (evidence,), dependencies=(record.ref,), run_id=record.run_id,
                                           position=record.position, record_id=record_id,
                                           expected_version=self.store.latest_version(record_id)))
        return tuple(drafts)

    def summarize(self, next_occurrence: Occurrence, view: ReadView, *, flush: bool = False) -> MemoryRecord | None:
        """Draft one chunk (including its final committed sentence); approve before next update.

        flush=True handles a scene/document tail shorter than summary_step.
        next_occurrence supplies the scope and the exclusive source-position boundary.
        """
        if self.store.run(view.run_id)['mode'] == 'game_resources':
            return self.summarize_topic(next_occurrence, view, flush=flush)
        records = self.eligible(next_occurrence, view)
        previous = max((r for r in records if r.kind == "summary"), key=lambda r: r.position, default=None)
        pending = sorted((r for r in records if r.kind == "translation" and
                          (previous is None or r.position > previous.position)), key=lambda r: r.position)
        if not pending or (not flush and len(pending) < self.config.summary_step):
            return None
        chunk = pending[:self.config.summary_step]
        language = f"{next_occurrence.source_lang}-{next_occurrence.target_lang}"
        if language not in {"en-zh", "zh-en"}:
            raise ValueError("Summary prompt adapters currently support en-zh and zh-en")
        calls = []
        summaries = {}
        for side, prefix in (("source", "src"), ("target", "tgt")):
            paragraph = "\n".join(r.content[side] for r in chunk)
            summary, call_id = self._call(f"summarize_{side}", f"{language}/{prefix}_summary_prompt.txt", src_para=paragraph)
            calls.append(call_id)
            if previous:
                summary, call_id = self._call(f"merge_{side}", f"{language}/{prefix}_merge_prompt.txt",
                                              summary_1=previous.content[side], summary_2=summary)
                calls.append(call_id)
            summaries[side] = summary
        scope = Scope.for_occurrence(next_occurrence)
        key = json.dumps([view.run_id, asdict(scope), next_occurrence.metadata], ensure_ascii=False, sort_keys=True)
        record_id = "summary:" + text_hash(key)
        dependencies = tuple(r.ref for r in chunk) + ((previous.ref,) if previous else ())
        evidence = tuple(dict.fromkeys((previous.evidence if previous else ()) + tuple(e for r in chunk for e in r.evidence)))
        return self.store.draft("summary", scope,
                               {**summaries, "metadata": next_occurrence.metadata, "model_calls": calls,
                                "covered_translations": [asdict(r.ref) for r in chunk], "input_view": asdict(view)},
                               evidence, dependencies=dependencies, run_id=view.run_id, position=chunk[-1].position,
                               record_id=record_id, expected_version=self.store.latest_version(record_id))

    def summarize_topic(self, occurrence: Occurrence, view: ReadView, *, flush: bool = False,
                        topic_id: str | None = None, sources: tuple[Evidence, ...] = (),
                        context_manifest: dict | None = None) -> MemoryRecord | None:
        """Rebuild a compact topic overview; target coverage is run-local verified TM only."""
        run = self.store.run(view.run_id)
        self.store.check_run(occurrence, run)
        if run['mode'] != 'game_resources':
            raise ValueError('Topic summaries require game_resources mode')
        topic = topic_id or occurrence.metadata.get('topic_id')
        if not topic:
            return None
        scope = self.store.game_scope(occurrence)
        ids = {e.occurrence_id for e in sources}
        records = [r for r in self.store.visible(view) if r.kind == 'translation' and
                   scope_match(r.scope, occurrence) == 'match' and self.store.game_compatible(r, occurrence) and
                   (r.evidence[0].occurrence_id in ids or r.content.get('metadata', {}).get('topic_id') == topic)]
        if not records:
            return None
        key = 'topic:' + text_hash(json.dumps([view.run_id, asdict(scope), topic], sort_keys=True))
        version = self.store.latest_version(key)
        previous = self.store.get(MemoryRef(key, version)) if version else None
        inputs = [asdict(r.ref) for r in records]
        evidence = tuple(dict.fromkeys(sources + tuple(r.evidence[0] for r in records)))
        if previous and self.store.is_valid(previous.ref) and previous.content.get('covered_translations') == inputs and previous.evidence == evidence:
            return None
        old = previous.content.get('covered_translations', []) if previous and self.store.is_valid(previous.ref) else []
        if not flush and sum(ref not in old for ref in inputs) < self.config.summary_step:
            return None
        source_texts = []
        for e in evidence:
            src = self.store.source(occurrence.project_id, e)
            if (src.source_lang, src.target_lang) != (occurrence.source_lang, occurrence.target_lang) or any(
                    src.metadata.get(k) != occurrence.metadata.get(k) for k in ('branch_id', 'phase_id', 'requirements')):
                raise ValueError('Topic source conditions/direction differ')
            source_texts.append(e.quote)
        result, calls = {}, []
        for side, texts in [('source', source_texts), ('target', [r.content['target'] for r in records])]:
            result[side], call = self._call('topic_' + side, 'topic.txt', side=side, topic=topic,
                                           texts=json.dumps(texts, ensure_ascii=False))
            calls.append(call)
        return self.store.draft('summary', scope, {**result, 'summary_mode': 'topic',
            'metadata': {**occurrence.metadata, 'topic_id': topic}, 'covered_translations': inputs,
            'model_calls': calls, 'input_view': asdict(view), 'context_manifest': context_manifest or {}}, evidence,
            dependencies=tuple(r.ref for r in records), run_id=view.run_id, position=view.watermark,
            record_id=key, expected_version=version)

    def extract_style(self, occurrence: Occurrence, view: ReadView, *,
                      context_manifest: dict | None = None) -> MemoryRecord | None:
        """Draft a source-side role note; independent semantic review gates creation."""
        speaker = occurrence.metadata.get('speaker_id')
        if not speaker:
            return None
        run = self.store.run(view.run_id)
        self.store.check_run(occurrence, run)
        if run['mode'] != 'game_resources':
            raise ValueError('Automatic role notes require game_resources mode')
        key = 'style:' + text_hash(json.dumps([view.run_id, speaker, asdict(self.store.game_scope(occurrence))], sort_keys=True))
        version = self.store.latest_version(key)
        if version and self.store.is_valid(MemoryRef(key, version)):
            return None
        note, call = self._call('extract_style', 'style.txt', speaker=speaker,
                               source=occurrence.source, context=occurrence.metadata.get('native_context', ''))
        verdict, review = self._call('review_style', 'review_style.txt', source=occurrence.source,
                                    context=occurrence.metadata.get('native_context', ''), note=note)
        decision = json.loads(verdict)
        if not isinstance(decision, dict) or decision.get('status') not in {'accepted', 'rejected', 'unknown'}:
            raise ValueError('Style reviewer must return accepted/rejected/unknown')
        if decision['status'] != 'accepted':
            return None
        base = self.store.game_scope(occurrence)
        scope = Scope(base.project_id, conditions={**base.conditions, 'speaker_id': (speaker,)},
                      source_lang=base.source_lang, target_lang=base.target_lang)
        return self.store.draft('style', scope, {'note': note, 'model_calls': [call, review],
            'context_manifest': context_manifest or {}},
            (Evidence(occurrence.occurrence_id, occurrence.source_version, occurrence.source),),
            run_id=view.run_id, position=occurrence.position, record_id=key, expected_version=version)
