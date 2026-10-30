"""Automatic entity discovery, catalog resolution and separately reviewed graph writes."""
import json
from dataclasses import asdict
from localization_agent.memory import MemoryService
from localization_agent.memory.service import _tokens
from localization_agent.schemas.benchmark import BuildResult
from localization_agent.schemas.memory import Evidence, Scope, text_hash
from localization_agent.schemas.graph import GraphNode, GraphEdge, GraphRef
from .graph import GraphStore


class AutoGraphBuilder:
    def __init__(self, graph: GraphStore, memory: MemoryService, *, reviewer: MemoryService, catalog_limit: int = 80):
        if graph.memory is not memory.store or graph.memory is not reviewer.store:
            raise ValueError('Builder, reviewer and graph must share a store')
        if catalog_limit < 1:
            raise ValueError('Positive catalog_limit required')
        self.catalog_limit = catalog_limit
        self.graph, self.memory, self.reviewer = graph, memory, reviewer

    def build(self, texts: tuple[GraphRef, ...]) -> BuildResult:
        if not texts:
            raise ValueError('Nonempty source batch required')
        nodes = [self.graph.get(r) for r in texts]
        if any(n.kind != 'occurrence' or not self.graph.valid(n.ref) for n in nodes):
            raise ValueError('Batch needs valid occurrence nodes')
        first = nodes[0].scope
        if any((n.scope.project_id, n.scope.source_lang, n.scope.target_lang) !=
               (first.project_id, first.source_lang, first.target_lang) for n in nodes):
            raise ValueError('Mixed projects/directions in batch')
        scope = Scope(first.project_id, source_lang=first.source_lang, target_lang=first.target_lang)
        sources = {n.attributes['occurrence_id']: self.graph.memory.source(scope.project_id, n.evidence[0]) for n in nodes}
        text_nodes = {n.attributes['occurrence_id']: n for n in nodes}
        rows = [{'id': o.occurrence_id, 'source_text': o.source,
                 'text_type': o.metadata.get('text_type', 'unknown'),
                 'context': o.metadata.get('native_context', '')} for o in sources.values()]
        catalog = {}
        for row in self.graph.db.execute("SELECT id,MAX(version) AS version FROM graph_items WHERE kind='node' AND project=? GROUP BY id", (scope.project_id,)):
            n = self.graph.get(GraphRef('node', row['id'], row['version']))
            if n.kind == 'entity' and n.scope == scope and self.graph.valid(n.ref):
                catalog[n.id] = n
        query = _tokens(' '.join(o.source + ' ' + o.metadata.get('native_context', '') for o in sources.values()))
        selected = sorted(catalog.values(), key=lambda n: (-len(query & _tokens(n.attributes.get('name', ''))), n.id))[:self.catalog_limit]
        catalog = {n.id: n for n in selected}
        data_rows = json.dumps(rows, ensure_ascii=False)
        data_catalog = json.dumps([{'id': n.id, **n.attributes} for n in catalog.values()], ensure_ascii=False)
        raw, call = self.memory._call('discover_graph', 'discover_graph.txt', rows=data_rows, catalog=data_catalog)
        proposal = json.loads(raw)
        if not isinstance(proposal, dict) or set(proposal) != {'entities', 'relations'} or any(
                not isinstance(proposal[k], list) for k in proposal):
            raise ValueError('Graph discovery needs entities and relations arrays')

        def evidence(values):
            if not isinstance(values, list) or not values:
                raise ValueError('Locatable source evidence required')
            refs, support = [], []
            for e in values:
                if not isinstance(e, dict) or set(e) != {'id', 'field', 'quote'} or e['id'] not in sources:
                    raise ValueError('Evidence must identify an allowed row')
                o = sources[e['id']]
                value = o.source if e['field'] == 'source' else o.metadata.get('native_context', '') if e['field'] == 'context' else ''
                if not isinstance(e['quote'], str) or not e['quote'] or e['quote'] not in value:
                    raise ValueError('Evidence quote is not in the indicated source field')
                # Native field support is stored alongside text evidence, without forging a quote.
                refs.append(Evidence(o.occurrence_id, o.source_version, e['quote'] if e['field'] == 'source' else o.source))
                support.append(e)
            return tuple(dict.fromkeys(refs)), support

        entity_plans, relation_plans, expected = {}, {}, set()
        for e in proposal['entities']:
            if not isinstance(e, dict) or set(e) != {'key', 'name', 'entity_type', 'reuse_id', 'evidence'}:
                raise ValueError('Invalid entity proposal fields')
            key = e['key']
            if not isinstance(key, str) or not key or key in entity_plans or not isinstance(e['name'], str) or not e['name'].strip():
                raise ValueError('Distinct entity keys and names required')
            if e['entity_type'] not in {'character', 'skill', 'item', 'location', 'organization', 'quest'}:
                raise ValueError('Unsupported entity type')
            reuse = e['reuse_id']
            if reuse is not None and (reuse not in catalog or catalog[reuse].attributes['entity_type'] != e['entity_type']):
                raise ValueError('Reuse must identify a compatible catalog entity')
            ev, support = evidence(e['evidence'])
            item_id = reuse or 'auto:entity:' + text_hash(json.dumps([scope.project_id, scope.source_lang,
                scope.target_lang, sorted((x.occurrence_id, x.source_version) for x in ev), key, e['name']], ensure_ascii=False))
            node = catalog[reuse] if reuse else GraphNode(item_id, 'entity', scope,
                {'name': e['name'], 'entity_type': e['entity_type'], 'support': support, 'model_call': call}, ev)
            entity_plans[key] = node
            expected.add('entity:' + key)

        def endpoint(value):
            if not isinstance(value, str) or ':' not in value:
                raise ValueError('Typed endpoint required')
            prefix, key = value.split(':', 1)
            if prefix == 'text' and key in text_nodes:
                return text_nodes[key]
            if prefix == 'entity' and key in entity_plans:
                return entity_plans[key]
            raise ValueError('Endpoint outside this proposal')

        for r in proposal['relations']:
            if not isinstance(r, dict) or set(r) != {'key', 'source', 'target', 'relation', 'conditions', 'evidence'}:
                raise ValueError('Invalid relation fields')
            if not isinstance(r['key'], str) or not r['key'] or r['key'] in relation_plans:
                raise ValueError('Distinct relation key required')
            if r['relation'] not in {'MENTIONS', 'DEFINES', 'SPOKEN_BY', 'ADDRESSED_TO', 'MEMBER_OF', 'LOCATED_IN', 'COUNTERS'}:
                raise ValueError('Unsupported extracted relation')
            left, right = endpoint(r['source']), endpoint(r['target'])
            definition = self.graph.relation_type(r['relation'])
            if left.kind not in definition.source_kinds or right.kind not in definition.target_kinds:
                raise ValueError('Wrong relation endpoint kinds')
            for n, allowed in ((left, definition.source_entity_types), (right, definition.target_entity_types)):
                if allowed and n.attributes.get('entity_type') not in allowed:
                    raise ValueError('Wrong entity subtype')
            if r['relation'] in {'SPOKEN_BY', 'ADDRESSED_TO'} and right.attributes['entity_type'] != 'character':
                raise ValueError('Dialogue participant must be a character')
            conditions = r['conditions']
            if not isinstance(conditions, dict) or any(not isinstance(k, str) or not isinstance(v, list)
                    or not v or any(not isinstance(x, str) or not x for x in v) for k, v in conditions.items()):
                raise ValueError('Conditions must map metadata keys to nonempty string lists')
            ev, support = evidence(r['evidence'])
            if left.kind == 'occurrence' and not any(x.occurrence_id == left.attributes['occurrence_id'] for x in ev):
                raise ValueError('Text relation evidence must include its text endpoint')
            # Native triggers must not become unconditional facts, even after model review.
            requirements = {sources[e.occurrence_id].metadata.get('requirements') for e in ev} - {None}
            if len(requirements) > 1:
                raise ValueError('Relation cites incompatible native trigger contexts')
            if requirements:
                conditions = {**conditions, 'requirements': sorted(requirements)}
                r = {**r, 'conditions': conditions}
            relation_plans[r['key']] = (r, ev, support)
            expected.add('relation:' + r['key'])
        reviewed, review_call = self.reviewer._call('review_graph', 'review_graph.txt',
            rows=data_rows, catalog=data_catalog, proposal=raw)
        review = json.loads(reviewed)
        decisions = review.get('decisions') if isinstance(review, dict) else None
        if not isinstance(decisions, dict) or set(decisions) != expected or any(
                not isinstance(d, dict) or d.get('status') not in {'accepted', 'rejected', 'unknown'}
                or not isinstance(d.get('reason'), str) or not d['reason'].strip() for d in decisions.values()):
            raise ValueError('Reviewer must decide every proposed item with a reason')
        # No graph writes before both model outputs and all contracts are validated.
        saved, edges = {}, []
        for key, node in entity_plans.items():
            if decisions['entity:' + key]['status'] == 'accepted':
                saved[key] = node if node.version else self.graph.put_node(node, expected_version=self.graph.latest('node', node.id))
        for key, (r, ev, support) in relation_plans.items():
            if decisions['relation:' + key]['status'] != 'accepted':
                continue
            if any(value.startswith('entity:') and value.split(':', 1)[1] not in saved for value in (r['source'], r['target'])):
                decisions['relation:' + key] = {'status': 'unknown', 'reason': 'Endpoint identity was not accepted'}
                continue
            def ref(value):
                prefix, name = value.split(':', 1)
                return (saved[name] if prefix == 'entity' else text_nodes[name]).ref
            identity = [ref(r['source']).__dict__, ref(r['target']).__dict__, r['relation'], r['conditions'], support]
            edge_id = 'auto:edge:' + text_hash(json.dumps(identity, sort_keys=True))
            edge = GraphEdge(edge_id, r['relation'], ref(r['source']), ref(r['target']),
                Scope(scope.project_id, conditions=r['conditions'], source_lang=scope.source_lang, target_lang=scope.target_lang),
                ev, {'support': support, 'model_call': call, 'review_call': review_call},
                status='approved', reviewer='model:' + review_call)
            edges.append(self.graph.put_edge(edge, expected_version=self.graph.latest('edge', edge_id)).ref)
        return BuildResult(tuple(n.ref for n in saved.values()), tuple(edges), decisions, call, review_call)
