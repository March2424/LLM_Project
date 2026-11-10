import csv
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from localization_agent.data.benchmark import import_benchmark
from localization_agent.maintenance import MaintenanceConfig, MaintenanceService
from localization_agent.memory import MemoryConfig, MemoryService, MemoryStore
from localization_agent.models.chat_completions import CallBudget
from localization_agent.models.protocols import ModelReply
from localization_agent.retrieval import GraphConfig, GraphRetriever, GraphStore
from localization_agent.schemas.maintenance import UpdateEvent
from localization_agent.schemas.memory import Evidence, MemoryRef, TranslationCheck, text_hash
from localization_agent.schemas.workflow import TranslationOutcome
from localization_agent.workflow.agent import AgentConfig, TranslationAgent

FIRST = {'Ward absorbs damage.': '护盾吸收伤害。', 'Use Ward!': '使用护盾！', 'Rest at the camp.': '在营地休息。'}
SECOND = {'Ward absorbs damage.': '守护吸收伤害。', 'Use Ward!': '使用守护！', 'Rest at the camp.': '在营地休息。'}
PASS = {'passed': True, 'issues': []}


class ScriptedModel:
    """Contract fixture keyed by the source line in the prompt: bookkeeping, not translation quality."""

    def __init__(self, budget, targets):
        self.budget, self.targets = budget, dict(targets)
        self.calls = []

    def __call__(self, task, prompt):
        self.calls.append((task, prompt))
        self.budget.reserve(20, None)
        if task == 'translate_game':
            # Keyed on the prompt's own source line: the context pack repeats other rows' sources.
            source = next(line[len('Source: '):] for line in prompt.splitlines() if line.startswith('Source: '))
            return ModelReply(self.targets[source], 'fixture', 10, 10, None)
        if task == 'review_agent':
            return ModelReply(json.dumps(PASS), 'fixture', 10, 10, None)
        return ModelReply('[]', 'fixture', 10, 10, None)


