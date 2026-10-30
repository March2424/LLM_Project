"""Small versioned SQLite graph sharing a connection and sources with M2."""
import json
from dataclasses import asdict, replace

from localization_agent.memory.store import MemoryStore, VersionConflict
from localization_agent.schemas.memory import Evidence, MemoryRef, Scope, scope_match
from localization_agent.schemas.graph import GraphEdge, GraphNode, GraphRef, GraphSnapshot, RelationType


def encode(value):
    return json.dumps(asdict(value), ensure_ascii=False, sort_keys=True)


class GraphStore:
    def __init__(self, memory: MemoryStore):
        self.memory = memory
        self.db = memory.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS graph_types (name TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS graph_items (
                kind TEXT, id TEXT, version INTEGER, project TEXT, payload TEXT NOT NULL,
                PRIMARY KEY(kind,id,version));
            CREATE TABLE IF NOT EXISTS graph_snapshots (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS graph_invalidations (
                kind TEXT,id TEXT,version INTEGER,reason TEXT, PRIMARY KEY(kind,id,version));
        ''')
        definitions = [
            ('IN_DOCUMENT', ('scene', 'block'), ('document',), False),
            ('IN_SCENE', ('block',), ('scene',), False),
            ('IN_BLOCK', ('occurrence',), ('block',), True),
            ('PREVIOUS', ('block',), ('block',), True),
            ('DEFINES', ('occurrence',), ('entity',), True),
            ('SPOKEN_BY', ('occurrence',), ('entity',), True),
            ('ADDRESSED_TO', ('occurrence',), ('entity',), True),
            ('MENTIONS', ('occurrence', 'block'), ('entity',), True),
            ('REFERS_BACK_TO', ('block',), ('block',), True),
            ('EXPLAINED_BY', ('block',), ('block',), True),
            ('CONTINUES', ('block',), ('block',), True),
            ('HAS_TERM', ('entity',), ('memory',), True),
            ('HAS_STYLE', ('entity',), ('memory',), True),
            ('HAS_TRANSLATION', ('occurrence',), ('memory',), True),
            ('HAS_SUMMARY', ('block', 'scene', 'document'), ('memory',), True),
        ]
        for name, sources, targets, walk in definitions:
            self.register_type(RelationType(name, sources, targets, name, traversable=walk))
        for name, left, right in [('MEMBER_OF', ('character',), ('organization',)),
                                  ('LOCATED_IN', (), ('location',)),
                                  ('COUNTERS', ('skill', 'item'), ('skill', 'item'))]:
            self.register_type(RelationType(name, ('entity',), ('entity',), name, left, right))

    def register_type(self, definition: RelationType):
        """Definitions are immutable; changed semantics require a new relation name."""
        if not definition.name or not definition.source_kinds or not definition.target_kinds:
            raise ValueError('Relation type needs a name and endpoint kinds')
        payload = encode(definition)
        old = self.db.execute('SELECT payload FROM graph_types WHERE name=?', (definition.name,)).fetchone()
        if old and old[0] != payload:
            raise VersionConflict('Relation type already has another definition')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO graph_types VALUES (?,?)', (definition.name, payload))

    def relation_type(self, name):
        row = self.db.execute('SELECT payload FROM graph_types WHERE name=?', (name,)).fetchone()
        if row is None:
            raise KeyError(name)
        return RelationType(**json.loads(row[0]))

    def get(self, ref: GraphRef) -> GraphNode | GraphEdge:
        row = self.db.execute('SELECT payload FROM graph_items WHERE kind=? AND id=? AND version=?',
                              (ref.kind, ref.id, ref.version)).fetchone()
        if row is None:
            raise KeyError(ref)
        data = json.loads(row[0])
        data['scope'] = Scope(**data['scope'])
        data['evidence'] = tuple(Evidence(**e) for e in data['evidence'])
        if ref.kind == 'edge':
            data['source'], data['target'] = GraphRef(**data['source']), GraphRef(**data['target'])
            return GraphEdge(**data)
        return GraphNode(**data)

    def latest(self, kind: str, item_id: str) -> int:
        return self.db.execute('SELECT COALESCE(MAX(version),0) FROM graph_items WHERE kind=? AND id=?',
                               (kind, item_id)).fetchone()[0]

    def _evidence_valid(self, scope, evidence):
        for e in evidence:
            source = self.memory.source(scope.project_id, e)
            latest = self.db.execute('SELECT MAX(version) FROM sources WHERE project=? AND id=?',
                                     (scope.project_id, e.occurrence_id)).fetchone()[0]
            if latest != e.source_version or not e.quote or e.quote not in source.source:
                return False
            if scope.document_id is not None and source.document_id != scope.document_id:
                return False
        return True

    def valid(self, ref: GraphRef) -> bool:
        item = self.get(ref)
        if self.db.execute('SELECT 1 FROM graph_invalidations WHERE kind=? AND id=? AND version=?',
                           (ref.kind, ref.id, ref.version)).fetchone():
            return False
        if not self._evidence_valid(item.scope, item.evidence):
            return False
        if isinstance(item, GraphEdge):
            return item.status == 'approved' and self.valid(item.source) and self.valid(item.target)
        if item.kind == 'memory':
            memory_ref = MemoryRef(**item.attributes['memory_ref'])
            record = self.memory.get(memory_ref)
            return record.status == 'approved' and self.memory.is_valid(memory_ref)
        return True

    def _save(self, item, kind, expected):
        if not self.db.in_transaction:
            self.db.execute('BEGIN IMMEDIATE')
        actual = self.latest(kind, item.id)
        if actual:
            previous = self.get(GraphRef(kind, item.id, actual))
            if replace(item, version=actual) == previous:
                return previous
            if previous.scope.project_id != item.scope.project_id:
                raise ValueError('Graph IDs cannot move between projects')
        if actual != expected:
            raise VersionConflict(f'{item.id}: expected {expected}, found {actual}')
        item = replace(item, version=actual + 1)
        self.db.execute('INSERT INTO graph_items VALUES (?,?,?,?,?)',
                        (kind, item.id, item.version, item.scope.project_id, encode(item)))
        return item

    def put_node(self, node: GraphNode, *, expected_version: int = 0) -> GraphNode:
        if node.kind not in {'document', 'scene', 'block', 'occurrence', 'entity', 'memory'}:
            raise ValueError('Unknown node kind')
        if node.kind in {'entity', 'occurrence', 'block', 'scene'} and not node.evidence:
            raise ValueError('Node needs source evidence')
        if node.kind == 'entity' and not node.attributes.get('entity_type'):
            raise ValueError('Entity needs an entity_type; custom types are allowed')
        if not self._evidence_valid(node.scope, node.evidence):
            raise ValueError('Stale or ungrounded node evidence')
        if node.kind == 'memory':
            record = self.memory.get(MemoryRef(**node.attributes['memory_ref']))
            if record.scope != node.scope or record.status != 'approved' or not self.memory.is_valid(record.ref):
                raise ValueError('Memory proxy must refer to approved memory with identical scope')
        with self.db:
            return self._save(node, 'node', expected_version)

    def put_edge(self, edge: GraphEdge, *, expected_version: int = 0) -> GraphEdge:
        definition = self.relation_type(edge.relation)
        left, right = self.get(edge.source), self.get(edge.target)
        if not isinstance(left, GraphNode) or not isinstance(right, GraphNode):
            raise ValueError('Edge endpoints must be nodes')
        if left.kind not in definition.source_kinds or right.kind not in definition.target_kinds:
            raise ValueError('Relation endpoint kinds do not match its definition')
        for node, allowed in ((left, definition.source_entity_types), (right, definition.target_entity_types)):
            if node.scope.project_id != edge.scope.project_id:
                raise ValueError('Cross-project edge')
            if allowed and node.attributes.get('entity_type') not in allowed:
                raise ValueError('Entity subtype does not match relation definition')
        if edge.status not in {'draft', 'approved'} or (edge.status == 'approved' and not edge.reviewer):
            raise ValueError('Approved relation needs an explicit reviewer')
        if not edge.evidence or not self._evidence_valid(edge.scope, edge.evidence):
            raise ValueError('Relation needs current, locatable evidence')
        if any(not self.valid(n.ref) for n in (left, right)):
            raise ValueError('Invalid endpoint')
        memory_kinds = {'HAS_TERM': 'term', 'HAS_STYLE': 'style', 'HAS_TRANSLATION': 'translation', 'HAS_SUMMARY': 'summary'}
        if edge.relation in memory_kinds:
            record = self.memory.get(MemoryRef(**right.attributes['memory_ref']))
            if record.kind != memory_kinds[edge.relation]:
                raise ValueError('Memory kind does not match link')
        if edge.relation in {'REFERS_BACK_TO', 'EXPLAINED_BY', 'CONTINUES'}:
            ids = {e.occurrence_id for e in edge.evidence}
            if any(not ids.intersection(e.occurrence_id for e in n.evidence) for n in (left, right)):
                raise ValueError('Discourse relation needs evidence from both blocks')
        if edge.relation == 'MENTIONS' and not any(e in left.evidence for e in edge.evidence):
            # A mention may quote a substring of the occurrence, not the whole sentence.
            if not any(e.occurrence_id == s.occurrence_id and e.source_version == s.source_version
                       for e in edge.evidence for s in left.evidence):
                raise ValueError('Mention evidence must identify its source node')
        with self.db:
            return self._save(edge, 'edge', expected_version)

    def approve(self, ref: GraphRef, reviewer: str) -> GraphEdge:
        edge = self.get(ref)
        if not isinstance(edge, GraphEdge) or edge.status != 'draft' or not reviewer.strip():
            raise ValueError('Approve a draft edge with a reviewer')
        return self.put_edge(replace(edge, status='approved', reviewer=reviewer), expected_version=edge.version)

    def create_snapshot(self, snapshot_id: str, project_id: str) -> GraphSnapshot:
        rows = self.db.execute('SELECT kind,id,MAX(version) AS version FROM graph_items WHERE project=? GROUP BY kind,id',
                               (project_id,)).fetchall()
        refs = [GraphRef(r['kind'], r['id'], r['version']) for r in rows]
        refs = [r for r in refs if self.valid(r)]
        nodes = {r for r in refs if r.kind == 'node'}
        refs = [r for r in refs if r.kind == 'node' or
                (self.get(r).source in nodes and self.get(r).target in nodes)]
        snapshot = GraphSnapshot(snapshot_id, project_id, tuple(refs))
        with self.db:
            self.db.execute('INSERT INTO graph_snapshots VALUES (?,?)', (snapshot_id, encode(snapshot)))
        return snapshot

    def snapshot(self, snapshot_id: str) -> GraphSnapshot:
        row = self.db.execute('SELECT payload FROM graph_snapshots WHERE id=?', (snapshot_id,)).fetchone()
        if row is None:
            raise KeyError(snapshot_id)
        data = json.loads(row[0])
        data['refs'] = tuple(GraphRef(**r) for r in data['refs'])
        return GraphSnapshot(**data)

    def invalidate(self, ref: GraphRef, reason: str):
        """Invalidate references and TM generated with them; keep audit payloads."""
        self.get(ref)
        affected = {ref}
        rows = self.db.execute("SELECT kind,id,version FROM graph_items WHERE kind='edge'").fetchall()
        for row in rows:
            edge = self.get(GraphRef(*row))
            if edge.source == ref or edge.target == ref:
                affected.add(edge.ref)
        with self.db:
            for item in affected:
                self.db.execute('INSERT OR IGNORE INTO graph_invalidations VALUES (?,?,?,?)',
                                (item.kind, item.id, item.version, reason))
            for row in self.db.execute('SELECT id,version,payload FROM records').fetchall():
                payload = json.loads(row['payload'])
                manifest = payload['content'].get('context_manifest', {})
                used = {GraphRef(**r) for r in manifest.get('graph_refs', [])}
                if used & affected:
                    self.memory._invalidate(MemoryRef(row['id'], row['version']), reason)
