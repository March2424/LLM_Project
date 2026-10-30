"""Small-model link proposals over a caller-curated project entity table."""
import json
from dataclasses import asdict
from localization_agent.memory import MemoryService
from localization_agent.schemas.memory import Evidence, Scope, text_hash
from localization_agent.schemas.graph import GraphEdge, GraphRef
from .graph import GraphStore


class EntityLinker:
    def __init__(self, graph: GraphStore, memory: MemoryService):
        if graph.memory is not memory.store:
            raise ValueError('Linker and memory must share a store')
        self.graph, self.memory = graph, memory

    def propose(self, texts: tuple[GraphRef, ...], entities: tuple[GraphRef, ...]) -> tuple[GraphEdge, ...]:
        """No automatic identity merging. Ambiguous mentions may remain unlinked."""
        nodes = {r.id: self.graph.get(r) for r in texts}
        catalog = {r.id: self.graph.get(r) for r in entities}
        if not nodes or not catalog:
            return ()
        if any(n.kind != 'occurrence' or not self.graph.valid(n.ref) for n in nodes.values()) or any(
                n.kind != 'entity' or not self.graph.valid(n.ref) for n in catalog.values()):
            raise ValueError('Supply valid occurrence and entity nodes')
        reply, call = self.memory._call('entity_links', 'entity_links.txt',
            texts=json.dumps([{'id': n.id, 'text': n.evidence[0].quote} for n in nodes.values()], ensure_ascii=False),
            entities=json.dumps([{'id': n.id, **n.attributes} for n in catalog.values()], ensure_ascii=False))
        proposals = json.loads(reply)
        if not isinstance(proposals, list):
            raise ValueError('Link proposals must be an array')
        edges = []
        for p in proposals:
            if (not isinstance(p, dict) or set(p) != {'text_id', 'entity_id', 'relation', 'quote'}
                    or p['text_id'] not in nodes or p['entity_id'] not in catalog
                    or p['relation'] not in {'MENTIONS', 'DEFINES', 'SPOKEN_BY'}
                    or not isinstance(p['quote'], str) or not p['quote']):
                raise ValueError('Invalid entity link proposal')
            left, right = nodes[p['text_id']], catalog[p['entity_id']]
            source = self.graph.memory.source(left.scope.project_id, left.evidence[0])
            if p['quote'] not in source.source or left.scope.project_id != right.scope.project_id:
                raise ValueError('Link evidence does not match its source/project')
            if p['relation'] == 'SPOKEN_BY' and right.attributes.get('entity_type') != 'character':
                raise ValueError('Speaker must be a character')
            scope = self.memory.store.game_scope(source)
            evidence = (Evidence(source.occurrence_id, source.source_version, p['quote']),)
            key = 'proposal:' + text_hash(json.dumps([asdict(left.ref), asdict(right.ref), p], sort_keys=True))
            edges.append(GraphEdge(key, p['relation'], left.ref, right.ref, scope, evidence,
                                   {'model_call': call}))
        # Validate all proposals before writing; still drafts until explicit approval.
        return tuple(self.graph.put_edge(e) for e in edges)
