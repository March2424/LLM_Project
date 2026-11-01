"""Bridge source-only benchmark CSVs into the existing M2/M3 store."""
import csv
import json
import re
from pathlib import Path
from dataclasses import replace
from localization_agent.schemas.benchmark import BenchmarkInput
from localization_agent.schemas.memory import Occurrence, Evidence, Scope, text_hash
from localization_agent.schemas.graph import GraphNode
from localization_agent.retrieval.graph import GraphStore
from localization_agent.models.chat_completions import estimate_tokens


def plan_batches(rows: list[dict], *, batch_size: int = 64, input_tokens: int = 5000,
                 output_tokens: int = 8192) -> list[list[int]]:
    """Pack independent rows by input AND expected graph output; no semantic groups."""
    if min(batch_size, input_tokens, output_tokens) < 1:
        raise ValueError('Positive batch limits required')
    batches, pending, inputs, outputs = [], [], 0, 0
    for index, row in enumerate(rows):
        size = estimate_tokens(json.dumps(row, ensure_ascii=False, separators=(',', ':')))
        # Entity/relation JSON is often longer than the source, especially for short names.
        expected_output = 192 + size // 2
        if size > input_tokens or expected_output > output_tokens:
            raise ValueError(f'Row {row["id"]} exceeds batch budget; raise limits or handle it separately')
        if pending and (len(pending) >= batch_size or inputs + size > input_tokens
                        or outputs + expected_output > output_tokens):
            batches.append(pending)
            pending, inputs, outputs = [], 0, 0
        pending.append(index)
        inputs += size
        outputs += expected_output
    if pending:
        batches.append(pending)
    return batches


def read_benchmark(paths: list[str | Path], project: str, *,
                     source_lang: str = 'en', target_lang: str = 'zh',
                     source_version: int = 1, batch_size: int = 64,
                     batch_input_tokens: int = 5000, batch_output_tokens: int = 8192) -> tuple[list[Occurrence], list[list[int]], list[str]]:
    """Preserve independent source rows; batches only limit model input size."""
    if not paths or batch_size < 1:
        raise ValueError('Input files and positive batch_size required')
    rows, seen, documents, skipped = [], set(), set(), []
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
                if not row['id'].strip() or row['id'] in seen:
                    raise ValueError(f'{path}:{reader.line_num}: empty/duplicate ID or empty source')
                seen.add(row['id'])
                if not row['source_text'].strip():
                    if row['text_type'] == 'formatting':
                        skipped.append(row['id'])
                        continue
                    raise ValueError(f'{path}:{reader.line_num}: empty source')
                rows.append(Occurrence(project, document, row['id'], pos, row['source_text'], source_version,
                    {'text_type': row['text_type'] or 'unknown',
                     'native_context': row['context']}, source_lang, target_lang))
    planned = plan_batches([{'id': o.occurrence_id, 'source_text': o.source,
                            'text_type': o.metadata['text_type'], 'context': o.metadata['native_context']}
                           for o in rows], batch_size=batch_size, input_tokens=batch_input_tokens,
                           output_tokens=batch_output_tokens)
    return rows, planned, skipped


def import_benchmark(paths: list[str | Path], project: str, graph: GraphStore, *,
                     source_lang: str = 'en', target_lang: str = 'zh',
                     source_version: int = 1, batch_size: int = 64,
                     batch_input_tokens: int = 5000, batch_output_tokens: int = 8192) -> BenchmarkInput:
    rows, planned, skipped = read_benchmark(paths, project, source_lang=source_lang,
        target_lang=target_lang, source_version=source_version, batch_size=batch_size,
        batch_input_tokens=batch_input_tokens, batch_output_tokens=batch_output_tokens)
    # Parse and plan all inputs before registering any source. No reference columns are read.
    refs, registered = [], []
    for o in rows:
        # Preserve explicit native triggers conservatively, without interpreting plot state.
        if re.search(r'(?:^|;\s*)(?:requires_\w+|condition|offer_condition|availability_condition|case)\s*=',
                     o.metadata['native_context']):
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
    batches = [tuple(refs[index] for index in batch) for batch in planned]
    return BenchmarkInput(tuple(registered), tuple(refs), tuple(batches), tuple(skipped))
