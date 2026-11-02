import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from localization_agent.models.chat_completions import CallBudget
from localization_agent.models.protocols import ModelReply
from localization_agent.selection.teacher import DurableChat, UncertainCall, prepare, run, export, migrate, extend
from localization_agent.memory import MemoryConfig, MemoryService, MemoryStore
from localization_agent.retrieval import GraphConfig, GraphRetriever, GraphStore
from localization_agent.models.token_count import LocalTokenCounter
from localization_agent.schemas.memory import Occurrence


class FixtureModel:
    calls = 0
    tasks = []

    def __init__(self, budget, **kwargs):
        self.budget = budget

    def __call__(self, task, prompt):
        FixtureModel.calls += 1
        FixtureModel.tasks.append(task)
        self.budget.reserve(20, 0.000001)
        if task == 'teacher_select':
            observation = json.loads(prompt.split('Observation: ', 1)[1])
            text = json.dumps({'rationale':'离线夹具：候选源文可提供当前资源的含义线索，保留该证据。',
                'selected_ids':[c['id'] for c in observation['candidates'][:1]]}, ensure_ascii=False)
        elif task == 'translate_game':
            source = prompt.split('\nSource: ', 1)[1].split('\nCurrent resource context:', 1)[0]
            text = 'MEMORY_ONLY ' + source
        elif task == 'teacher_translation':
            raise AssertionError('Teacher collection must not request a separate final translation')
        elif task == 'extract_terms':
            text = '[]'
        else:
            text = '离线夹具：仅概括已提供的表达。'
        return ModelReply(text, 'offline-fixture-not-real-quality')


