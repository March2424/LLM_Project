"""Bounded relation-guided retrieval and a deterministic character-budget context builder."""
import json
from dataclasses import asdict, dataclass, replace

from localization_agent.memory import MemoryService
from localization_agent.schemas.memory import Evidence, MemoryRecord, MemoryRef, Occurrence, ReadView, TranslationCheck, scope_match, text_hash
from localization_agent.schemas.graph import Candidate, CandidatePool, ContextPack, GraphEdge, GraphNode, GraphRef
from .graph import GraphStore


@dataclass(frozen=True)
class GraphConfig:
    max_neighbors: int = 6
    max_hops: int = 4
    max_nodes: int = 40
    max_candidates: int = 30
    max_characters: int = 6000
    source_visibility: str = 'previous'  # 'all' requires an explicitly supplied game source script

    def __post_init__(self):
        if min(self.max_neighbors, self.max_hops, self.max_nodes, self.max_candidates, self.max_characters) < 1:
            raise ValueError('Positive retrieval limits required')
        if self.source_visibility not in {'previous', 'all'}:
            raise ValueError('Unknown source visibility')

    @classmethod
    def from_file(cls, path):
        with open(path, encoding='utf-8') as handle:
            return cls(**json.load(handle))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def candidate_from_memory(record, *, mandatory=False, path=(), relations=()):
    return Candidate('memory:' + record.record_id + '@' + str(record.version), record.kind,
                     canonical({k: v for k, v in record.content.items() if k in {'source', 'target', 'note', 'metadata', 'summary_mode'}}), mandatory, record.ref, record.evidence, path, relations)


