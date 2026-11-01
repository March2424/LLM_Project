"""Contract checks for lightweight game retrieval; no model calls or tokenizer download."""
import json
import unittest
from dataclasses import asdict, replace

from localization_agent.memory import MemoryStore, MemoryService
from localization_agent.retrieval import GraphStore, GraphRetriever, GraphConfig
from localization_agent.retrieval.game_index import normalize_name
from localization_agent.schemas.graph import Candidate, GraphNode, GraphEdge
from localization_agent.schemas.memory import Occurrence, Evidence, Scope


class LightweightRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.addCleanup(self.store.close)
        self.graph = GraphStore(self.store)
        self.rows = {}
        self.nodes = {}
        for key, text, metadata in [
            ('q', 'Use EMBER  WARD!', {}),
            ('d1', 'An arcane barrier absorbs incoming harm.', {'native_context': 'requires level 5', 'requirements': 'level 5'}),
            ('d2', 'A protective aura surrounds its caster.', {}),
            ('alias', 'A shimmering shield protects the hero.', {}),
            ('branch', 'A forbidden branch definition.', {'branch_id': 'other'})]:
            o = Occurrence('p', key, key, 0, text, metadata=metadata)
            self.rows[key] = o
            self.store.put_source(o)
            self.nodes[key] = self.graph.put_node(GraphNode(key, 'occurrence', Scope('p', key),
                {'occurrence_id': key}, (Evidence(key, 1, text),)))
        for key, name, evidence in [('e1','Ember Ward','q'), ('e2','ember  ward','d2'), ('alias-e','Ember Shield','alias')]:
            self.nodes[key] = self.graph.put_node(GraphNode(key, 'entity', Scope('p'),
                {'entity_type': 'skill', 'name': name}, self.nodes[evidence].evidence))
        for key, src, dst, rel in [('q-e','q','e1','MENTIONS'), ('d1-e1','d1','e1','DEFINES'),
                ('d1-e2','d1','e2','DEFINES'), ('d2-e2','d2','e2','DEFINES'),
                ('bad','branch','e1','DEFINES'), ('alias-def','alias','alias-e','DEFINES')]:
            self.graph.put_edge(GraphEdge(key, rel, self.nodes[src].ref, self.nodes[dst].ref,
                Scope('p'), self.nodes[src].evidence, status='approved', reviewer='fixture'))
        self.graph.create_snapshot('g','p')
        self.store.create_snapshot('k','p')
        self.store.start_run('r','p',None,'en','zh','k',mode='game_resources')
        self.retriever = GraphRetriever(self.graph,MemoryService(self.store),
            GraphConfig(max_characters=20000), token_counter=len)  # deterministic budget fixture

    def pool(self):
        return self.retriever.retrieve(self.rows['q'],self.store.view('r'),'g')

    def test_name_lookup_deduplicates_evidence_without_merging_identity(self):
        self.assertEqual(normalize_name(' EMBER\t Ward '), 'ember ward')
        pool = self.pool()
        sources = [c for c in pool.candidates if c.category=='source']
        self.assertEqual({c.id for c in sources}, {'source:d1@1','source:d2@1'})
        d1 = next(c for c in sources if c.id=='source:d1@1')
        self.assertEqual(sum(c.id==d1.id for c in sources), 1)
        self.assertTrue({self.nodes['e1'].ref,self.nodes['e2'].ref} <= set(d1.graph_refs))
        self.assertIn('requires level 5',d1.text)
        self.assertIn('not assumed true',d1.text)
        index = self.retriever.game_index('g')
        self.assertEqual(len(index.names[('skill','ember ward')]),2)
        self.assertNotIn('source:alias@1',{c.id for c in sources})
        self.assertNotIn('source:branch@1',{c.id for c in sources})
        resolved,_=self.retriever.bindings(replace(self.rows['q'],metadata={'topic_id':'native-topic'}),'g')
        self.assertNotIn('topic_id',resolved)

    def test_source_update_invalidates_cached_evidence(self):
        self.pool()
        self.store.put_source(replace(self.rows['d1'],source_version=2,source='Changed source'))
        self.assertNotIn('source:d1@1',{c.id for c in self.pool().candidates})

    def test_one_entity_hop_and_no_arbitrary_multi_topic_binding(self):
        for key in ('place1','place2'):
            self.nodes[key]=self.graph.put_node(GraphNode(key,'entity',Scope('p'),
                {'entity_type':'location','name':key},self.nodes['q'].evidence))
        for key,src,dst,rel in [('first-hop','e1','place1','LOCATED_IN'),
                ('second-hop','place1','place2','LOCATED_IN'),('second-topic','q','alias-e','MENTIONS')]:
            self.graph.put_edge(GraphEdge(key,rel,self.nodes[src].ref,self.nodes[dst].ref,Scope('p'),
                self.nodes['q'].evidence,status='approved',reviewer='fixture'))
        self.graph.create_snapshot('g2','p')
        pool=self.retriever.retrieve(self.rows['q'],self.store.view('r'),'g2')
        ids={c.id for c in pool.candidates}
        self.assertIn('relation:first-hop@1',ids)
        self.assertNotIn('relation:second-hop@1',ids)
        self.assertNotIn('topic_id',pool.resolved_metadata)

    def test_lexical_fallback_without_entity_and_unknown_topic(self):
        row=self.rows['alias']
        # A source-only snapshot still supports lexical retrieval.
        refs=tuple(n.ref for n in self.nodes.values() if n.kind=='occurrence')
        self.store.db.execute('INSERT INTO graph_snapshots VALUES (?,?)',
            ('source-only',json.dumps({'id':'source-only','project_id':'p','refs':[asdict(r) for r in refs]})))
        pool=self.retriever.retrieve(row,self.store.view('r'),'source-only')
        self.assertFalse(pool.resolved_metadata)
        self.assertTrue(pool.candidates)
        self.assertTrue(all('LEXICAL' in c.relations for c in pool.candidates))

    def test_selector_actions_exact_pack_and_hard_items(self):
        pool=self.pool()
        hard=Candidate('hard@1','style','Keep tags.',True)
        soft=tuple(Candidate('s%d@1'%i,'source','Context '+str(i)) for i in range(6))
        pool=replace(pool,candidates=(hard,)+soft)
        observation=self.retriever.selector_observation(pool)
        self.assertEqual([c['id'] for c in observation['candidates']],['c01','c02','c03','c04','c05','c06'])
        pack=self.retriever.select_context(pool,('c02','c04'))
        self.assertEqual([c.id for c in pack.selected],['hard@1','s1@1','s3@1'])
        self.assertEqual(pack.model_context,pack.manifest['model_context'])
        self.assertEqual(pack.tokens,len(pack.model_context))
        self.assertEqual(pack,self.retriever.select_context(pool,('c04','c02')))
        self.assertEqual([c['id'] for c in json.loads(pack.model_context)],['h1','c02','c04'])
        self.assertEqual(self.retriever.select_context(pool,()).selected,(hard,))
        for ids in [('c99',),('c01','c01'),tuple(c['id'] for c in observation['candidates'])]:
            with self.assertRaises(ValueError):self.retriever.select_context(pool,ids)
        tiny=GraphRetriever(self.graph,self.retriever.memory,GraphConfig(max_soft_tokens=5),token_counter=len)
        with self.assertRaises(ValueError):tiny.select_context(pool,('c01',))
        self.assertEqual(tiny.build_context(pool).selected,(hard,))
        missing=GraphRetriever(self.graph,self.retriever.memory)
        with self.assertRaisesRegex(ValueError,'budget tokenizer'):missing.build_context(pool)


if __name__=='__main__':
    unittest.main()
