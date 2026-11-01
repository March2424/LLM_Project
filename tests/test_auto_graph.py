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
from localization_agent.schemas.extraction import ENTITY_TYPES, RELATIONS


class DiscoveryFixture:
    """Deterministic contract fixture, not a quality experiment."""
    def __init__(self):
        self.calls = []
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
        self.builder = AutoGraphBuilder(self.graph, self.memory)

    def build(self):
        a = self.builder.build(self.input.batches[0])
        b = self.builder.build(self.input.batches[1])
        self.graph.create_snapshot('g', 'p')
        self.store.create_snapshot('k', 'p')
        self.store.start_run('r', 'p', None, 'en', 'zh', 'k', mode='game_resources')
        self.retriever = GraphRetriever(self.graph, self.memory, GraphConfig(max_characters=50000, max_soft_tokens=None))
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
                self.assertIn('Allowed entity types: ' + ','.join(ENTITY_TYPES), prompt)
                self.assertIn('Allowed relation types: ' + ','.join(RELATIONS), prompt)
                catalog = json.loads(prompt.split('\nExisting catalog: ')[1])
                if rows[0]['id'] == 'l':
                    self.assertTrue(any(item['name'] == 'Ward' for item in catalog))
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

    def test_agent_reviewer_commit_updates_memory_without_duplicate_reviews(self):
        from localization_agent.workflow.game import GameSession
        self.build()
        session=GameSession(self.retriever)
        pending=session.translate_one(self.input.occurrences[1], 'r', 'g', selected_ids=())
        self.assertEqual(pending.status,'review_pending')
        self.assertFalse(self.store.visible(self.store.view('r')))
        draft=self.store.get(pending.draft)
        check=TranslationCheck('l',1,text_hash(pending.target),draft.content['context_hash'],'fixture-AgentReviewer',True)
        outcome=session.complete_review(pending.draft,check)
        self.assertEqual(outcome.status, 'committed')
        self.assertTrue(outcome.translation)
        self.assertFalse(self.store.get(outcome.translation).content['context_manifest']['items'])
        context=self.store.get(outcome.translation).content['context_manifest']['model_context']
        prompts=[prompt for task,prompt in self.model.calls if task in {'translate_game','check_game_translation'}]
        self.assertEqual(len(prompts),1)
        self.assertTrue(all('Selected context: '+context in prompt for prompt in prompts))
        current=self.store.get(outcome.translation).content['context_manifest']['current_source_context']
        self.assertTrue(all('Current resource context: '+current in prompt for prompt in prompts))
        self.assertEqual({self.store.get(r).kind for r in outcome.memories}, {'term', 'summary', 'style'})
        self.assertFalse({'check_game_translation','review_memory','review_style'} & {task for task,_ in self.model.calls})

    def test_format_warning_is_given_to_reviewer_without_local_rejection(self):
        from localization_agent.workflow.game import GameSession
        self.build()
        self.model.bad_target = True
        session=GameSession(self.retriever)
        outcome = session.translate_one(self.input.occurrences[1], 'r', 'g')
        self.assertEqual(outcome.status, 'review_pending')
        self.assertIn('Placeholder/tag mismatch',outcome.issues)
        self.assertIsNone(outcome.translation)
        self.assertFalse(self.store.visible(self.store.view('r')))
        draft=self.store.get(outcome.draft)
        check=TranslationCheck('l',1,text_hash(outcome.target),draft.content['context_hash'],'fixture-AgentReviewer',False)
        self.assertEqual(session.complete_review(outcome.draft,check).status,'needs_revision')
        self.assertFalse(self.store.visible(self.store.view('r')))

    def test_term_failure_does_not_prevent_summary_and_style_updates(self):
        from localization_agent.workflow.game import GameSession
        self.build()
        original=self.memory.chat
        self.memory.chat=lambda task,prompt:ModelReply('not JSON','fixture') if task=='extract_terms' else original(task,prompt)
        session=GameSession(self.retriever)
        pending=session.translate_one(self.input.occurrences[1],'r','g')
        draft=self.store.get(pending.draft)
        check=TranslationCheck('l',1,text_hash(pending.target),draft.content['context_hash'],'fixture-AgentReviewer',True)
        result=session.complete_review(pending.draft,check)
        self.assertTrue(result.translation)
        self.assertEqual(result.status,'committed_with_warnings')
        self.assertTrue(any(issue.startswith('term:') for issue in result.issues))
        self.assertEqual({self.store.get(r).kind for r in result.memories},{'summary','style'})

    def test_unreviewed_trial_records_memory_without_faking_reviewer_pass(self):
        from localization_agent.workflow.game import GameSession
        self.build()
        result=GameSession(self.retriever,require_reviewer=False).translate_one(self.input.occurrences[1],'r','g')
        self.assertEqual(result.status,'recorded_unreviewed')
        saved=self.store.get(result.translation)
        self.assertFalse(saved.content['check']['passed'])
        self.assertEqual(saved.content['verification'],'unreviewed')
        self.assertEqual({self.store.get(ref).kind for ref in result.memories},{'term','summary','style'})
        self.assertTrue(all(self.store.get(ref).content['verification']=='unreviewed' for ref in result.memories))
        pool=self.retriever.retrieve(self.input.occurrences[1],self.store.view('r'),'g')
        self.assertTrue(any(c.memory_ref and '[Unreviewed trial memory]' in c.text for c in pool.candidates))
        self.store.create_snapshot('after-trial','p')
        self.store.start_run('other','p',None,'en','zh','after-trial',mode='game_resources')
        self.assertFalse(self.memory.read(self.input.occurrences[1],self.store.view('other')).refs)
        self.assertFalse({'check_game_translation','review_memory','review_style'} & {task for task,_ in self.model.calls})

    def test_one_extraction_call_and_explicit_program_validation(self):
        result = self.builder.build(self.input.batches[0])
        self.assertEqual([task for task, _ in self.model.calls], ['discover_graph'])
        self.assertTrue(result.entities)
        self.assertTrue(result.edges)
        self.assertEqual(result.verification, 'program_only')
        edge = self.graph.get(result.edges[0])
        self.assertEqual(edge.attributes['verification'], 'program_only')
        self.assertEqual(edge.reviewer, 'program:graph-contract')
        self.assertNotIn('review_call', edge.attributes)

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