class GraphRetriever:
    def __init__(self, graph: GraphStore, memory: MemoryService, config: GraphConfig | None = None):
        if graph.memory is not memory.store:
            raise ValueError('Graph and memory service must share a MemoryStore')
        self.graph, self.memory = graph, memory
        self.config = config or GraphConfig()

    def _source_allowed(self, occurrence, evidence, game=False):
        source = self.graph.memory.source(occurrence.project_id, evidence)
        if (source.source_lang, source.target_lang) != (occurrence.source_lang, occurrence.target_lang):
            return False
        if not game and source.document_id != occurrence.document_id:
            return False
        # Unknown branch/phase cannot be treated as matching a known one, in either direction.
        for key in ('branch_id', 'phase_id'):
            if source.metadata.get(key) != occurrence.metadata.get(key):
                return False
        if source.metadata.get('requirements') and source.metadata['requirements'] != occurrence.metadata.get('requirements'):
            return False
        return game or self.config.source_visibility == 'all' or source.position <= occurrence.position

    def bindings(self, occurrence: Occurrence, snapshot_id: str) -> tuple[dict[str, str], tuple[GraphRef, ...]]:
        """Resolve only visible, approved direct links; never modify registered source."""
        snapshot = self.graph.snapshot(snapshot_id)
        if snapshot.project_id != occurrence.project_id:
            raise ValueError('Graph snapshot belongs to another project')
        nodes = {r: self.graph.get(r) for r in snapshot.refs if r.kind == 'node' and self.graph.valid(r)}
        speakers, topics = {}, {}
        for r in snapshot.refs:
            if r.kind != 'edge' or not self.graph.valid(r):
                continue
            edge = self.graph.get(r)
            if edge.source not in nodes or edge.target not in nodes:
                continue
            left, right = nodes[edge.source], nodes[edge.target]
            if left.kind != 'occurrence' or left.attributes['occurrence_id'] != occurrence.occurrence_id or right.kind != 'entity':
                continue
            if any(scope_match(replace(obj.scope, document_id=None), occurrence) != 'match' for obj in (left, right, edge)):
                continue
            if not all(self._source_allowed(occurrence, e, True) for obj in (left, right, edge) for e in obj.evidence):
                continue
            refs = (left.ref, edge.ref, right.ref)
            if edge.relation == 'SPOKEN_BY':
                speakers[right.id] = refs
            if edge.relation in {'DEFINES', 'MENTIONS'} and right.attributes['entity_type'] != 'character':
                topics[right.id] = (0 if edge.relation == 'DEFINES' else 1, refs)
        metadata, used = {}, []
        if len(speakers) == 1:
            identity, refs = next(iter(speakers.items()))
            metadata['speaker_id'] = identity
            used.extend(refs)
        if topics:
            identity = min(topics, key=lambda k: (topics[k][0], k))
            metadata['topic_id'] = identity
            used.extend(topics[identity][1])
        return metadata, tuple(dict.fromkeys(used))

    def retrieve(self, occurrence: Occurrence, view: ReadView, snapshot_id: str) -> CandidatePool:
        game = self.memory.store.run(view.run_id)['mode'] == 'game_resources'
        snapshot = self.graph.snapshot(snapshot_id)
        if snapshot.project_id != occurrence.project_id:
            raise ValueError('Graph snapshot belongs to another project')
        current = Evidence(occurrence.occurrence_id, occurrence.source_version, occurrence.source)
        if self.graph.memory.source(occurrence.project_id, current) != occurrence:
            raise ValueError('Current occurrence differs from registered source')
        latest = self.graph.db.execute('SELECT MAX(version) FROM sources WHERE project=? AND id=?',
                                       (occurrence.project_id, occurrence.occurrence_id)).fetchone()[0]
        if latest != occurrence.source_version:
            raise ValueError('Current occurrence source version is stale')
        resolved, binding_refs = self.bindings(occurrence, snapshot_id) if game else ({}, ())
        occurrence = replace(occurrence, metadata={**occurrence.metadata, **resolved})
        eligible = self.memory.eligible(occurrence, view)
        by_ref = {r.ref: r for r in eligible}
        baseline = self.memory.read(occurrence, view)
        candidates = {}
        initial = (*baseline.terms, *baseline.styles) if game else (*baseline.terms, *baseline.styles, *baseline.stm, *baseline.ltm, *baseline.summaries)
        for record in initial:
            item = candidate_from_memory(record, mandatory=record.kind == 'term' or bool(record.content.get('mandatory')))
            candidates[item.id] = item
        nodes = {}
        edges = []
        unresolved = []
        for ref in snapshot.refs:
            if not self.graph.valid(ref):
                continue
            item = self.graph.get(ref)
            match = scope_match(replace(item.scope, document_id=None) if game and not (isinstance(item, GraphNode) and item.kind == "memory") else item.scope, occurrence)
            if match == 'unknown':
                unresolved.append('scope_unknown:' + item.id)
            if match != 'match':
                continue
            if isinstance(item, GraphNode):
                if item.kind == 'memory' and MemoryRef(**item.attributes['memory_ref']) not in by_ref:
                    continue
                nodes[ref] = item
            elif all(self._source_allowed(occurrence, e, game) for e in item.evidence):
                edges.append(item)
        edges = [e for e in edges if e.source in nodes and e.target in nodes]
        outgoing = {}
        members = {}
        reverse = {}
        for edge in edges:
            if game and edge.relation in {'DEFINES', 'MENTIONS', 'SPOKEN_BY'}:
                reverse.setdefault(edge.target, []).append(edge)
            if self.graph.relation_type(edge.relation).traversable and (not game or edge.relation not in {'IN_BLOCK', 'PREVIOUS', 'CONTINUES'}):
                outgoing.setdefault(edge.source, []).append(edge)
            if edge.relation == 'IN_BLOCK':
                members.setdefault(edge.target, []).append(edge)
        starts = [r for r, n in nodes.items() if n.kind == 'occurrence' and n.attributes['occurrence_id'] == occurrence.occurrence_id]
        if len(starts) != 1:
            raise ValueError('Snapshot needs exactly one current occurrence node')
        current_blocks = [nodes[e.target] for e in edges if e.source == starts[0] and e.relation == 'IN_BLOCK']
        if not game and not any(b.attributes.get('order_trusted') for b in current_blocks):
            candidates = {k: c for k, c in candidates.items() if c.category not in {'translation', 'summary'}}
            unresolved.append('untrusted_order:local_target_memory_disabled')
        queue = [(starts[0], (), (), 0)]
        visited = set()
        reached_occurrences = {}
        while queue and len(visited) < self.config.max_nodes:
            ref, path, labels, depth = queue.pop(0)
            if ref in visited:
                continue
            visited.add(ref)
            node = nodes[ref]
            graph_refs = tuple(dict.fromkeys(path + (ref,)))
            path_sources = []
            for path_ref in graph_refs:
                obj = self.graph.get(path_ref)
                if isinstance(obj, GraphEdge):
                    path_sources.extend(obj.evidence)
                elif obj.kind in {'entity', 'occurrence'}:
                    path_sources.extend(obj.evidence)
            path_sources = tuple(dict.fromkeys(path_sources))
            allowed = all(self._source_allowed(occurrence, e, game) for e in path_sources)
            if allowed and node.kind == 'occurrence' and ref != starts[0]:
                e = node.evidence[0]
                source = self.graph.memory.source(occurrence.project_id, e)
                if source.occurrence_id != occurrence.occurrence_id:
                    item = Candidate('source:' + source.occurrence_id + '@' + str(source.source_version),
                                     'source', source.source, sources=path_sources,
                                     graph_refs=graph_refs, relations=labels)
                    candidates.setdefault(item.id, item)
                    reached_occurrences[source.occurrence_id] = (graph_refs, labels, path_sources)
            if allowed and node.kind == 'entity':
                item = Candidate('entity:' + node.id + '@' + str(node.version), 'entity',
                                 canonical(node.attributes), sources=path_sources,
                                 graph_refs=graph_refs, relations=labels)
                candidates.setdefault(item.id, item)
            if allowed and node.kind == 'memory':
                record = by_ref[MemoryRef(**node.attributes['memory_ref'])]
                item = candidate_from_memory(record, mandatory=record.kind == 'term' or bool(record.content.get('mandatory')),
                                             path=graph_refs, relations=labels)
                item = Candidate(**{**item.__dict__, 'sources': tuple(dict.fromkeys(item.sources + path_sources))})
                candidates[item.id] = item
            if depth >= self.config.max_hops or not allowed:
                continue
            if game and node.kind == 'entity':
                # Definitions first; reverse lookup does not assert an inverse semantic fact.
                incoming = sorted(reverse.get(ref, ()), key=lambda e: (e.relation != 'DEFINES', e.id))
                for edge in incoming[:self.config.max_neighbors]:
                    queue.append((edge.source, graph_refs + (edge.ref,), labels + ('REVERSE_' + edge.relation,), depth + 1))
            links = outgoing.get(ref, ())
            for edge in (links[:self.config.max_neighbors] if game else links):
                label = edge.relation
                if nodes[edge.source].kind == 'entity' and nodes[edge.target].kind == 'entity':
                    if game and any(r.kind == 'edge' and self.graph.get(self.graph.get(r).source).kind == 'entity' and self.graph.get(self.graph.get(r).target).kind == 'entity' for r in path):
                        continue
                    # Preserve the fact itself, not only a path leading to another entity.
                    refs = tuple(dict.fromkeys(graph_refs + (edge.ref, edge.target)))
                    sources = tuple(dict.fromkeys(path_sources + edge.evidence + nodes[edge.target].evidence))
                    if all(self._source_allowed(occurrence, e, game) for e in sources):
                        item = Candidate('relation:' + edge.id + '@' + str(edge.version), 'relation',
                            canonical({'source': node.attributes, 'relation': label,
                                       'target': nodes[edge.target].attributes, 'qualifiers': edge.attributes}),
                            bool(edge.attributes.get('mandatory')), sources=sources, graph_refs=refs,
                            relations=labels + (label,))
                        candidates.setdefault(item.id, item)
                queue.append((edge.target, graph_refs + (edge.ref,), labels + (label,), depth + 1))
            # A technical block is not itself evidence that arbitrary CSV rows belong together.
            if node.kind == 'block' and (node.attributes.get('order_trusted') or
                    any(label in {'REFERS_BACK_TO', 'EXPLAINED_BY', 'CONTINUES'} for label in labels)):
                for edge in members.get(ref, ()):
                    if not node.attributes.get('order_trusted') and node.attributes.get('boundary_origin') == 'technical':
                        cited = {e.occurrence_id for r in graph_refs if r.kind == 'edge'
                                 for e in self.graph.get(r).evidence
                                 if self.graph.get(r).relation in {'REFERS_BACK_TO', 'EXPLAINED_BY', 'CONTINUES'}}
                        if not any(e.occurrence_id in cited for e in nodes[edge.source].evidence):
                            continue
                    queue.append((edge.source, graph_refs + (edge.ref,), labels + ('BLOCK_MEMBER',), depth + 1))
        if queue:
            unresolved.append('graph_truncated')
        if game:
            related = set(reached_occurrences)
            bundle = self.memory.read(occurrence, view, related_ids=related)
            for record in (*bundle.stm, *bundle.ltm, *bundle.summaries):
                path, labels, sources = reached_occurrences.get(record.evidence[0].occurrence_id, ((), (), ()))
                item = candidate_from_memory(record, path=path, relations=labels + ('RELATED_EXAMPLE' if record.kind == 'translation' else 'TOPIC_OVERVIEW',))
                candidates[item.id] = replace(item, sources=tuple(dict.fromkeys(item.sources + sources)))
        for record in ([] if game else eligible):
            if record.kind == 'translation' and record.evidence[0].occurrence_id in reached_occurrences:
                path, labels, sources = reached_occurrences[record.evidence[0].occurrence_id]
                item = candidate_from_memory(record, path=path, relations=labels)
                item = Candidate(**{**item.__dict__, 'sources': tuple(dict.fromkeys(item.sources + sources))})
                candidates.setdefault(item.id, item)
        values = list(candidates.values())
        hard = [c for c in values if c.mandatory]
        soft = [c for c in values if not c.mandatory]
        if len(soft) > self.config.max_candidates:
            unresolved.append('candidates_truncated')
        values = hard + soft[:self.config.max_candidates]
        by_term = {}
        for item in values:
            if item.category == 'term':
                record = by_ref[item.memory_ref]
                by_term.setdefault(record.content['source'], set()).add(record.content['target'])
        conflicts = tuple(k for k, targets in by_term.items() if len(targets) > 1)
        return CandidatePool(occurrence.occurrence_id, occurrence.source_version, view, snapshot_id,
                             tuple(values), conflicts, tuple(dict.fromkeys(unresolved)), resolved, binding_refs)

    def build_context(self, pool: CandidatePool, selected_ids: tuple[str, ...] | None = None) -> ContextPack:
        lookup = {c.id: c for c in pool.candidates}
        ids = selected_ids if selected_ids is not None else tuple(lookup)
        if len(set(ids)) != len(ids) or any(i not in lookup for i in ids):
            raise ValueError('Selection must contain distinct allowed candidate IDs')
        mandatory = [c for c in pool.candidates if c.mandatory]
        selected = list(mandatory)
        def size(items):
            # Includes item identifiers, provenance and labels. This is NOT a tokenizer.
            return len(canonical([asdict(c) for c in items]))
        status = 'conflict' if pool.conflicts else 'ready'
        if size(selected) > self.config.max_characters:
            status = 'budget_conflict'
        else:
            for item_id in ids:
                item = lookup[item_id]
                if not item.mandatory and size(selected + [item]) <= self.config.max_characters:
                    selected.append(item)
        refs = tuple(dict.fromkeys(pool.binding_refs + tuple(r for c in selected for r in c.graph_refs)))
        sources = tuple(dict.fromkeys(tuple(e for c in selected for e in c.sources) +
                    tuple(e for r in pool.binding_refs for e in self.graph.get(r).evidence)))
        manifest = {'occurrence_id': pool.occurrence_id, 'source_version': pool.source_version,
                    'view': asdict(pool.view), 'graph_snapshot': pool.graph_snapshot,
                    'resolved_metadata': pool.resolved_metadata,
                    'source_visibility': 'all' if self.memory.store.run(pool.view.run_id)['mode'] == 'game_resources' else self.config.source_visibility,
                    'items': [asdict(c) for c in selected],
                    'memory_refs': [asdict(c.memory_ref) for c in selected if c.memory_ref],
                    'graph_refs': [asdict(r) for r in refs], 'sources': [asdict(e) for e in sources]}
        return ContextPack(pool, tuple(selected), status, text_hash(canonical(manifest)), manifest, size(selected))

    def update_memory(self, occurrence: Occurrence, view: ReadView, snapshot_id: str, *,
                      flush: bool = False) -> tuple[MemoryRecord, ...]:
        """Produce topic/role drafts using the same visible graph as translation."""
        pool = self.retrieve(occurrence, view, snapshot_id)
        pack = self.build_context(pool)
        if pack.status != 'ready':
            raise ValueError('Memory update needs a ready context pack')
        effective = replace(occurrence, metadata={**occurrence.metadata, **pool.resolved_metadata})
        source_items = [c for c in pool.candidates if c.category == 'source' and any(
            r.kind == 'node' and r.id == pool.resolved_metadata.get('topic_id') for r in c.graph_refs)]
        sources = tuple(dict.fromkeys(e for c in source_items for e in c.sources))
        # Include current source: after commit, a topic update must cover that new TM.
        sources = tuple(dict.fromkeys(sources + (Evidence(occurrence.occurrence_id, occurrence.source_version, occurrence.source),)))
        memory_manifest = {**pack.manifest, 'graph_refs': [asdict(r) for r in dict.fromkeys(
            pool.binding_refs + tuple(r for c in source_items for r in c.graph_refs))],
            'sources': [asdict(e) for e in sources]}
        summary = self.memory.summarize_topic(effective, view, flush=flush, sources=sources,
                                              context_manifest=memory_manifest)
        style = self.memory.extract_style(effective, view, context_manifest=pack.manifest)
        return tuple(r for r in (summary, style) if r is not None)

    def commit(self, occurrence: Occurrence, target: str, check: TranslationCheck, pack: ContextPack,
               *, expected_version: int = 0):
        """Single-thread run: revalidate the actual input before delegating the TM transaction."""
        if pack.status != 'ready' or check.context_hash != pack.context_hash:
            raise ValueError('Check must bind a ready context pack')
        if text_hash(canonical(pack.manifest)) != pack.context_hash:
            raise ValueError('Context manifest changed after validation')
        fresh = self.retrieve(occurrence, pack.pool.view, pack.pool.graph_snapshot)
        fresh_lookup = {c.id: c for c in fresh.candidates}
        if fresh.conflicts or any(fresh_lookup.get(c.id) != c for c in pack.selected):
            raise ValueError('Context is stale; rebuild and recheck')
        rebuilt = self.build_context(fresh, tuple(c.id for c in pack.selected))
        if rebuilt.context_hash != pack.context_hash:
            raise ValueError('Context changed; rebuild and recheck')
        return self.graph.memory.commit_translation(occurrence, pack.pool.view.run_id, target, check,
                    used=tuple(c.memory_ref for c in pack.selected if c.memory_ref), view=pack.pool.view,
                    expected_version=expected_version,
                    context_sources=tuple(dict.fromkeys(
                        tuple(Evidence(**e) for e in pack.manifest['sources']) +
                        tuple(e for c in pack.selected for ref in c.graph_refs
                              for e in self.graph.get(ref).evidence))),
                    context_manifest=pack.manifest)
