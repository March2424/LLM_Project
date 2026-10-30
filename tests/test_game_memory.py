import unittest
from dataclasses import replace
from localization_agent.memory import MemoryStore, MemoryService, MemoryConfig
from localization_agent.models.protocols import ModelReply
from localization_agent.schemas.memory import Occurrence, Evidence, Scope, TranslationCheck, text_hash
from localization_agent.schemas.graph import GraphNode, GraphEdge
from localization_agent.retrieval import GraphStore, GraphRetriever, GraphConfig


class GameMemoryTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.addCleanup(self.store.close)
        self.graph = GraphStore(self.store)
        self.scope = Scope('p')
        self.rows = [Occurrence('p', 'skills', 'definition', 90, 'Ward absorbs damage.', metadata={'topic_id': 'ward'}),
                     Occurrence('p', 'hero', 'line', 0, 'Use Ward!', metadata={'topic_id': 'ward'})]
        self.nodes = []
        for o in self.rows:
            self.store.put_source(o)
            self.nodes.append(self.graph.put_node(GraphNode(o.occurrence_id, 'occurrence', Scope('p', o.document_id),
                {'occurrence_id': o.occurrence_id}, (Evidence(o.occurrence_id, 1, o.source),))))
        entity = self.graph.put_node(GraphNode('ward', 'entity', self.scope, {'entity_type': 'skill'}, self.nodes[0].evidence))
        for node, rel in zip(self.nodes, ('DEFINES', 'MENTIONS')):
            self.graph.put_edge(GraphEdge(rel, rel, node.ref, entity.ref, self.scope, node.evidence,
                                          status='approved', reviewer='fixture'))
        self.graph.create_snapshot('g', 'p')
        self.store.create_snapshot('k', 'p')
        self.store.start_run('r', 'p', None, 'en', 'zh', 'k', mode='game_resources')
        self.memory = MemoryService(self.store, MemoryConfig(stm_size=1, ltm_window=1, summary_step=1),
                                    chat=lambda task, prompt: ModelReply('fixture ' + task, 'fixture'))
        self.retriever = GraphRetriever(self.graph, self.memory, GraphConfig(max_characters=50000))

    def commit(self, o, target, run='r'):
        pack = self.retriever.build_context(self.retriever.retrieve(o, self.store.view(run), 'g'))
        check = TranslationCheck(o.occurrence_id, 1, text_hash(target), pack.context_hash, 'fixture', True)
        return self.retriever.commit(o, target, check, pack)

    def test_cross_file_definition_stm_commit_and_run_isolation(self):
        first = self.commit(self.rows[0], '护盾吸收伤害。')
        pool = self.retriever.retrieve(self.rows[1], self.store.view('r'), 'g')
        self.assertTrue(any(c.category == 'source' and c.text == self.rows[0].source for c in pool.candidates))
        self.assertTrue(any(c.memory_ref == first.ref for c in pool.candidates))
        second = self.commit(self.rows[1], '使用护盾！')
        self.assertIn(first.ref, second.dependencies)
        self.store.start_run('other', 'p', None, 'en', 'zh', 'k', mode='game_resources')
        self.assertFalse(self.memory.read(self.rows[1], self.store.view('other')).stm)
        self.graph.invalidate(self.nodes[0].ref, 'changed definition')
        self.assertFalse(self.store.is_valid(second.ref))

    def test_topic_summary_rebuild_and_source_invalidation(self):
        self.commit(self.rows[0], '护盾吸收伤害。')
        draft = self.memory.summarize(self.rows[1], self.store.view('r'), flush=True)
        self.assertEqual(draft.content['summary_mode'], 'topic')
        approved = self.store.approve(draft.ref, 'fixture')
        self.assertIn(approved.ref, self.memory.read(self.rows[1], self.store.view('r')).refs)
        self.assertIsNone(self.memory.summarize(self.rows[1], self.store.view('r'), flush=True))
        self.store.put_source(replace(self.rows[0], source_version=2, source='Ward heals.'))
        self.assertFalse(self.store.is_valid(approved.ref))

    def test_model_link_proposals_require_approval(self):
        from localization_agent.retrieval.extraction import EntityLinker
        from localization_agent.schemas.graph import GraphRef
        model = lambda task, prompt: ModelReply('[{"text_id":"line","entity_id":"ward","relation":"MENTIONS","quote":"Ward"}]', 'fixture')
        linker = EntityLinker(self.graph, MemoryService(self.store, chat=model))
        draft, = linker.propose((self.nodes[1].ref,), (GraphRef('node', 'ward', 1),))
        self.assertFalse(self.graph.valid(draft.ref))
        approved = self.graph.approve(draft.ref, 'fixture')
        self.assertTrue(self.graph.valid(approved.ref))

    def test_no_topic_stm_and_ltm_searches_beyond_recent_window(self):
        first = self.commit(self.rows[0], '护盾吸收伤害。')
        for i in range(3):
            o = Occurrence('p', 'misc', str(i), i, 'Unrelated button')
            self.store.put_source(o)
            self.store.commit_translation(o, 'r', '按钮', TranslationCheck(str(i), 1, text_hash('按钮'), 'fixture', 'fixture', True))
        query = replace(self.rows[1], metadata={})
        bundle = self.memory.read(query, self.store.view('r'))
        self.assertFalse(bundle.stm)
        self.assertIn(first.ref, [r.ref for r in bundle.ltm])
        phase = replace(self.rows[1], metadata={'phase_id': 'late'})
        self.assertFalse(self.memory.read(phase, self.store.view('r')).refs)

if __name__ == '__main__':
    unittest.main()