class StubAgent:
    """Stands in for M5 when the outcome, not the write, is what M6 must react to."""

    def __init__(self, outcomes):
        self.outcomes, self.runs = outcomes, []

    def run(self, occurrence, run_id, snapshot_id, **values):
        self.runs.append((occurrence.occurrence_id, values.get('expected_version')))
        outcome = self.outcomes.get(occurrence.occurrence_id)
        if outcome is None:
            raise AssertionError(f'No scripted outcome for {occurrence.occurrence_id}')
        return replace(outcome, occurrence_id=occurrence.occurrence_id)

    @property
    def chat(self):
        return None


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = MemoryStore()
        self.addCleanup(self.store.close)
        self.graph = GraphStore(self.store)
        path = Path(self.tmp.name) / 'resources.csv'
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            writer.writerow(['id', 'source_text', 'text_type', 'context'])
            for row in [('w', 'Ward absorbs damage.', 'skill_description', ''),
                        ('l', 'Use Ward!', 'dialogue', ''), ('c', 'Rest at the camp.', 'dialogue', '')]:
                writer.writerow(row)
        self.input = import_benchmark([path], 'p', self.graph, batch_size=1)
        self.graph.create_snapshot('g', 'p')
        self.store.create_snapshot('k', 'p')
        self.store.start_run('r', 'p', None, 'en', 'zh', 'k', mode='game_resources')
        self.budget = CallBudget(max_calls=60, max_tokens=200000)
        self.rows = {o.occurrence_id: o for o in self.input.occurrences}
        self.term = self.store.approve(self.store.draft(
            'term', MemoryStore.game_scope(self.rows['w']),
            {'source': 'Ward', 'target': '护盾', 'mandatory': False, 'verification': 'program_only'},
            (Evidence('w', 1, 'Ward'),), run_id='r', position=self.rows['w'].position,
            record_id='term:ward').ref, 'human:glossary').ref

    def build(self, **config):
        self.model = ScriptedModel(self.budget, FIRST)
        memory = MemoryService(self.store, MemoryConfig(summary_step=99), chat=self.model)
        retriever = GraphRetriever(self.graph, memory, GraphConfig(max_characters=50000, max_soft_tokens=None))
        self.agent = TranslationAgent(retriever, config=AgentConfig(reviewer='agent:reviewer'))
        self.retriever = retriever
        return MaintenanceService(retriever, self.agent, config=MaintenanceConfig(**config))

    def translate_all(self):
        maintenance = self.build()
        for occurrence_id in ('w', 'l', 'c'):
            outcome = self.agent.run(self.rows[occurrence_id], 'r', 'g')
            self.assertEqual(outcome.status, 'committed', outcome.failure)
        self.assertEqual(self.tm('w').content['target'], '护盾吸收伤害。')
        for occurrence_id in ('w', 'l'):
            self.assertIn(self.term, self.tm(occurrence_id).dependencies,
                          'The term was supplied, so the row is a dependency of the change')
        self.assertNotIn(self.term, self.tm('c').dependencies)
        return maintenance

    def tm(self, occurrence_id):
        return next(r for r in self.store.visible(self.store.view('r'))
                    if r.kind == 'translation' and r.evidence[0].occurrence_id == occurrence_id)

    def event(self, maintenance):
        """A glossary decision: Ward is 守护 from now on, and the model is told."""
        revised = maintenance.supersede(self.term, {'target': '守护'}, 'human:glossary')
        self.model.targets = SECOND
        return UpdateEvent('p', 'term', 'Glossary: Ward is now 守护', ref=revised.ref)

    def test_a_term_revision_reaches_only_the_rows_that_carried_it(self):
        maintenance = self.translate_all()
        event = self.event(maintenance)
        self.assertEqual([i.occurrence_id for i in maintenance.plan(event, 'r', 'g')[0]], ['w', 'l'],
                         'The camp row never supplied this term')
        report = maintenance.apply(event, 'r', 'g')
        self.assertEqual({i.occurrence_id for i in report.revised}, {'w', 'l'})
        self.assertEqual(report.counts['needs_revision'], 2)
        self.assertEqual(report.counts['no_change'], 1, 'Replacing a row retires the history that used it')
        camp = next(i for i in report.items if i.occurrence_id == 'c')
        self.assertIn('Re-anchored', camp.reason)
        self.assertEqual((camp.new_target, camp.old_target), ('在营地休息。', '在营地休息。'))
        self.assertEqual(self.tm('w').content['target'], '守护吸收伤害。')
        self.assertEqual(self.tm('c').content['target'], '在营地休息。')
        self.assertEqual(report.conflicts, ())
        self.assertEqual(maintenance.audit()[-1]['report']['counts']['needs_revision'], 2)

    def test_full_re_review_inspects_more_rows_for_the_same_work(self):
        maintenance = self.translate_all()
        event = self.event(maintenance)
        self.assertEqual(len(maintenance.plan(event, 'r', 'g')[0]), 2)
        wide = MaintenanceService(self.retriever, self.agent, config=MaintenanceConfig(discovery='full'))
        report = wide.apply(event, 'r', 'g')
        self.assertEqual(report.candidates, 3, 'Full re-review looks at every committed row')
        self.assertEqual(report.counts['needs_revision'], 2)
        self.assertEqual(report.counts['no_change'], 1)
        self.assertIn('does not occur', [i.reason for i in report.items if i.decision == 'no_change'][0])

    def test_a_human_lock_stops_the_model_touching_the_row(self):
        maintenance = self.translate_all()
        report = maintenance.apply(self.event(maintenance), 'r', 'g')
        maintenance.lock('l', 'human:lead', project='p', note='approved wording')
        locked = maintenance.apply(UpdateEvent('p', 'term', 'Second glossary pass',
                                               ref=self.term), 'r', 'g')
        self.assertEqual({i.occurrence_id: i.decision for i in locked.items}, {'w': 'no_change', 'l': 'locked'})
        self.assertEqual(self.tm('l').version, 2, 'The locked row kept the revision it already had')
        self.assertEqual(self.tm('l').content['target'], '使用守护！')
        maintenance.unlock('l', project='p')
        self.assertIsNone(maintenance.locked('l', 'p'))
        self.assertTrue(report.items)

    def test_a_row_that_moved_underneath_the_plan_is_a_conflict_not_an_overwrite(self):
        maintenance = self.translate_all()
        event = self.event(maintenance)
        maintenance.agent = StubAgent({
            'w': TranslationOutcome('w', 1, 'conflict', failure='VersionConflict: expected 1'),
            'l': TranslationOutcome('l', 1, 'committed', target='使用守护！',
                                    translation=MemoryRef('tm:l', 2), context_hash='ctx')})
        report = maintenance.apply(event, 'r', 'g')
        self.assertEqual(len(report.conflicts), 1)
        self.assertIn('w', report.conflicts[0])
        self.assertEqual(report.counts['unresolved'], 1)
        self.assertEqual(self.tm('w').version, 1, 'Nothing was written over the newer row')

    def test_rollback_appends_the_last_different_wording_as_a_new_version(self):
        maintenance = self.translate_all()
        maintenance.apply(self.event(maintenance), 'r', 'g')
        rejected = self.tm('w')
        self.assertEqual(rejected.content['target'], '守护吸收伤害。')
        restored = maintenance.rollback(rejected.ref, run_id='r', snapshot_id='g', reason='Reviewer preferred v1')
        self.assertEqual(restored.content['target'], '护盾吸收伤害。')
        self.assertEqual(restored.version, rejected.version + 1, 'History is appended, never rewritten')
        self.assertTrue(self.store.is_valid(restored.ref))
        self.assertFalse(self.store.is_valid(rejected.ref), 'The rejected revision is retired, not erased')
        self.assertIn('maintenance:rollback', restored.content['check']['reviewer'])
        self.assertEqual(maintenance.audit()[-1]['action'], 'rollback')

    def test_revision_uses_the_version_the_plan_inspected(self):
        maintenance = self.translate_all()
        event = self.event(maintenance)
        recorder = StubAgent({
            'w': TranslationOutcome('w', 1, 'committed', target='守护吸收伤害。',
                                    translation=MemoryRef('tm:w', 2), context_hash='ctx'),
            'l': TranslationOutcome('l', 1, 'committed', target='使用守护！',
                                    translation=MemoryRef('tm:l', 2), context_hash='ctx')})
        maintenance.agent = recorder
        maintenance.apply(event, 'r', 'g')
        self.assertEqual(sorted(recorder.runs), [('l', 1), ('w', 1)], 'Every re-review declares its base version')

    def test_export_carries_the_diff_and_the_deliberate_savings(self):
        maintenance = self.translate_all()
        report = maintenance.apply(self.event(maintenance), 'r', 'g')
        exported = MaintenanceService.export(report)
        self.assertEqual(exported['revised'], 2)
        self.assertEqual(exported['untouched'], 1, 'The camp row was re-anchored, never re-worded')
        self.assertEqual(exported['counts'], {'no_change': 1, 'needs_revision': 2, 'locked': 0, 'unresolved': 0})
        old, new = next(i for i in exported['items'] if i['decision'] == 'needs_revision')['diff']
        self.assertNotEqual(old, new)
        self.assertEqual(json.loads(json.dumps(exported, ensure_ascii=False, default=str))['candidates'], 3)

    def test_a_re_review_that_keeps_the_wording_is_reported_as_no_change(self):
        maintenance = self.translate_all()
        event = self.event(maintenance)
        self.model.targets = FIRST  # The new glossary turns out not to move these wordings.
        report = maintenance.apply(event, 'r', 'g')
        self.assertEqual(report.counts['needs_revision'], 0)
        self.assertEqual({i.occurrence_id for i in report.untouched}, {'w', 'l', 'c'})
        self.assertIn('Re-review kept', next(i for i in report.items if i.occurrence_id == 'w').reason)
        self.assertEqual(self.tm('w').content['target'], '护盾吸收伤害。')

    def test_replaying_a_committed_event_costs_no_second_revision(self):
        maintenance = self.translate_all()
        event = self.event(maintenance)
        maintenance.apply(event, 'r', 'g')
        versions = {key: self.tm(key).version for key in ('w', 'l', 'c')}
        calls = self.budget.calls
        again = maintenance.apply(event, 'r', 'g')
        self.assertEqual(again.counts['needs_revision'], 0)
        self.assertEqual(again.model_calls, (), 'A replay asks the model for nothing')
        self.assertEqual({key: self.tm(key).version for key in ('w', 'l', 'c')}, versions, 'Nothing was rewritten')
        self.assertEqual(self.budget.calls, calls)

    def test_a_dry_run_prices_the_work_without_paying_for_it(self):
        maintenance = self.translate_all()
        before = {key: self.tm(key).version for key in ('w', 'l', 'c')}
        report = maintenance.apply(self.event(maintenance), 'r', 'g', dry_run=True)
        self.assertEqual(report.counts['unresolved'], 2, 'The plan names the rows it would re-review')
        self.assertEqual(report.counts['needs_revision'], 0)
        self.assertEqual(report.model_calls, ())
        self.assertEqual({key: self.tm(key).version for key in ('w', 'l', 'c')}, before, 'Nothing was written')
        self.assertTrue(maintenance.audit()[-1]['dry_run'])

    def test_an_unknown_discovery_mode_is_refused_before_any_work(self):
        with self.assertRaises(ValueError):
            MaintenanceConfig(discovery='guess')


