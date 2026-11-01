"""Source-only four-column benchmark input and automatic graph build results."""
from dataclasses import dataclass
from .memory import Occurrence
from .graph import GraphRef


@dataclass(frozen=True)
class BenchmarkInput:
    occurrences: tuple[Occurrence, ...]
    nodes: tuple[GraphRef, ...]
    batches: tuple[tuple[GraphRef, ...], ...]
    skipped_formatting_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BuildResult:
    entities: tuple[GraphRef, ...]
    edges: tuple[GraphRef, ...]
    extraction_call: str
    verification: str = 'program_only'
    rejected: tuple[dict, ...] = ()
