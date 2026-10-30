"""M2 contracts. Test references intentionally have no field in these objects."""

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal


def text_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Occurrence:
    project_id: str
    document_id: str
    occurrence_id: str
    position: int
    source: str
    source_version: int = 1
    metadata: dict[str, str | None] = field(default_factory=dict)
    source_lang: str = "en"
    target_lang: str = "zh"


@dataclass(frozen=True)
class Scope:
    project_id: str
    document_id: str | None = None
    conditions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    source_lang: str = "en"
    target_lang: str = "zh"

    def __post_init__(self):
        # A single string is one allowed value, never a substring membership test.
        object.__setattr__(self, "conditions", {
            key: (values,) if isinstance(values, str) else tuple(values)
            for key, values in self.conditions.items()
        })

    @classmethod
    def for_occurrence(cls, occurrence: Occurrence) -> "Scope":
        return cls(occurrence.project_id, occurrence.document_id, {
            key: (value,) for key, value in occurrence.metadata.items() if value is not None
        }, occurrence.source_lang, occurrence.target_lang)


def scope_match(scope: Scope, occurrence: Occurrence) -> Literal["match", "no_match", "unknown"]:
    if scope.project_id != occurrence.project_id:
        return "no_match"
    if (scope.source_lang, scope.target_lang) != (occurrence.source_lang, occurrence.target_lang):
        return "no_match"
    if scope.document_id is not None and scope.document_id != occurrence.document_id:
        return "no_match"
    unknown = False
    for key, allowed in scope.conditions.items():
        value = occurrence.metadata.get(key)
        if value is None:
            unknown = True
        elif value not in allowed:
            return "no_match"
    return "unknown" if unknown else "match"


@dataclass(frozen=True)
class Evidence:
    occurrence_id: str
    source_version: int
    quote: str


@dataclass(frozen=True)
class MemoryRef:
    record_id: str
    version: int


@dataclass(frozen=True)
class ReadView:
    run_id: str
    watermark: int


@dataclass(frozen=True)
class TranslationCheck:
    occurrence_id: str
    source_version: int
    target_hash: str
    context_hash: str
    reviewer: str
    passed: bool


@dataclass(frozen=True)
class MemoryRecord:
    record_id: str
    version: int
    kind: Literal["term", "summary", "translation", "style"]
    scope: Scope
    content: dict
    evidence: tuple[Evidence, ...]
    dependencies: tuple[MemoryRef, ...] = ()
    status: Literal["draft", "approved"] = "draft"
    run_id: str | None = None
    position: int | None = None
    commit_seq: int = 0
    reviewer: str | None = None

    @property
    def ref(self) -> MemoryRef:
        return MemoryRef(self.record_id, self.version)


@dataclass(frozen=True)
class MemoryBundle:
    view: ReadView
    terms: tuple[MemoryRecord, ...] = ()
    summaries: tuple[MemoryRecord, ...] = ()
    stm: tuple[MemoryRecord, ...] = ()
    ltm: tuple[MemoryRecord, ...] = ()
    styles: tuple[MemoryRecord, ...] = ()
    conflicts: tuple[str, ...] = ()

    @property
    def refs(self) -> tuple[MemoryRef, ...]:
        """Candidates only: this is NOT a manifest of actual translator input."""
        return tuple(r.ref for group in (self.terms, self.summaries, self.stm, self.ltm, self.styles)
                     for r in group)
