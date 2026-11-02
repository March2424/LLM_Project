"""CPU preparation must preserve resource groups and keep reference text offline."""
import json
import csv
import tempfile
import unittest
from pathlib import Path

from localization_agent.data.training_split import assign_groups, build_game_sequences, prepare
from localization_agent.data.native_memory import native_binding, memory_blocks


class TrainingSplitTests(unittest.TestCase):
    def test_coverage_pilot_does_not_copy_train_targets_to_test_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            with (root/'data/source.csv').open('w',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(['id','source_text','text_type','context'])
                for i in range(60):writer.writerow([str(i),'Source '+chr(65+i),'item_name',''])
            (root/'data/provenance.jsonl').write_text(''.join(json.dumps({'record_id':str(i),
                'localization_key':f'items.fixture{i}.name','definition':{'path':'items.properties','line':i+1},'use':None})+'\n' for i in range(60)))
            (root/'data/reference.jsonl').write_text(''.join(json.dumps({'record_id':str(i),'reference_text':'REFERENCE'})+'\n' for i in range(60)))
            (root/'data/game_samples.json').write_text(json.dumps({'datasets':[{'game':'shattered',
                'model_input_csv':'data/source.csv','status_provenance':'data/provenance.jsonl',
                'offline_references':'data/reference.jsonl'}]}))
            prepare(root,root/'prepared',pilot_size=8,memory_coverage=True)
            plan=[json.loads(l) for l in (root/'prepared/pilot_sequences.jsonl').read_text().splitlines()]
            splits=[json.loads(l) for l in (root/'prepared/splits.jsonl').read_text().splitlines()]
            self.assertTrue(any(r['split']=='test' for r in splits))
            self.assertEqual(len(plan),1)
            self.assertEqual(plan[0]['split'],'train')
            self.assertEqual(len(plan[0]['target_ids']),8)

    def test_native_speakers_require_explicit_npc_role_and_resource_identity(self):
        provenance={'module':'campaign','resource':'mods/campaign/npcs/arin.txt','source':{'path':'npcs/arin.txt','line':3}}
        row={'source_text':'Keep your shield ready.','text_type':'dialogue',
             'context':'npc_name=Arin; utterance_role=npc; requires_status=ready'}
        npc=native_binding('flare',row,provenance)
        player=native_binding('flare',{**row,'context':row['context'].replace('role=npc','role=player')},provenance)
        other=native_binding('flare',row,{**provenance,'resource':'mods/campaign/npcs/other.txt'})
        self.assertIn('speaker_id',npc['metadata'])
        self.assertNotIn('speaker_id',player['metadata'])
        self.assertNotEqual(npc['metadata']['speaker_id'],other['metadata']['speaker_id'])

    def test_memory_blocks_keep_conditions_and_do_not_require_sft_references(self):
        rows=[{'game':'flare','source_id':str(i),'split':'train','source_usable':True,
               'score_candidate':False,'position':i} for i in range(12)]
        bindings={'flare':{str(i):{'metadata':{'speaker_id':'npc','topic_id':'quest'},'source_chars':80,'source_hash':str(i),
                    'conditions':{'requirements':'ready' if i<6 else 'finished'}} for i in range(12)}}
        ordered,blocks=memory_blocks(rows,bindings,set())
        self.assertEqual(len(ordered),12)
        self.assertEqual(sum(b['kind']=='role' for b in blocks),2)
        self.assertEqual(sum(b['kind']=='topic' for b in blocks),2)
        self.assertTrue(all(len(b['source_ids'])==6 for b in blocks))

    def test_memory_stream_crosses_resource_groups_but_not_games_or_splits(self):
        rows = [{'game':game, 'split':split, 'source_id':f'{game}-{split}-{i}',
                 'group_id':f'resource-{i}', 'position':i, 'source_usable':True}
                for game,split,count in [('flare','train',16),('flare','test',12),('shattered','train',14)]
                for i in range(count)]
        streams = build_game_sequences(rows)
        self.assertEqual(len(streams),3)
        self.assertEqual(len({s['sequence_id'] for s in streams}),3)
        for stream in streams:
            self.assertEqual(stream['memory_scope'],'game_split')
            self.assertEqual(len(stream['warmup_ids']),10)
            self.assertTrue(stream['target_ids'])
            self.assertFalse(set(stream['warmup_ids']) & set(stream['target_ids']))
            self.assertTrue(all(key.startswith(stream['game']+'-'+stream['split'])
                                for key in stream['warmup_ids']+stream['target_ids']))

    def test_resource_and_template_components_stay_together(self):
        rows = [{'id':'a','source_text':'Deal 10 damage to the enemy.'},
                {'id':'b','source_text':'Deal 20 damage to the enemy.'},
                {'id':'c','source_text':'Another name'}]
        provenance = {'a':{'resource':'one','native_id':'skill'},
                      'b':{'resource':'two','native_id':'other'},
                      'c':{'resource':'two','native_id':'other'}}
        result = assign_groups('flare',rows,provenance,42)
        self.assertEqual(result['a'],result['b'])
        self.assertEqual(result['b'],result['c'])
        self.assertTrue(all(split in {'train','test'} for _,split in result.values()))

    def test_preparation_does_not_export_reference_to_model_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'data').mkdir()
            (root/'data/source.csv').write_text('id,source_text,text_type,context\na,Source,item_name,\n')
            (root/'data/provenance.jsonl').write_text(json.dumps({'record_id':'a','source':{'path':'item.xml'}})+'\n')
            (root/'data/reference.jsonl').write_text(json.dumps({'record_id':'a','reference_text':'SECRET_REFERENCE'})+'\n')
            (root/'data/game_samples.json').write_text(json.dumps({'datasets':[{'game':'naev',
                'model_input_csv':'data/source.csv','status_provenance':'data/provenance.jsonl',
                'offline_references':'data/reference.jsonl'}]}))
            summary=prepare(root,root/'prepared')
            self.assertFalse(summary['history_generated'])
            self.assertFalse(summary['teacher_trajectories_generated'])
            self.assertEqual(summary['paid_calls'],0)
            for name in ('sources.jsonl','splits.jsonl','sequences.jsonl','teacher_pilot.jsonl'):
                self.assertNotIn('SECRET_REFERENCE',(root/'prepared'/name).read_text())
            self.assertIn('SECRET_REFERENCE',(root/'prepared/offline/references.jsonl').read_text())


if __name__=='__main__':
    unittest.main()
