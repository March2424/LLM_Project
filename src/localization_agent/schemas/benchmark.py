"""Source-only four-column benchmark input and automatic graph build results."""
from dataclasses import dataclass
from .memory import Occurrence
from .graph import GraphRef


@dataclass(frozen=True)
class BenchmarkInput:
    occurrences: tuple[Occurrence, ...]
    nodes: tuple[GraphRef, ...]
    batches: tuple[tuple[GraphRef, ...], ...]


@dataclass(frozen=True)
class BuildResult:
    entities: tuple[GraphRef, ...]
    edges: tuple[GraphRef, ...]
    decisions: dict[str, dict]
    extraction_call: str
    review_call: str
