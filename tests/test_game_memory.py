import unittest
from dataclasses import replace
from localization_agent.memory import MemoryStore, MemoryService, MemoryConfig
from localization_agent.models.protocols import ModelReply
from localization_agent.schemas.memory import Occurrence, Evidence, Scope, TranslationCheck, text_hash
from localization_agent.schemas.graph import GraphNode, GraphEdge
from localization_agent.retrieval import GraphStore, GraphRetriever, GraphConfig


class GameMemoryTests(unittest.TestCase):
    def test_native_multi_line_notes_and_topic_summary_are_recalled_after_history(self):
        from localization_agent.data.native_memory import native_binding
        rows=[];bindings={}
        context='npc_name=Arin; utterance_role=npc; requires_status=ready'
        provenance={'module':'campaign','resource':'mods/campaign/npcs/arin.txt','source':{'path':'npcs/arin.txt','line':1}}
        for i in range(6):
            row=Occurrence('p','arin-resource',f'arin-{i}',i,'Ward protects the village. Line '+str(i),
                           metadata={'native_context':context,'requirements':context,'text_type':'dialogue'})
            self.store.put_source(row)
            self.graph.put_node(GraphNode(row.occurrence_id,'occurrence',Scope('p','arin-resource'),
                {'occurrence_id':row.occurrence_id},(Evidence(row.occurrence_id,1,row.source),)))
            bindings[row.occurrence_id]=native_binding('flare',{'source_text':row.source,'text_type':'dialogue','context':context},provenance)
            rows.append(row)
        self.graph.create_snapshot('native','p')
        memory=MemoryService(self.store,MemoryConfig(summary_step=5,style_min_examples=2),
                             chat=lambda task,prompt:ModelReply('fixture '+task,'fixture'))
        retriever=GraphRetriever(self.graph,memory,GraphConfig(max_characters=50000,max_soft_tokens=None),
                                 source_annotations=bindings)
        for i,row in enumerate(rows):
            pool=retriever.retrieve(row,self.store.view('r'),'native')
            kinds={c['memory_kind'] for c in retriever.selector_observation(pool)['candidates']}
            if i==1:self.assertNotIn('style',kinds)
            if i==2:self.assertIn('style',kinds)
            if i==5:self.assertTrue({'style','summary'}<=kinds)
            pack=retriever.build_context(pool)
            retriever.commit(row,'护盾守护村庄。',TranslationCheck(row.occurrence_id,1,text_hash('护盾守护村庄。'),pack.context_hash,'fixture',True),pack)
            for draft in retriever.update_memory(row,self.store.view('r'),'native'):
                self.store.approve(draft.ref,'fixture')
        changed=replace(rows[-1],metadata={**rows[-1].metadata,'requirements':'finished'})
        visible=memory.read(replace(changed,metadata={**changed.metadata,**bindings[changed.occurrence_id]['metadata']}),self.store.view('r'))
        self.assertFalse(visible.styles or visible.summaries)
        self.store.start_run('isolated','p',None,'en','zh','k',mode='game_resources')
        other=retriever.retrieve(rows[-1],self.store.view('isolated'),'native')
        self.assertFalse(any(c.category in {'style','summary'} for c in other.candidates))
        bindings[rows[-1].occurrence_id]['source_hash']='changed'
        with self.assertRaisesRegex(ValueError,'Native binding'):
            retriever.retrieve(rows[-1],self.store.view('r'),'native')

    def test_percent_prose_is_not_a_printf_placeholder(self):
        from localization_agent.workflow.game import GameSession
        session=GameSession(self.retriever)
        self.assertEqual(session.placeholders('Gain +50% durability and 75% faster.'),[])
        self.assertEqual(session.placeholders('%1$d-%2$d %s %% % d'),['%1$d','%2$d','%s','%%','% d'])

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
        self.retriever = GraphRetriever(self.graph, self.memory, GraphConfig(max_characters=50000, max_soft_tokens=None))

    def commit(self, o, target, run='r'):
        pack = self.retriever.build_context(self.retriever.retrieve(o, self.store.view(run), 'g'))
        check = TranslationCheck(o.occurrence_id, 1, text_hash(target), pack.context_hash, 'fixture', True)
        return self.retriever.commit(o, target, check, pack)

    def test_cross_file_definition_stm_commit_and_run_isolation(self):
        first = self.commit(self.rows[0], '护盾吸收伤害。')
        pool = self.retriever.retrieve(self.rows[1], self.store.view('r'), 'g')
        self.assertTrue(any(c.category == 'source' and c.text == self.rows[0].source for c in pool.candidates))
        self.assertTrue(any(c.memory_ref == first.ref for c in pool.candidates))
        observation = self.retriever.selector_observation(pool)
        example = next(c for c in observation['candidates'] if c['category'] == 'translation')
        self.assertEqual(example['memory_kind'], 'stm')
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

    def test_training_visibility_excludes_other_sources_and_their_memory(self):
        self.commit(self.rows[0], '护盾吸收伤害。')
        bounded = GraphRetriever(self.graph, self.memory,
            GraphConfig(max_characters=50000, max_soft_tokens=None),
            allowed_source_ids=frozenset({'line'}))
        pool = bounded.retrieve(self.rows[1], self.store.view('r'), 'g')
        self.assertTrue(all(e.occurrence_id=='line' for c in pool.candidates for e in c.sources))
        self.assertFalse(any(c.category=='translation' for c in pool.candidates))
        with self.assertRaises(ValueError):
            bounded.retrieve(self.rows[0], self.store.view('r'), 'g')

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
