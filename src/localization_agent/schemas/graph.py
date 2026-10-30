"""Graph and retrieval contracts; graph references never masquerade as memory IDs."""
from dataclasses import dataclass, field
from typing import Literal

from .memory import Evidence, MemoryRef, ReadView, Scope


@dataclass(frozen=True)
class GraphRef:
    kind: Literal['node', 'edge']
    id: str
    version: int


@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: str
    scope: Scope
    attributes: dict = field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()
    version: int = 0

    @property
    def ref(self) -> GraphRef:
        return GraphRef('node', self.id, self.version)


@dataclass(frozen=True)
class GraphEdge:
    id: str
    relation: str
    source: GraphRef
    target: GraphRef
    scope: Scope
    evidence: tuple[Evidence, ...]
    attributes: dict = field(default_factory=dict)
    status: Literal['draft', 'approved'] = 'draft'
    reviewer: str | None = None
    version: int = 0

    @property
    def ref(self) -> GraphRef:
        return GraphRef('edge', self.id, self.version)


@dataclass(frozen=True)
class RelationType:
    name: str
    source_kinds: tuple[str, ...]
    target_kinds: tuple[str, ...]
    description: str
    source_entity_types: tuple[str, ...] = ()
    target_entity_types: tuple[str, ...] = ()
    traversable: bool = True


@dataclass(frozen=True)
class GraphSnapshot:
    id: str
    project_id: str
    refs: tuple[GraphRef, ...]


@dataclass(frozen=True)
class Candidate:
    id: str
    category: str
    text: str
    mandatory: bool = False
    memory_ref: MemoryRef | None = None
    sources: tuple[Evidence, ...] = ()
    graph_refs: tuple[GraphRef, ...] = ()
    relations: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidatePool:
    occurrence_id: str
    source_version: int
    view: ReadView
    graph_snapshot: str
    candidates: tuple[Candidate, ...]
    conflicts: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    resolved_metadata: dict[str, str] = field(default_factory=dict)
    binding_refs: tuple[GraphRef, ...] = ()


@dataclass(frozen=True)
class ContextPack:
    pool: CandidatePool
    selected: tuple[Candidate, ...]
    status: Literal['ready', 'conflict', 'budget_conflict']
    context_hash: str
    manifest: dict
    characters: int