class OptimisticCommitTests(unittest.TestCase):
    """M6 relies on M2's version contract: a stale writer cannot fork a translation row."""

    def test_replacing_a_row_requires_the_version_that_was_inspected(self):
        store, path = MemoryStore(), Path(tempfile.mkdtemp()) / 'r.csv'
        self.addCleanup(store.close)
        with path.open('w', newline='', encoding='utf-8') as handle:
            handle.write('id,source_text,text_type,context\nx,Ward absorbs damage.,skill_description,\n')
        graph = GraphStore(store)
        occurrence = import_benchmark([path], 'p', graph, batch_size=1).occurrences[0]
        graph.create_snapshot('g', 'p')
        store.create_snapshot('k', 'p')
        store.start_run('r', 'p', None, 'en', 'zh', 'k', mode='game_resources')
        saved = store.commit_translation(occurrence, 'r', '护盾吸收伤害。', TranslationCheck(
            occurrence.occurrence_id, 1, text_hash('护盾吸收伤害。'), 'ctx', 'agent:reviewer', True))
        self.assertEqual(saved.version, 1)
        with self.assertRaises(ValueError):
            store.commit_translation(occurrence, 'r', '守护吸收伤害。', TranslationCheck(
                occurrence.occurrence_id, 1, text_hash('守护吸收伤害。'), 'ctx', 'agent:reviewer', True))
        self.assertEqual(store.latest_version(saved.record_id), 1)
        replaced = store.commit_translation(occurrence, 'r', '守护吸收伤害。', TranslationCheck(
            occurrence.occurrence_id, 1, text_hash('守护吸收伤害。'), 'ctx', 'agent:reviewer', True),
            expected_version=1)
        self.assertEqual((replaced.version, store.is_valid(saved.ref)), (2, False))


if __name__ == '__main__':
    unittest.main()
