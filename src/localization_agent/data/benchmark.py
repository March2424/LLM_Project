"""Bridge source-only benchmark CSVs into the existing M2/M3 store."""
import csv
from pathlib import Path
from dataclasses import replace
from localization_agent.schemas.benchmark import BenchmarkInput
from localization_agent.schemas.memory import Occurrence, Evidence, Scope, text_hash
from localization_agent.schemas.graph import GraphNode
from localization_agent.retrieval.graph import GraphStore


def import_benchmark(paths: list[str | Path], project: str, graph: GraphStore, *,
                     source_lang: str = 'en', target_lang: str = 'zh',
                     source_version: int = 1, batch_size: int = 12) -> BenchmarkInput:
    """Preserve independent source rows; batches only limit model input size."""
    if not paths or batch_size < 1:
        raise ValueError('Input files and positive batch_size required')
    rows, seen, documents = [], set(), set()
    required = {'id', 'source_text', 'text_type', 'context'}
    for path in map(Path, paths):
        document = path.name
        if document in documents:
            raise ValueError('Input filenames must be distinct within a project')
        documents.add(document)
        with path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            if len(reader.fieldnames or []) != 4 or set(reader.fieldnames or []) != required:
                raise ValueError('Benchmark CSV requires exactly id,source_text,text_type,context')
            for pos, row in enumerate(reader):
                if None in row or any(v is None for v in row.values()):
                    raise ValueError(f'{path}:{reader.line_num}: malformed CSV row')
                if not row['id'].strip() or row['id'] in seen or not row['source_text'].strip():
                    raise ValueError(f'{path}:{reader.line_num}: empty/duplicate ID or empty source')
                seen.add(row['id'])
                rows.append(Occurrence(project, document, row['id'], pos, row['source_text'], source_version,
                    {'text_type': row['text_type'] or 'unknown',
                     'native_context': row['context']}, source_lang, target_lang))
    # Parse all inputs before registering any source. No reference columns are read.
    refs, registered = [], []
    for o in rows:
        # Preserve explicit native triggers conservatively, without interpreting plot state.
        if 'requires_' in o.metadata['native_context']:
            o = replace(o, metadata={**o.metadata, 'requirements': o.metadata['native_context']})
        graph.memory.put_source(o)
        evidence = (Evidence(o.occurrence_id, o.source_version, o.source),)
        key = 'occurrence:' + text_hash(project + ':' + o.occurrence_id)
        node = graph.put_node(GraphNode(key, 'occurrence', Scope(project, o.document_id,
            source_lang=source_lang, target_lang=target_lang), {'occurrence_id': o.occurrence_id,
            'text_type': o.metadata['text_type'],
            'context': o.metadata['native_context']}, evidence), expected_version=graph.latest('node', key))
        registered.append(o)
        refs.append(node.ref)
    batches = [tuple(refs[start:start + batch_size]) for start in range(0, len(refs), batch_size)]
    return BenchmarkInput(tuple(registered), tuple(refs), tuple(batches))
