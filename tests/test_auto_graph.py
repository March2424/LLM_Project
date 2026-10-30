import csv
import json
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from localization_agent.data.benchmark import import_benchmark
from localization_agent.retrieval.builder import AutoGraphBuilder
from localization_agent.memory import MemoryStore, MemoryService, MemoryConfig
from localization_agent.models.protocols import ModelReply
from localization_agent.retrieval import GraphStore, GraphRetriever, GraphConfig
from localization_agent.schemas.memory import TranslationCheck, text_hash


class DiscoveryFixture:
    """Deterministic contract fixture, not a quality experiment."""
    def __init__(self):
        self.calls = []
        self.reject = False
        self.bad_quote = False
        self.bad_target = False

    def __call__(self, task, prompt):
        self.calls.append((task, prompt))
        if task == 'discover_graph':
            rows = json.loads(prompt.split('Rows: ')[1].split('\nExisting catalog: ')[0])
            catalog = json.loads(prompt.split('\nExisting catalog: ')[1])
            reuse = {n['name']: n['id'] for n in catalog}
            evidence = lambda rid, field, quote: [{'id': rid, 'field': field, 'quote': quote}]
            entities, relations = [], []
            def ent(key, name, typ, rid, field, quote):
                entities.append(dict(key=key, name=name, entity_type=typ, reuse_id=reuse.get(name),
                                     evidence=evidence(rid, field, 'fabricated' if self.bad_quote else quote)))
            def rel(key, src, dst, typ, rid, field, quote):
                relations.append(dict(key=key, source=src, target=dst, relation=typ, conditions={}, evidence=evidence(rid, field, quote)))
            if rows[0]['id'] == 'd':
                ent('ward', 'Ward', 'skill', 'd', 'source', 'Ward')
                rel('def', 'text:d', 'entity:ward', 'DEFINES', 'd', 'source', 'Ward')
            else:
                ent('ward', 'Ward', 'skill', 'l', 'source', 'Ward')
                ent('hero', 'Arin', 'character', 'l', 'context', 'Arin')
                ent('order', 'Order', 'organization', 'l', 'context', 'Order')
                rel('mention', 'text:l', 'entity:ward', 'MENTIONS', 'l', 'source', 'Ward')
                rel('speaker', 'text:l', 'entity:hero', 'SPOKEN_BY', 'l', 'context', 'speaker=Arin')
                rel('member', 'entity:hero', 'entity:order', 'MEMBER_OF', 'l', 'context', 'Arin is a member of the Order')
            text = json.dumps({'entities': entities, 'relations': relations})
        elif task == 'review_graph':
            proposal = json.loads(prompt.split('\nProposal: ')[1])
            decisions = {}
            for collection, prefix in [('entities', 'entity'), ('relations', 'relation')]:
                for x in proposal[collection]:
                    decisions[prefix + ':' + x['key']] = {'status': 'unknown' if self.reject else 'accepted', 'reason': 'fixture decision'}
            text = json.dumps({'decisions': decisions})
        elif task == 'check_game_translation':
            text = '{"passed":true,"issues":[]}'
        elif task == 'translate_game':
            text = '护盾<b>' if self.bad_target else '使用护盾！'
        elif task == 'review_memory':
            text = '{"status":"accepted","reason":"fixture"}'
        elif task == 'review_style':
            text = '{"status":"accepted","reason":"fixture"}'
        elif task == 'extract_terms':
            text = '[{"source":"Ward","target":"护盾"}]'
        else:
            text = 'fixture ' + task
        return ModelReply(text, 'offline-contract-fixture')


class AutoGraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = MemoryStore()
        self.addCleanup(self.store.close)
        self.graph = GraphStore(self.store)
        path = Path(self.tmp.name) / 'resources.csv'
        with path.open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['id', 'source_text', 'text_type', 'context'])
            w.writerow(['d', 'Ward absorbs damage.', 'skill_description', ''])
            w.writerow(['l', 'Use Ward!', 'dialogue', 'speaker=Arin; Arin is a member of the Order'])
        self.input = import_benchmark([path], 'p', self.graph, batch_size=1)
        self.model = DiscoveryFixture()
        self.memory = MemoryService(self.store, MemoryConfig(summary_step=1), chat=self.model)
        self.builder = AutoGraphBuilder(self.graph, self.memory, reviewer=self.memory)

    def build(self):
        a = self.builder.build(self.input.batches[0])
        b = self.builder.build(self.input.batches[1])
        self.graph.create_snapshot('g', 'p')
        self.store.create_snapshot('k', 'p')
        self.store.start_run('r', 'p', None, 'en', 'zh', 'k', mode='game_resources')
        self.retriever = GraphRetriever(self.graph, self.memory, GraphConfig(max_characters=50000))
        return a, b

    def commit(self, occurrence, target):
        pack = self.retriever.build_context(self.retriever.retrieve(occurrence, self.store.view('r'), 'g'))
        check = TranslationCheck(occurrence.occurrence_id, 1, text_hash(target), pack.context_hash, 'fixture', True)
        return self.retriever.commit(occurrence, target, check, pack)

    def test_discovery_reuse_native_speaker_and_five_memory_paths(self):
        a, b = self.build()
        for task, prompt in self.model.calls:
            if task == 'discover_graph':
                rows = json.loads(prompt.split('Rows: ')[1].split('\nExisting catalog: ')[0])
                self.assertTrue(all(set(row) == {'id', 'source_text', 'text_type', 'context'} for row in rows))
        self.assertIn(a.entities[0], b.entities)
        self.assertTrue(any(self.graph.get(r).relation == 'MEMBER_OF' for r in b.edges))
        d, line = self.input.occurrences
        self.assertEqual(line.source, 'Use Ward!')
        self.assertNotIn('speaker_id', line.metadata)
        first = self.commit(d, '护盾吸收伤害。')
        terms = self.memory.extract_terms(first.ref)
        self.store.approve(terms[0].ref, 'fixture')
        drafts = self.retriever.update_memory(line, self.store.view('r'), 'g', flush=True)
        self.assertEqual({r.kind for r in drafts}, {'summary', 'style'})
        for draft in drafts:
            self.store.approve(draft.ref, 'fixture')
        pool = self.retriever.retrieve(line, self.store.view('r'), 'g')
        self.assertTrue(pool.resolved_metadata['speaker_id'])
        self.assertTrue(pool.resolved_metadata['topic_id'])
        self.assertTrue({'term','summary','style','translation','source','relation'} <= {c.category for c in pool.candidates})
        saved = self.commit(line, '使用护盾！')
        self.assertIn('speaker_id', saved.content['metadata'])
        # Derived identity is a real dependency even when no soft context is selected.
        pack = self.retriever.build_context(pool, ())
        self.assertTrue(pack.manifest['graph_refs'])
        speaker = next(r for r in b.edges if self.graph.get(r).relation == 'SPOKEN_BY')
        self.graph.invalidate(speaker, 'identity correction')
        self.assertFalse(self.store.is_valid(saved.ref))
        self.assertTrue(all(not self.store.is_valid(r.ref) for r in drafts))

    def test_sequential_session_commits_and_updates_memory_without_reference(self):
        from localization_agent.workflow.game import GameSession
        self.build()
        outcome = GameSession(self.retriever).translate_one(self.input.occurrences[1], 'r', 'g')
        self.assertEqual(outcome.status, 'committed')
        self.assertTrue(outcome.translation)
        self.assertEqual({self.store.get(r).kind for r in outcome.memories}, {'term', 'summary', 'style'})

    def test_format_failure_never_becomes_tm_even_if_model_passes(self):
        from localization_agent.workflow.game import GameSession
        self.build()
        self.model.bad_target = True
        outcome = GameSession(self.retriever).translate_one(self.input.occurrences[1], 'r', 'g')
        self.assertEqual(outcome.status, 'failed_check')
        self.assertIsNone(outcome.translation)
        self.assertFalse(self.store.visible(self.store.view('r')))

    def test_unknown_review_creates_no_approved_graph(self):
        self.model.reject = True
        result = self.builder.build(self.input.batches[0])
        self.assertFalse(result.entities)
        self.assertFalse(result.edges)
        self.assertTrue(result.review_call)
        self.assertEqual(result.decisions['entity:ward']['status'], 'unknown')

    def test_invalid_quotes_fail_before_writes(self):
        self.model.bad_quote = True
        with self.assertRaises(ValueError):
            self.builder.build(self.input.batches[0])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM graph_items WHERE kind='edge'").fetchone()[0], 0)

    def test_conditional_binding_and_source_revision(self):
        a, b = self.build()
        line = self.input.occurrences[1]
        speaker = next(r for r in b.edges if self.graph.get(r).relation == 'SPOKEN_BY')
        edge = self.graph.get(speaker)
        conditional = self.graph.put_edge(replace(edge, scope=replace(edge.scope, conditions={'phase_id': ('late',)})), expected_version=edge.version)
        self.graph.create_snapshot('conditional', 'p')
        metadata, refs = self.retriever.bindings(line, 'conditional')
        self.assertNotIn('speaker_id', metadata)
        self.store.put_source(replace(line, source_version=2, metadata={**line.metadata, 'native_context': 'speaker=SomeoneElse'}))
        self.assertFalse(self.graph.valid(speaker))

    def test_import_batches_independent_rows_and_rejects_extra_columns(self):
        path = Path(self.tmp.name) / 'independent.csv'
        with path.open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['id', 'source_text', 'text_type', 'context'])
            w.writerows([['a', 'Same text', 'ui_label', ''],
                         ['b', 'Same text', 'dialogue', 'speaker=Arin'],
                         ['c', 'Other text', '', '']])
        result = import_benchmark([path], 'independent', self.graph, batch_size=2)
        self.assertEqual([len(batch) for batch in result.batches], [2, 1])
        self.assertEqual(tuple(ref for batch in result.batches for ref in batch), result.nodes)
        self.assertEqual([o.occurrence_id for o in result.occurrences], ['a', 'b', 'c'])
        self.assertEqual(result.occurrences[2].metadata['text_type'], 'unknown')
        self.assertTrue(all('group_id' not in o.metadata for o in result.occurrences))
        self.assertTrue(all('group_id' not in self.graph.get(ref).attributes for ref in result.nodes))
        bad = Path(self.tmp.name) / 'bad.csv'
        for extra in ('reference_text', 'group_id'):
            bad.write_text(f'id,source_text,text_type,context,{extra}\nx,text,ui,,extra\n')
            with self.assertRaises(ValueError):
                import_benchmark([bad], 'other', self.graph)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sources WHERE project='other'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
