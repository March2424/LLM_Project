"""Snapshot-local indexes. Names are lookup keys, never identity equivalence claims."""
import math
import json
import re
from collections import defaultdict

from localization_agent.schemas.graph import GraphEdge
from localization_agent.schemas.memory import Occurrence
from localization_agent.memory.service import _tokens


def normalize_name(text: str) -> str:
    return ' '.join(text.casefold().split())


class GameIndex:
    def __init__(self, graph, snapshot_id):
        self.snapshot = graph.snapshot(snapshot_id)
        self.objects = {ref: graph.get(ref) for ref in self.snapshot.refs}
        invalid = {(r['kind'], r['id'], r['version']) for r in graph.db.execute('SELECT * FROM graph_invalidations')}
        self.sources = {o.occurrence_id: o for row in graph.db.execute(
            'SELECT s.payload FROM sources s WHERE s.project=? AND s.version=(SELECT MAX(version) '
            'FROM sources WHERE project=s.project AND id=s.id)', (self.snapshot.project_id,))
            for o in [Occurrence(**json.loads(row[0]))]}
        validity = {}
        def valid(ref):
            if ref in validity:
                return validity[ref]
            obj = self.objects.get(ref)
            okay = obj is not None and (ref.kind, ref.id, ref.version) not in invalid
            if okay:
                for evidence in obj.evidence:
                    source = self.sources.get(evidence.occurrence_id)
                    if (source is None or source.source_version != evidence.source_version
                            or not evidence.quote or evidence.quote not in source.source
                            or (obj.scope.document_id is not None and source.document_id != obj.scope.document_id)):
                        okay = False
                        break
            if okay and isinstance(obj, GraphEdge):
                okay = obj.status == 'approved' and valid(obj.source) and valid(obj.target)
            elif okay and obj.kind == 'memory':
                okay = graph.valid(ref)
            validity[ref] = okay
            return okay
        self.objects = {ref: obj for ref,obj in self.objects.items() if valid(ref)}
        self.nodes = {ref:obj for ref,obj in self.objects.items() if ref.kind=='node'}
        self.edges = {ref:obj for ref,obj in self.objects.items() if ref.kind=='edge'}
        self.names, self.incoming, self.outgoing = defaultdict(list), defaultdict(list), defaultdict(list)
        self.occurrences, self.postings, self.tokens = {}, defaultdict(set), {}
        for ref,node in self.nodes.items():
            if node.kind=='entity' and node.attributes.get('name'):
                key = (node.attributes['entity_type'], normalize_name(node.attributes['name']))
                self.names[key].append(ref)
            elif node.kind=='occurrence':
                source = self.sources[node.attributes['occurrence_id']]
                self.occurrences[source.occurrence_id] = ref
                self.tokens[ref] = _tokens(source.source)
                for word in self.tokens[ref]:self.postings[word].add(ref)
        for refs in self.names.values():refs.sort(key=lambda r:r.id)
        self.name_labels={key[1] for key in self.names}
        for edge in self.edges.values():
            self.outgoing[edge.source].append(edge)
            self.incoming[edge.target].append(edge)

    def lexical_score(self, query, ref):
        words = _tokens(query) if isinstance(query,str) else query
        total = len(self.occurrences)
        return sum(math.log1p(total/(1+len(self.postings[word]))) for word in words & self.tokens.get(ref,set())) / math.sqrt(max(1,len(self.tokens.get(ref,set()))))

    def lexical(self, query, limit):
        words = _tokens(query)
        matches = set().union(*(self.postings.get(word,set()) for word in words)) if words else set()
        return sorted(matches,key=lambda ref:(-self.lexical_score(words,ref),ref.id))[:limit]

    def seed_groups(self, occurrence, limit):
        current = self.occurrences[occurrence.occurrence_id]
        priorities = {'DEFINES':0,'MENTIONS':1,'SPOKEN_BY':2,'ADDRESSED_TO':3}
        groups = {}
        for edge in self.outgoing[current]:
            target = self.nodes[edge.target]
            if edge.relation in priorities and target.kind=='entity' and target.attributes.get('name'):
                key=(target.attributes['entity_type'],normalize_name(target.attributes['name']))
                groups[key]=min(groups.get(key,9),priorities[edge.relation])
        query=normalize_name(occurrence.source)
        for key in self.names:
            if key not in groups and re.search(r'(?<!\w)'+re.escape(key[1])+r'(?!\w)',query):groups[key]=4
        keys=sorted(groups,key=lambda k:(groups[k],-len(k[1]),k))[:limit]
        return [(key,self.names[key]) for key in keys]
