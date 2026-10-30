"""Offline fixture: unordered skill/character files, graph lookup and five memory categories."""
import json
from localization_agent.memory import MemoryStore, MemoryService, MemoryConfig
from localization_agent.models.protocols import ModelReply
from localization_agent.retrieval import GraphStore, GraphRetriever, GraphConfig
from localization_agent.schemas.graph import GraphNode, GraphEdge
from localization_agent.schemas.memory import Occurrence, Evidence, Scope, TranslationCheck, text_hash


def main():
    store = MemoryStore()
    try:
        graph = GraphStore(store)
        definition = Occurrence('demo', 'skills.csv', 'skill-description', 30, 'Ember Ward absorbs damage.', metadata={'topic_id': 'ward'})
        line = Occurrence('demo', 'hero.csv', 'hero-line', 0, 'Use Ember Ward!', metadata={'topic_id': 'ward', 'speaker_id': 'hero'})
        nodes = []
        for o in (definition, line):
            store.put_source(o)
            nodes.append(graph.put_node(GraphNode(o.occurrence_id, 'occurrence', Scope('demo', o.document_id),
                {'occurrence_id': o.occurrence_id}, (Evidence(o.occurrence_id, 1, o.source),))))
        entity = graph.put_node(GraphNode('skill:ward', 'entity', Scope('demo'),
            {'entity_type': 'skill', 'name': 'Ember Ward'}, nodes[0].evidence))
        for node, rel in zip(nodes, ('DEFINES', 'MENTIONS')):
            draft = graph.put_edge(GraphEdge(rel, rel, node.ref, entity.ref, Scope('demo'), node.evidence))
            graph.approve(draft.ref, 'offline-fixture')
        term = store.draft('term', Scope('demo'), {'source': 'Ember Ward', 'target': '余烬护盾'}, nodes[0].evidence)
        store.approve(term.ref, 'offline-fixture')
        style = store.draft('style', Scope('demo', conditions={'speaker_id': ('hero',)}), {'note': '简短直接。'}, nodes[1].evidence)
        store.approve(style.ref, 'offline-fixture')
        store.create_snapshot('knowledge', 'demo')
        graph.create_snapshot('graph', 'demo')
        store.start_run('run', 'demo', None, 'en', 'zh', 'knowledge', mode='game_resources')
        memory = MemoryService(store, MemoryConfig(summary_step=1),
            chat=lambda task, prompt: ModelReply('offline fixture: ' + task, 'offline-fixture'))
        retrieval = GraphRetriever(graph, memory, GraphConfig(max_characters=50000))
        pack = retrieval.build_context(retrieval.retrieve(definition, store.view('run'), 'graph'))
        target = '余烬护盾吸收伤害。'
        check = TranslationCheck(definition.occurrence_id, 1, text_hash(target), pack.context_hash, 'offline-fixture', True)
        retrieval.commit(definition, target, check, pack)
        summary = memory.summarize(line, store.view('run'), flush=True)
        store.approve(summary.ref, 'offline-fixture')
        pool = retrieval.retrieve(line, store.view('run'), 'graph')
        print(json.dumps({'fixture_only': True, 'candidates': [{'category': c.category, 'text': c.text} for c in pool.candidates],
                          'note': '五类记忆保留；只有一条译例时进入STM，LTM去重后为空。'}, ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == '__main__':
    main()
