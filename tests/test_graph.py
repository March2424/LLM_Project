import importlib.util
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from localization_agent.data import CSVConfig, import_csv
from localization_agent.memory import MemoryService, MemoryStore
from localization_agent.retrieval import GraphConfig, GraphRetriever, GraphStore
from localization_agent.schemas.graph import GraphEdge, GraphNode, RelationType
from localization_agent.schemas.memory import Evidence, Scope, TranslationCheck, text_hash

spec = importlib.util.spec_from_file_location('demo_graph', Path(__file__).resolve().parents[1] / 'scripts/demo_graph.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.addCleanup(self.store.close)

    def test_graph_recalls_remote_memory_and_tracks_invalidation(self):
        graph, retriever, imported, current = demo.build_demo(self.store)
        pool = retriever.retrieve(current, self.store.view('run'), 'graph-v1')
        self.assertEqual({c.category for c in pool.candidates}, {'term', 'entity', 'source', 'translation'})
        self.assertNotIn('DO NOT IMPORT', str(pool))
        pack = retriever.build_context(pool)
        check = TranslationCheck(current.occurrence_id, 1, text_hash('译文'), pack.context_hash, 'fixture', True)
        saved = retriever.commit(current, '译文', check, pack)
        self.assertTrue(saved.content['context_manifest']['graph_refs'])
        relation = next(r for r in graph.snapshot('graph-v1').refs if r.id == 'reference')
        graph.invalidate(relation, 'Relation corrected')
        self.assertFalse(self.store.is_valid(saved.ref))
        with self.assertRaises(ValueError):
            retriever.commit(current, '译文', check, pack, expected_version=saved.version)

    def test_source_update_invalidates_graph_and_used_tm(self):
        graph, retriever, imported, current = demo.build_demo(self.store)
        pack = retriever.build_context(retriever.retrieve(current, self.store.view('run'), 'graph-v1'))
        check = TranslationCheck(current.occurrence_id, 1, text_hash('译文'), pack.context_hash, 'fixture', True)
        saved = retriever.commit(current, '译文', check, pack)
        self.store.put_source(replace(imported.occurrences[0], source_version=2, source='Changed skill.'))
        self.assertFalse(self.store.is_valid(saved.ref))
        self.assertFalse(graph.valid(imported.blocks[0].ref))

    def test_selection_keeps_hard_terms_and_rejects_illegal_ids(self):
        graph, retriever, _, current = demo.build_demo(self.store)
        pool = retriever.retrieve(current, self.store.view('run'), 'graph-v1')
        pack = retriever.build_context(pool, ())
        self.assertEqual([c.category for c in pack.selected], ['term'])
        with self.assertRaises(ValueError):
            retriever.build_context(pool, ('invented',))
        tiny = GraphRetriever(graph, MemoryService(self.store), GraphConfig(max_characters=1))
        self.assertEqual(tiny.build_context(pool).status, 'budget_conflict')

    def test_custom_entity_relations_approval_scope_and_snapshot(self):
        graph, retriever, imported, current = demo.build_demo(self.store)
        scope = Scope('demo', current.document_id)
        e = Evidence(imported.occurrences[0].occurrence_id, 1, 'Ember Ward')
        skill = graph.get(next(r for r in graph.snapshot('graph-v1').refs if r.id == 'skill:ward'))
        graph.register_type(RelationType('game:UNLOCKS', ('entity',), ('entity',),
                                        'A skill unlocks a mechanic', ('skill',), ('mechanic',)))
        mechanic = graph.put_node(GraphNode('mechanic', 'entity', scope, {'entity_type': 'mechanic', 'name': 'Protection'}, (e,)))
        edge = graph.put_edge(GraphEdge('unlock', 'game:UNLOCKS', skill.ref, mechanic.ref, scope, (e,)))
        self.assertNotIn(edge.ref, graph.create_snapshot('draft-snapshot', 'demo').refs)
        approved = graph.approve(edge.ref, 'fixture-human')
        graph.create_snapshot('approved-snapshot', 'demo')
        self.assertNotIn(approved.ref, graph.snapshot('graph-v1').refs)
        pool = retriever.retrieve(current, self.store.view('run'), 'approved-snapshot')
        self.assertTrue(any(c.category == 'relation' for c in pool.candidates))
        restricted = graph.put_edge(replace(approved, scope=replace(scope, conditions={'branch_id': ('other',)}),
                                             status='draft', reviewer=None), expected_version=approved.version)
        graph.approve(restricted.ref, 'fixture-human')
        graph.create_snapshot('restricted', 'demo')
        pool = retriever.retrieve(current, self.store.view('run'), 'restricted')
        self.assertFalse(any(c.category == 'relation' for c in pool.candidates))
        with self.assertRaises(ValueError):
            graph.put_edge(GraphEdge('wrong', 'game:UNLOCKS', mechanic.ref, skill.ref, scope, (e,)))

    def test_csv_unknown_order_stable_keys_and_annotation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.csv'
            path.write_text('id,source\na,Same line.\nb,Same line.\nc,Other line.\n', encoding='utf-8')
            graph = GraphStore(self.store)
            cfg = CSVConfig('file', max_rows=1)
            imported = import_csv(path, 'p', graph, cfg, annotations={'a': {'scene': 's'}, 'b': {'scene': 's'}})
            self.assertEqual(len({o.occurrence_id for o in imported.occurrences}), 3)
            self.assertEqual(imported.occurrences[0].metadata['branch_id'], None)
            self.assertEqual(imported.blocks[0].attributes['boundary_origin'], 'technical')
            again = import_csv(path, 'p', graph, cfg, annotations={'a': {'scene': 's'}, 'b': {'scene': 's'}})
            self.assertEqual(imported, again)
            self.store.create_snapshot('k', 'p')
            self.store.start_run('r', 'p', imported.occurrences[0].document_id, 'en', 'zh', 'k')
            graph.create_snapshot('g', 'p')
            retriever = GraphRetriever(graph, MemoryService(self.store))
            pool = retriever.retrieve(imported.occurrences[1], self.store.view('r'), 'g')
            self.assertFalse(pool.candidates)
            with self.assertRaises(ValueError):
                import_csv(path, 'p', graph, cfg, annotations={'missing': {'scene': 's'}})

    def test_future_members_do_not_hide_current_block(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.csv'
            path.write_text('id,source\na,First.\nb,Second.\nc,Future.\n', encoding='utf-8')
            graph = GraphStore(self.store)
            imported = import_csv(path, 'p', graph, CSVConfig('ordered', order_trusted=True))
            self.store.create_snapshot('k', 'p')
            self.store.start_run('r', 'p', imported.occurrences[0].document_id, 'en', 'zh', 'k')
            graph.create_snapshot('g', 'p')
            pool = GraphRetriever(graph, MemoryService(self.store)).retrieve(imported.occurrences[1], self.store.view('r'), 'g')
            self.assertEqual([c.text for c in pool.candidates], ['First.'])


if __name__ == '__main__':
    unittest.main()
