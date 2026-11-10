import csv
import json
import tempfile
import unittest
from pathlib import Path

from localization_agent.data.benchmark import import_benchmark
from localization_agent.memory import MemoryConfig, MemoryService, MemoryStore
from localization_agent.models.chat_completions import BudgetExceeded, CallBudget
from localization_agent.models.protocols import ModelReply
from localization_agent.retrieval import GraphConfig, GraphRetriever, GraphStore
from localization_agent.workflow.agent import AgentConfig, TranslationAgent
from localization_agent.workflow.journal import CommittedWithoutCheckpoint, RunJournal

PASS = {'passed': True, 'issues': []}
TERM_ISSUE = {'passed': False, 'issues': [{'kind': 'terminology', 'detail': 'Ward must be 护盾',
                                           'repairable': True, 'candidate_ids': []}]}
NO_EVIDENCE = {'passed': False, 'issues': [{'kind': 'missing_evidence',
                                            'detail': 'Cannot tell which Ward this is',
                                            'repairable': False, 'candidate_ids': []}]}


class ScriptedModel:
    """Deterministic contract fixture, not a translation-quality result.

    A plain string review is replayed verbatim, so malformed model output is testable.
    """

    def __init__(self, reviews, budget, repair_text='护盾吸收伤害。', translate_text='使用护盾！'):
        self.reviews = list(reviews)
        self.budget = budget
        self.repair_text = repair_text
        self.translate_text = translate_text
        self.calls = []

    def __call__(self, task, prompt):
        self.calls.append((task, prompt))
        if self.budget.calls >= self.budget.max_calls:
            raise BudgetExceeded('Call, token or configured money budget exhausted before request')
        self.budget.reserve(20, None)
        if task == 'translate_game':
            return self.reply(self.translate_text)
        if task == 'review_agent':
            if not self.reviews:
                raise AssertionError('Reviewer called more often than the script allows')
            verdict = self.reviews.pop(0)
            return self.reply(verdict if isinstance(verdict, str) else json.dumps(verdict, ensure_ascii=False))
        if task == 'repair':
            return self.reply(self.repair_text)
        if task == 'extract_terms':
            return self.reply('[]')
        return self.reply('fixture ' + task)

    @staticmethod
    def reply(text):
        return ModelReply(text, 'offline-contract-fixture', 10, 10, None)


