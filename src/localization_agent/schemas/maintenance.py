"""M6 contract: what one rule/source update actually changed and what it deliberately did not."""

from dataclasses import dataclass, field
from typing import Literal

from .memory import Evidence, MemoryRef

EventKind = Literal['term', 'style', 'summary', 'source', 'graph', 'rule']
Decision = Literal['no_change', 'needs_revision', 'locked', 'unresolved']
Discovery = Literal['dependency', 'scope', 'retrieval']


@dataclass(frozen=True)
class UpdateEvent:
    """A change that may invalidate committed translations."""
    project_id: str
    kind: EventKind
    reason: str
    ref: MemoryRef | None = None
    previous: dict | None = None
    current: dict | None = None
    source_change: Evidence | None = None

    def __post_init__(self):
        if not self.reason.strip():
            raise ValueError('An update event must state what changed')
        if self.kind == 'source' and self.source_change is None:
            raise ValueError('A source event needs the changed occurrence evidence')
        if self.kind != 'source' and self.ref is None:
            raise ValueError('A memory/graph event needs the changed reference')


@dataclass(frozen=True)
class AffectedTranslation:
    translation: MemoryRef
    occurrence_id: str
    source_version: int
    discovery: tuple[Discovery, ...]
    decision: Decision
    reason: str
    old_target: str = ''
    new_target: str = ''
    context_hash: str = ''

    def __post_init__(self):
        if not self.discovery:
            raise ValueError('Every affected item states how it was found')
        if self.decision == 'needs_revision' and not self.new_target.strip():
            raise ValueError('A revision carries the proposed target')
        if self.decision == 'no_change' and self.new_target and self.new_target != self.old_target:
            raise ValueError('no_change cannot carry a different target')

    @property
    def diff(self) -> tuple[str, str]:
        return (self.old_target, self.new_target)


@dataclass(frozen=True)
class UpdateReport:
    event: UpdateEvent
    candidates: int
    items: tuple[AffectedTranslation, ...] = ()
    conflicts: tuple[str, ...] = ()
    model_calls: tuple[str, ...] = ()
    counts: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.counts:
            # A frozen record still carries its own tally, so asdict() keeps the report complete.
            object.__setattr__(self, 'counts', {d: sum(1 for i in self.items if i.decision == d)
                                                for d in ('no_change', 'needs_revision', 'locked', 'unresolved')})

    @property
    def revised(self) -> tuple[AffectedTranslation, ...]:
        return tuple(i for i in self.items if i.decision == 'needs_revision')

    @property
    def untouched(self) -> tuple[AffectedTranslation, ...]:
        """Deliberately not regenerated: the maintenance saving the project claims to measure."""
        return tuple(i for i in self.items if i.decision in ('no_change', 'locked'))
