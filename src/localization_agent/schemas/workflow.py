"""M5 contract: what one translation task actually produced."""

from dataclasses import dataclass, field
from typing import Literal

from .memory import MemoryRef

Status = Literal['committed', 'review_pending', 'needs_revision', 'missing_evidence',
                 'conflict', 'budget_exhausted', 'failed', 'locked']
IssueKind = Literal['accuracy', 'terminology', 'style', 'format', 'context', 'unsupported',
                    'missing_evidence']


@dataclass(frozen=True)
class ReviewIssue:
    """One actionable defect grounded in supplied evidence; empty means nothing to repair."""
    kind: IssueKind
    detail: str
    repairable: bool = True
    candidate_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.detail.strip():
            raise ValueError('An issue needs the concrete defect, not an empty placeholder')
        if self.kind == 'missing_evidence' and self.repairable:
            raise ValueError('Missing evidence routes to retrieval, not to the repairer')


@dataclass(frozen=True)
class TranslationOutcome:
    occurrence_id: str
    source_version: int
    status: Status
    target: str = ''
    draft: MemoryRef | None = None
    translation: MemoryRef | None = None
    context_hash: str = ''
    issues: tuple[ReviewIssue, ...] = ()
    unresolved: tuple[str, ...] = ()
    revisions: int = 0
    model_calls: tuple[str, ...] = ()
    memories: tuple[MemoryRef, ...] = ()
    budget: dict = field(default_factory=dict)
    failure: str | None = None

    def __post_init__(self):
        if self.status == 'committed' and (self.translation is None or not self.context_hash):
            raise ValueError('A committed outcome must bind the TM reference and its context')


def outcome_from_state(value: dict) -> TranslationOutcome:
    """Rebuild an outcome written by asdict; JSON keeps no tuple/ref distinction."""
    data = dict(value)
    data['issues'] = tuple(ReviewIssue(item['kind'], item['detail'], item['repairable'],
                                       tuple(item.get('candidate_ids') or ()))
                           for item in data.get('issues', ()))
    for key in ('draft', 'translation'):
        if data.get(key):
            data[key] = MemoryRef(**data[key])
    for key in ('memories', 'model_calls', 'unresolved'):
        data[key] = tuple(MemoryRef(**item) if key == 'memories' else item
                          for item in data.get(key, ()))
    return TranslationOutcome(**data)
