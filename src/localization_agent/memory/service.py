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
        if (occurrence.project_id, occurrence.document_id, occurrence.source_lang, occurrence.target_lang) != (
                run["project"], run["document"], run["source_lang"], run["target_lang"]):
            raise ValueError("Occurrence does not belong to this run/document/direction")
        result = []
        for record in self.store.visible(view):
            if record.run_id and record.position >= occurrence.position:
                continue
            if scope_match(record.scope, occurrence) != "match":
                continue
            # Target-side examples are conservative: unknown vs known is not a wildcard.
            if record.kind in {"translation", "summary"} and (
                    record.scope != Scope.for_occurrence(occurrence)
                    or record.content.get("metadata", {}) != occurrence.metadata):
                continue
            result.append(record)
        return result

    def read(self, occurrence: Occurrence, view: ReadView) -> MemoryBundle:
        records = self.eligible(occurrence, view)
        translations = sorted((r for r in records if r.kind == "translation"), key=lambda r: (r.position, r.record_id))
        stm = translations[-self.config.stm_size:] if self.config.stm_size else []
        stm_ids = {r.record_id for r in stm}
        # One canonical TM; exclude STM before ranking LTM so the slots are not duplicated.
        candidates = [r for r in translations[-self.config.ltm_window:] if r.record_id not in stm_ids]
        ltm = self._retrieve(occurrence.source, candidates)
        terms = [r for r in records if r.kind == "term" and r.content["source"] in occurrence.source]
        by_source: dict[str, set[str]] = {}
        for term in terms:
            by_source.setdefault(term.content["source"], set()).add(term.content["target"])
        conflicts = tuple(source for source, targets in by_source.items() if len(targets) > 1)
        return MemoryBundle(view, tuple(terms), tuple(r for r in records if r.kind == "summary"),
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