class AgentWorkflowTests(unittest.TestCase):
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
            writer.writerow(['d', 'Ward absorbs damage.', 'skill_description', ''])
            writer.writerow(['l', 'Use Ward!', 'dialogue', 'speaker=Arin; Arin is a member of the Order'])
        self.input = import_benchmark([path], 'p', self.graph, batch_size=1)
        self.graph.create_snapshot('g', 'p')
        self.store.create_snapshot('k', 'p')
        self.store.start_run('r', 'p', None, 'en', 'zh', 'k', mode='game_resources')
        self.budget = CallBudget(max_calls=40, max_tokens=200000)
        self.occurrence = next(o for o in self.input.occurrences if o.occurrence_id == 'l')

    def agent(self, reviews, repair_text='护盾吸收伤害。', translate_text='使用护盾！', **config):
        self.model = ScriptedModel(reviews, self.budget, repair_text, translate_text)
        memory = MemoryService(self.store, MemoryConfig(summary_step=1), chat=self.model)
        retriever = GraphRetriever(self.graph, memory, GraphConfig(max_characters=50000, max_soft_tokens=None))
        return TranslationAgent(retriever, config=AgentConfig(**config))

    def outcome(self, agent, **kwargs):
        return agent.run(self.occurrence, 'r', 'g', **kwargs)

    def tasks(self):
        return [task for task, _ in self.model.calls]

    def test_passing_review_commits_and_both_roles_see_the_same_input(self):
        outcome = self.outcome(self.agent([PASS]))
        self.assertEqual(outcome.status, 'committed')
        record = self.store.get(outcome.translation)
        self.assertEqual(record.content['target'], '使用护盾！')
        self.assertEqual(record.content['check']['reviewer'], 'agent:reviewer')
        self.assertTrue(record.status == 'approved' and not outcome.issues and 'repair' not in self.tasks())
        context = record.content['context_manifest']['model_context']
        prompts = {task: prompt for task, prompt in self.model.calls}
        self.assertIn('Selected context: ' + context, prompts['translate_game'])
        self.assertIn('Supplied context: ' + context, prompts['review_agent'])

    def test_confirmed_defect_repairs_then_commits_the_revision(self):
        outcome = self.outcome(self.agent([TERM_ISSUE, PASS]))
        self.assertEqual((outcome.status, outcome.revisions), ('committed', 1))
        self.assertEqual(self.store.get(outcome.translation).content['target'], '护盾吸收伤害。')
        self.assertEqual(self.tasks().count('review_agent'), 2)

    def test_program_format_defect_reaches_the_repairer(self):
        agent = self.agent([PASS, PASS], translate_text='使用护盾<b>！', repair_text='使用护盾！')
        outcome = self.outcome(agent)
        self.assertEqual(outcome.status, 'committed')
        self.assertEqual(outcome.revisions, 1)
        self.assertEqual(self.store.get(outcome.translation).content['target'], '使用护盾！')
        first_review = [prompt for task, prompt in self.model.calls if task == 'review_agent'][0]
        self.assertIn('Placeholder or tag set differs from the source', first_review)

    def test_unrepairable_evidence_gap_stops_without_touching_tm(self):
        outcome = self.outcome(self.agent([NO_EVIDENCE]))
        self.assertEqual(outcome.status, 'missing_evidence')
        self.assertEqual(outcome.issues[0].kind, 'missing_evidence')
        self.assertIsNone(outcome.translation)
        self.assertFalse([r for r in self.store.visible(self.store.view('r')) if r.kind == 'translation'])
        self.assertEqual(self.tasks().count('translate_game'), 1, 'The loop must not spin or re-pay')

    def test_revision_budget_exhausts_to_needs_revision(self):
        outcome = self.outcome(self.agent([TERM_ISSUE] * 5, max_revisions=1))
        self.assertEqual(outcome.status, 'needs_revision')
        self.assertEqual(outcome.revisions, 1)
        self.assertIsNone(outcome.translation)
        self.assertIn('Revision budget exhausted', outcome.failure)

    def test_unparsable_reviewer_reply_is_reported_not_swallowed(self):
        outcome = self.outcome(self.agent(['not json at all']))
        self.assertEqual(outcome.status, 'failed')
        self.assertIn('not parsable JSON', outcome.failure)
        self.assertFalse(self.store.visible(self.store.view('r')))

    def test_budget_exhaustion_is_an_explicit_status(self):
        self.budget.max_calls = 1
        outcome = self.outcome(self.agent([PASS]))
        self.assertEqual(outcome.status, 'budget_exhausted')
        self.assertIsNone(outcome.translation)
        self.assertIn('budget', outcome.failure)

    def test_resume_replays_logged_calls_instead_of_paying_twice(self):
        agent = self.agent(['not json at all'])
        failed = self.outcome(agent)
        self.assertEqual(failed.status, 'failed')
        backend_calls, charged = len(self.model.calls), self.budget.calls

        self.model.reviews = []  # Any new backend call would fail this fixture outright.
        again = self.outcome(agent, reuse_calls=True)
        self.assertEqual(again.status, 'failed')
        self.assertEqual(len(self.model.calls), backend_calls)
        self.assertEqual(self.budget.calls, charged)
        self.assertEqual(len(again.model_calls), backend_calls)

    def test_journal_skips_finished_cases(self):
        journal = RunJournal(Path(self.tmp.name) / 'journal', run_id='r', config={'model': 'fixture'},
                             store=self.store)
        outcome = self.outcome(self.agent([PASS]))
        journal.record(outcome, self.budget.snapshot())
        self.assertEqual(journal.pending(['d', 'l']), ['d'])
        self.assertEqual(journal.counts(), {'committed': 1})

    def test_a_verdict_is_final_but_a_transport_failure_is_retried(self):
        journal = RunJournal(Path(self.tmp.name) / 'journal3', run_id='r', config={'m': 1}, store=self.store)
        journal.record(self.outcome(self.agent(['not json at all'])), self.budget.snapshot())
        self.assertEqual(journal.pending(['l']), ['l'], 'A failed row is not a translation verdict')
        journal.record(self.outcome(self.agent([NO_EVIDENCE])), self.budget.snapshot())
        self.assertEqual(journal.pending(['l']), [], 'An evidence gap is a stop for a human, not a retry')

    def test_committed_row_without_a_checkpoint_is_inspected_not_replayed(self):
        outcome = self.outcome(self.agent([PASS]))
        with self.assertRaises(CommittedWithoutCheckpoint):
            RunJournal(Path(self.tmp.name) / 'fresh', run_id='r', config={'model': 'fixture'}, store=self.store)
        self.assertTrue(outcome.translation)

    def test_outcome_roundtrips_through_the_journal(self):
        journal = RunJournal(Path(self.tmp.name) / 'journal2', run_id='r', config={'m': 1}, store=self.store)
        outcome = self.outcome(self.agent([TERM_ISSUE, PASS]))
        journal.record(outcome, self.budget.snapshot())
        self.assertEqual(journal.outcome('l'), outcome)
        self.assertEqual(journal.outcome('l').issues, outcome.issues)


if __name__ == '__main__':
    unittest.main()
