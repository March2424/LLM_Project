"""Source-only CSV import. Physical row order is not assumed to be narrative order."""
import csv
from dataclasses import dataclass, field
from pathlib import Path

from localization_agent.schemas.memory import Evidence, Occurrence, Scope, text_hash
from localization_agent.schemas.graph import GraphNode, GraphEdge, GraphRef
from localization_agent.retrieval.graph import GraphStore


@dataclass(frozen=True)
class CSVConfig:
    namespace: str
    source_column: str = 'source'
    id_column: str | None = 'id'
    # Explicit source-side columns only. Reference/target columns are never auto-detected.
    columns: dict[str, str] = field(default_factory=dict)
    max_rows: int = 8
    max_characters: int = 1200
    order_trusted: bool = False
    source_version: int = 1
    source_lang: str = 'en'
    target_lang: str = 'zh'
    mode: str = 'document'


@dataclass(frozen=True)
class CSVImport:
    occurrences: tuple[Occurrence, ...]
    blocks: tuple[GraphNode, ...]
    warnings: tuple[str, ...]


def import_csv(path: str | Path, project_id: str, graph: GraphStore, config: CSVConfig,
               *, annotations: dict[str, dict[str, str | None]] | None = None) -> CSVImport:
    """Annotations are caller-reviewed source metadata keyed by original CSV key.

    document/scene/block are structural fields; branch/phase/speaker/addressee/text_type
    are compatibility metadata. Unknown values stay None. No semantic inference occurs.
    """
    if not config.namespace or config.max_rows < 1 or config.max_characters < 1:
        raise ValueError('Namespace and positive block limits required')
    if config.mode not in {'document', 'game_resources'}:
        raise ValueError('Unknown CSV mode')
    allowed = {'topic_id', 'document', 'scene', 'block', 'branch_id', 'phase_id', 'speaker_id', 'addressee_id', 'text_type'}
    if set(config.columns) - allowed:
        raise ValueError('Unknown source metadata mapping')
    annotations = annotations or {}
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        required = {config.source_column, *config.columns.values()}
        if config.id_column:
            required.add(config.id_column)
        if not required <= set(reader.fieldnames or ()):
            raise ValueError(f'Missing CSV columns: {sorted(required - set(reader.fieldnames or ()))}')
        rows = list(reader)
    seen = set()
    prepared = []
    positions = {}
    for row_number, row in enumerate(rows, 2):
        key = row[config.id_column] if config.id_column else str(row_number)
        source = row[config.source_column]
        if not key or key in seen:
            raise ValueError(f'Empty or duplicate occurrence key at CSV row {row_number}: {key!r}')
        if not source or not source.strip():
            raise ValueError(f'Empty source at CSV row {row_number}')
        seen.add(key)
        labels = {name: row.get(column) or None for name, column in config.columns.items()}
        extra = annotations.get(key, {})
        if set(extra) - allowed:
            raise ValueError(f'Unknown annotation fields for {key}')
        labels.update(extra)
        # This document ID is an import unit when no chapter/document has been supplied.
        document = config.namespace + ':document:' + (labels.get('document') or '__source_unit__')
        metadata = {name: labels.get(name) for name in sorted(allowed - {'document', 'scene', 'block'})}
        if not labels.get('topic_id'):
            metadata.pop('topic_id', None)
        occurrence_id = config.namespace + ':row:' + text_hash(key)[:24]
        position = positions.get(document, 0)
        positions[document] = position + 1
        occurrence = Occurrence(project_id, document, occurrence_id, position, source,
                                config.source_version, metadata, config.source_lang, config.target_lang)
        prepared.append((occurrence, labels, key, row_number))
    unknown_keys = set(annotations) - seen
    if unknown_keys:
        raise ValueError(f'Annotation keys absent from CSV: {sorted(unknown_keys)}')

    groups = []
    for entry in prepared:
        occurrence, labels, _, _ = entry
        boundary = (occurrence.document_id, labels.get('scene'), labels.get('block'),
                    labels.get('branch_id'), labels.get('phase_id'))
        if (not groups or groups[-1][0] != boundary or len(groups[-1][1]) >= config.max_rows
                or sum(len(e[0].source) for e in groups[-1][1]) + len(occurrence.source) > config.max_characters):
            groups.append((boundary, []))
        groups[-1][1].append(entry)

    blocks = []
    previous = None
    for boundary, entries in groups:
        first, labels, _, _ = entries[0]
        scope = Scope(project_id, first.document_id, {
            name: (labels[name],) for name in ('branch_id', 'phase_id') if labels.get(name)
        }, config.source_lang, config.target_lang)
        evidence = tuple(Evidence(o.occurrence_id, o.source_version, o.source) for o, *_ in entries)
        for o, *_ in entries:
            graph.memory.put_source(o)
        document_id = 'document:' + text_hash(project_id + first.document_id)
        graph.put_node(GraphNode(document_id, 'document', Scope(project_id, first.document_id,
                             source_lang=config.source_lang, target_lang=config.target_lang),
                                 {'semantic_document': labels.get('document'), 'source_unit': config.namespace}))
        members = [(o.occurrence_id, o.source_version) for o, *_ in entries]
        block_id = 'block:' + text_hash(str((project_id, members, boundary, config.order_trusted)))
        block = graph.put_node(GraphNode(block_id, 'block', scope,
                              {'members': [o.occurrence_id for o, *_ in entries],
                               'boundary_origin': 'annotated' if labels.get('block') else 'technical',
                               'order_trusted': config.order_trusted, 'scene': labels.get('scene')}, evidence))
        blocks.append(block)
        document_node = graph.get(GraphRef(
            'node', document_id, graph.latest('node', document_id)))
        links = [('IN_DOCUMENT', block, document_node)]
        if labels.get('scene'):
            scene_id = 'scene:' + text_hash(str((project_id, first.document_id, labels['scene'], boundary[3:])))
            version = graph.latest('node', scene_id)
            scene = graph.get(GraphRef('node', scene_id, version)) if version else None
            if scene is None or not graph.valid(scene.ref):
                scene = graph.put_node(GraphNode(scene_id, 'scene', scope, {'name': labels['scene']},
                                                (evidence[0],)), expected_version=version)
            links.append(('IN_SCENE', block, scene))
        for occurrence, _, key, row_number in entries:
            e = Evidence(occurrence.occurrence_id, occurrence.source_version, occurrence.source)
            node_id = 'occurrence:' + text_hash(project_id + occurrence.occurrence_id)
            node = graph.put_node(GraphNode(node_id, 'occurrence', scope,
                                 {'occurrence_id': occurrence.occurrence_id, 'csv_key': key,
                                  'csv_row': row_number, 'position': occurrence.position}, (e,)),
                                  expected_version=graph.latest('node', node_id))
            links.append(('IN_BLOCK', node, block))
            for field, relation in (('speaker_id', 'SPOKEN_BY'), ('addressee_id', 'ADDRESSED_TO')):
                identity = occurrence.metadata.get(field)
                if identity is None:
                    continue
                entity_scope = Scope(project_id, source_lang=config.source_lang, target_lang=config.target_lang) if config.mode == 'game_resources' else scope
                entity_id = 'character:' + text_hash(str((project_id, identity, config.source_lang, config.target_lang) if config.mode == 'game_resources' else (project_id, first.document_id, identity, scope)))
                version = graph.latest('node', entity_id)
                character = graph.get(GraphRef('node', entity_id, version)) if version else None
                if character is None or not graph.valid(character.ref):
                    character = graph.put_node(GraphNode(entity_id, 'entity', entity_scope,
                        {'entity_type': 'character', 'source_id': identity}, (e,)), expected_version=version)
                links.append((relation, node, character))
        # Technical adjacency is only enabled when the caller explicitly trusts the order.
        if config.order_trusted and previous and previous[0][:2] == boundary[:2] and previous[0][3:] == boundary[3:]:
            links.append(('PREVIOUS', block, previous[1]))
        for relation, left, right in links:
            edge_id = relation + ':' + text_hash(str((left.ref, right.ref)))
            graph.put_edge(GraphEdge(edge_id, relation, left.ref, right.ref, scope,
                                     left.evidence if relation in {'IN_BLOCK', 'SPOKEN_BY', 'ADDRESSED_TO'} else evidence,
                                     status='approved', reviewer='csv-explicit-metadata'))
        previous = (boundary, block)
    warnings = []
    if config.id_column is None:
        warnings.append('Row-number IDs: freeze namespace per file revision; use stable export keys for updates.')
    if not config.order_trusted:
        warnings.append('Row order untrusted: technical blocks do not imply narrative continuity or local context.')
    if any(not labels.get('document') or not labels.get('scene') for _, labels, *_ in prepared):
        warnings.append('Missing semantic document/scene labels remain unknown; document_id may be a source-unit ID.')
    return CSVImport(tuple(e[0] for e in prepared), tuple(blocks), tuple(warnings))