class TeacherCollectionTests(unittest.TestCase):
    def test_durable_calls_replay_and_uncertain_calls_do_not_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            chat = DurableChat(Path(tmp), {'selection':{},'translation':{}}, CallBudget(), FixtureModel)
            try:
                before = FixtureModel.calls
                first = chat('extract_terms','fixture')
                self.assertEqual(first,chat('extract_terms','fixture'))
                self.assertEqual(FixtureModel.calls,before+1)
                chat.db.execute("INSERT INTO calls VALUES ('x','fixture','fixture','inflight',NULL,NULL)")
                chat.db.commit()
                from localization_agent.schemas.memory import text_hash
                key=text_hash('fixture\nuncertain')
                chat.db.execute('INSERT INTO calls VALUES (?,?,?,?,NULL,NULL)',(key,'fixture','uncertain','inflight'))
                chat.db.commit()
                with self.assertRaises(UncertainCall):chat('fixture','uncertain')
                self.assertEqual(FixtureModel.calls,before+1)
            finally:
                chat.db.close()

    @unittest.skipUnless(Path('outputs/cpu_preparation_2026-11-01_nodev/pilot_sequences.jsonl').exists(),
                         'Optional real source DB and CPU plan not present')
    def test_selection_only_translation_updates_memory_and_resume_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            plan=root/'plan';plan.mkdir()
            base=Path('outputs/cpu_preparation_2026-11-01_nodev')
            sequences=[json.loads(line) for line in (base/'pilot_sequences.jsonl').read_text().splitlines()]
            sequence=next(s for s in sequences if s['game']=='flare' and s['warmup_ids'])
            other=next(s for s in sequences if s['game']=='flare' and s['sequence_id']!=sequence['sequence_id'])
            sequence={**sequence,'memory_scope':'game_split','sequence_id':'flare:train',
                      'warmup_ids':sequence['warmup_ids'][:1],
                      'target_ids':sequence['target_ids'][:1]+other['target_ids'][:1]}
            (plan/'pilot_sequences.jsonl').write_text(json.dumps(sequence)+'\n')
            (plan/'splits.jsonl').write_bytes((base/'splits.jsonl').read_bytes())
            output=root/'run'
            prepare(output,plan,'configs/flare.memory_trial.json',1)
            initial_calls = len(FixtureModel.tasks)
            state=run(output,FixtureModel)
            self.assertEqual(state['status'],'complete',state)
            self.assertTrue(all(j['status']=='complete' for j in state['jobs']))
            self.assertNotIn('teacher_translation',FixtureModel.tasks[initial_calls:])
            self.assertEqual(FixtureModel.tasks[initial_calls:].count('translate_game'),3)
            target=next(j for j in state['jobs'] if j['phase']=='target')
            self.assertEqual(len({j['run_id'] for j in state['jobs']}),1)
            targets=[j for j in state['jobs'] if j['phase']=='target']
            self.assertNotEqual(targets[0]['group_id'],targets[1]['group_id'])
            self.assertGreater(targets[1]['view']['watermark'],targets[0]['view']['watermark'])
            self.assertNotIn('teacher_translation',target)
            self.assertEqual(target['observation']['max_selected'],8)
            self.assertEqual([c['id'] for c in target['selected_contexts']],target['action']['selected_ids'])
            before=FixtureModel.calls
            run(output,FixtureModel)
            self.assertEqual(FixtureModel.calls,before)
            db=sqlite3.connect(output/'flare.sqlite')
            try:
                translations=[json.loads(row[0]) for row in db.execute("SELECT payload FROM records WHERE run IS NOT NULL")]
                translations=[r for r in translations if r['kind']=='translation']
                self.assertEqual(len(translations),3)
                self.assertTrue(all(r['content']['target'].startswith('MEMORY_ONLY ') for r in translations))
                self.assertTrue(all(r['content']['verification']=='unreviewed' for r in translations))
            finally:db.close()
            self.assertEqual(export(output),2)
            row=json.loads((output/'selector_sft_candidates.json').read_text())[0]
            self.assertEqual(set(json.loads(row['output'])),{'rationale','selected_ids'})
            self.assertNotIn('MEMORY_ONLY ',row['output'])
            # A valid selection remains usable even if its later memory update failed.
            target['status']='failed';target['memory_status']='failed'
            (output/'state.json').write_text(json.dumps(state))
            self.assertEqual(export(output,root/'data'),2)
            self.assertTrue((root/'data/selector_sft_train.json').exists())
            self.assertFalse(json.loads((root/'data/selector_sft_train.metadata.json').read_text())['translation_is_label'])
            expanded=root/'expanded-plan';expanded.mkdir()
            used={j['source_id'] for j in state['jobs']}
            extra=next(r['source_id'] for r in map(json.loads,(plan/'splits.jsonl').read_text().splitlines())
                       if r['game']=='flare' and r['split']=='train' and r['source_usable'] and r['source_id'] not in used)
            (expanded/'splits.jsonl').write_bytes((plan/'splits.jsonl').read_bytes())
            (expanded/'pilot_sequences.jsonl').write_text(json.dumps({**sequence,'target_ids':sequence['target_ids']+[extra]})+'\n')
            before_state=(output/'state.json').read_bytes()
            before_translations=FixtureModel.tasks.count('translate_game')
            enlarged=root/'enlarged';extend(output,enlarged,expanded,2)
            self.assertEqual((output/'state.json').read_bytes(),before_state)
            continued=run(enlarged,FixtureModel)
            self.assertEqual(sum(j['phase']=='warmup' for j in continued['jobs']),1)
            self.assertEqual(len({j['run_id'] for j in continued['jobs']}),1)
            self.assertEqual(FixtureModel.tasks.count('translate_game'),before_translations+1)
            self.assertEqual(export(enlarged),3)
            config=json.loads((output/'config.json').read_text());config.pop('memory_policy')
            (output/'config.json').write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError,'resource-group'):
                run(output,FixtureModel)
            with self.assertRaisesRegex(ValueError,'resource-group'):
                export(output)

    @unittest.skipUnless(Path('outputs/cpu_preparation_2026-11-01_nodev/pilot_sequences.jsonl').exists(),
                         'Optional real source DB and CPU plan not present')
    def test_legacy_migration_reuses_paid_interrupted_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan=root/'plan';plan.mkdir()
            base=Path('outputs/cpu_preparation_2026-11-01_nodev')
            sequences=[json.loads(line) for line in (base/'pilot_sequences.jsonl').read_text().splitlines()]
            sequence=next(s for s in sequences if s['game']=='flare' and s['warmup_ids'])
            sequence={**sequence,'memory_scope':'game_split','sequence_id':'flare:train',
                      'warmup_ids':sequence['warmup_ids'][:1],'target_ids':sequence['target_ids'][:1]}
            (plan/'pilot_sequences.jsonl').write_text(json.dumps(sequence)+'\n')
            (plan/'splits.jsonl').write_bytes((base/'splits.jsonl').read_bytes())
            source=root/'old';prepare(source,plan,'configs/flare.memory_trial.json',1)
            config=json.loads((source/'config.json').read_text())
            config.pop('trajectory_schema_version');config['graph']['max_selected']=5
            (source/'config.json').write_text(json.dumps(config))
            state=json.loads((source/'state.json').read_text());job=state['jobs'][0]
            store=MemoryStore(source/'flare.sqlite')
            budget=CallBudget(**json.loads((source/'budget.json').read_text()))
            chat=DurableChat(source,config['models'],budget,FixtureModel)
            try:
                store.start_run(job['run_id'],'flare',None,'en','zh','teacher-knowledge',mode='game_resources')
                job['view']=asdict(store.view(job['run_id']));job['status']='working'
                source_row=store.db.execute('SELECT payload FROM sources WHERE project=? AND id=?',('flare',job['source_id'])).fetchone()
                occurrence=Occurrence(**json.loads(source_row[0]))
                memory=MemoryService(store,MemoryConfig(**config['memory']),chat=chat)
                retriever=GraphRetriever(GraphStore(store),memory,GraphConfig(**config['graph']),
                    token_counter=LocalTokenCounter(config['tokenizer']),
                    allowed_source_ids=frozenset(config['allowed_source_ids']['flare']))
                pack=retriever.build_context(retriever.retrieve(occurrence,store.view(job['run_id']),'graph'))
                memory._call('translate_game','translate_game.txt',source=occurrence.source,
                    source_lang='en',target_lang='zh',contexts=pack.model_context,
                    source_context=pack.manifest['current_source_context'])
            finally:
                store.close();chat.db.close()
            (source/'budget.json').write_text(json.dumps(asdict(budget)))
            (source/'state.json').write_text(json.dumps(state))
            original_state=(source/'state.json').read_bytes()
            before=FixtureModel.tasks.count('translate_game')
            target=root/'upgraded';migrate(source,target)
            self.assertEqual((source/'state.json').read_bytes(),original_state)
            self.assertEqual(json.loads((target/'budget.json').read_text())['calls'],budget.calls)
            result=run(target,FixtureModel)
            self.assertEqual(result['status'],'complete',result)
            self.assertEqual(FixtureModel.tasks.count('translate_game'),before+1)
            self.assertEqual(json.loads((target/'config.json').read_text())['graph']['max_selected'],8)


if __name__=='__main__':unittest.main()
