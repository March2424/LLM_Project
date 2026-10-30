"""Offline CSV -> approved graph -> DelTA candidates -> checked TM demonstration."""
import argparse
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from localization_agent.data import CSVConfig, import_csv
from localization_agent.memory import MemoryService, MemoryStore
from localization_agent.retrieval import GraphConfig, GraphRetriever, GraphStore
from localization_agent.schemas.graph import GraphEdge, GraphNode
from localization_agent.schemas.memory import Evidence, Scope, TranslationCheck, text_hash


def build_demo(store):
    graph = GraphStore(store)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'script.csv'
        path.write_text('id,source,reference\nintro,Ember Ward protects the bridge.,DO NOT IMPORT\nfiller,The shop is closed.,DO NOT IMPORT\ncurrent,If the ward fails retreat.,DO NOT IMPORT\n', encoding='utf-8')
        imported = import_csv(path, 'demo', graph, CSVConfig('script', max_rows=1))
    old, _, current = imported.occurrences
    scope = Scope('demo', current.document_id)
    old_e = Evidence(old.occurrence_id, 1, 'Ember Ward')
    current_e = Evidence(current.occurrence_id, 1, 'ward')
    term = store.approve(store.draft('term', scope, {'source': 'Ember Ward', 'target': '余烬护盾'}, (old_e,)).ref, 'demo-human')
    store.create_snapshot('knowledge', 'demo')
    store.start_run('run', 'demo', current.document_id, 'en', 'zh', 'knowledge')
    target = '余烬护盾保护桥梁。'
    store.commit_translation(old, 'run', target, TranslationCheck(old.occurrence_id, 1, text_hash(target), 'offline-empty', 'demo-check', True))
    skill = graph.put_node(GraphNode('skill:ward', 'entity', scope, {'entity_type': 'skill', 'name': 'Ember Ward'}, (old_e,)))
    proxy = graph.put_node(GraphNode('memory:ward', 'memory', scope, {'memory_ref': asdict(term.ref)}))
    node_rows = graph.db.execute("SELECT id,version FROM graph_items WHERE kind='node'").fetchall()
    from localization_agent.schemas.graph import GraphRef
    node = next(graph.get(GraphRef('node', r['id'], r['version'])) for r in node_rows
                if graph.get(GraphRef('node', r['id'], r['version'])).attributes.get('occurrence_id') == current.occurrence_id)
    edges = [
        GraphEdge('mention', 'MENTIONS', node.ref, skill.ref, scope, (current_e,)),
        GraphEdge('term-link', 'HAS_TERM', skill.ref, proxy.ref, scope, (old_e,)),
        GraphEdge('reference', 'REFERS_BACK_TO', imported.blocks[-1].ref, imported.blocks[0].ref,
                  scope, (current_e, old_e)),
    ]
    for edge in edges:
        graph.approve(graph.put_edge(edge).ref, 'demo-human')
    graph.create_snapshot('graph-v1', 'demo')
    retriever = GraphRetriever(graph, MemoryService(store), GraphConfig(max_characters=20000))
    return graph, retriever, imported, current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=':memory:', help='Use a fresh database path for a persistent demo')
    args = parser.parse_args()
    store = MemoryStore(args.db)
    try:
        graph, retriever, imported, current = build_demo(store)
        pool = retriever.retrieve(current, store.view('run'), 'graph-v1')
        pack = retriever.build_context(pool)
        target = '如果护盾失效，就撤退。'
        check = TranslationCheck(current.occurrence_id, 1, text_hash(target), pack.context_hash, 'offline-fixture', True)
        saved = retriever.commit(current, target, check, pack)
        print(json.dumps({'mode': 'offline-fixture; no translation quality claim', 'warnings': imported.warnings,
                          'status': pack.status, 'candidates': [(c.category, c.relations) for c in pool.candidates],
                          'saved': asdict(saved.ref), 'characters': pack.characters}, ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == '__main__':
    main()
