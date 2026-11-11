"""Autonomous routing boundaries with explicit fixtures, never quality experiment results."""
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest

from langgraph.checkpoint.sqlite import SqliteSaver

from localization_agent.data.benchmark import import_benchmark
from localization_agent.memory import MemoryStore, MemoryService
from localization_agent.models.protocols import ModelReply
from localization_agent.retrieval import GraphStore, GraphRetriever, GraphConfig
from localization_agent.schemas.memory import MemoryRef, TranslationCheck
from localization_agent.workflow.game import GameSession
from localization_agent.workflow.langgraph_game import build_workflow
from localization_agent.workflow.quality import assess_draft, format_checks


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.calls = []
        self.mode = 'format'
        self.cfg = {'configurable': {'thread_id': 'test-thread'}, 'max_concurrency': 1}
        path = self.folder / 'input.csv'
        path.write_text('id,source_text,text_type,context\na,Buy Price: %d %s,system_message,\n'
                        'b,Ward is an organization.,description,\nc,Ward,ui_label,\n', encoding='utf-8')
        store = MemoryStore(self.folder / 'memory.sqlite')
        try:
            graph = GraphStore(store)
            self.rows = import_benchmark([path], 'p', graph).occurrences
            self.occurrence = self.rows[0]
            graph.create_snapshot('g', 'p')
            store.create_snapshot('k', 'p')
            store.start_run('r', 'p', None, 'en', 'zh', 'k', mode='game_resources')
        finally:
            store.close()

    def model(self, task, prompt):
        self.calls.append(task)
        if task == 'translate_game':
            text = '护盾' if self.mode == 'reselect' else '购买价格：%d'
        elif task == 'repair_game':
            text = ('组织' if self.mode == 'reselect' else '购买价格：%d' if self.mode == 'stalled' else '购买价格：%d %s')
        elif task == 'extract_terms':
            text = '[]'
        elif task == 'review_game_autonomous':
            data = json.loads(prompt.split('\nInput: ')[-1])
            result = {'action': 'accept', 'reason': 'fixture adequate', 'issues': [], 'selected_ids': []}
            if self.mode == 'invalid':
                return ModelReply('not JSON', 'synthetic-fixture')
            if self.mode == 'stop':
                result.update(action='stop', reason='fixture: required identity absent')
            if self.mode == 'invalid_ids':
                result.update(action='reselect', selected_ids=['c99'], reason='fixture invalid pool ID')
            if self.mode == 'no_context':
                result.update(action='reselect', selected_ids=[], reason='fixture unchanged context')
            if self.mode == 'reselect' and data['actual_translator_context'] == '[]':
                result.update(action='reselect', selected_ids=['c01'], reason='Need organization definition for Ward')
            # Deliberately wrong accept in the format fixture: deterministic checks MUST override it.
            text = json.dumps(result)
        else:
            raise AssertionError(task)
        return ModelReply(text, 'synthetic-fixture')

    @contextmanager
    def runtime(self, selector=None):
        store = MemoryStore(self.folder / 'memory.sqlite', check_same_thread=False)
        try:
            session = GameSession(GraphRetriever(GraphStore(store), MemoryService(store, chat=self.model),
                                                 GraphConfig(max_soft_tokens=None)))
            with SqliteSaver.from_conn_string(str(self.folder / 'checkpoints.sqlite')) as saver:
                yield store, session, build_workflow(session, saver, selector=selector)
        finally:
            store.close()

    def initial(self, max_repairs=2):
        return {'occurrence': asdict(self.occurrence), 'run_id': 'r', 'graph_snapshot': 'g',
                'operation_prefix': 'test-thread', 'max_repairs': max_repairs, 'max_reselections': 1,
                'repair_count': 0, 'reselection_count': 0, 'reviews': [], 'history': []}

    def test_resume_after_generation_repair_and_idempotent_commit(self):
        with self.runtime() as (store, _, app):
            app.invoke(self.initial(), self.cfg, interrupt_after=['generate_draft'])
            self.assertFalse(store.visible(store.view('r')))
            self.assertEqual(self.calls, ['translate_game'])
        with self.runtime() as (store, session, app):
            app.invoke(None, self.cfg)
            state = app.get_state(self.cfg).values
            self.assertEqual(state['status'], 'committed')
            self.assertEqual(state['repair_count'], 1)
            self.assertEqual([r['action'] for r in state['reviews']], ['repair', 'accept'])
            self.assertEqual(state['reviews'][0]['model_action'], 'accept')
            saved = store.get(MemoryRef(**state['outcome']['translation']))
            self.assertTrue(saved.reviewer.startswith('ai:auto-review:'))
            self.assertEqual(saved.content['target'], '购买价格：%d %s')
            count = len(self.calls)
            repeat = session.complete_review(MemoryRef(**state['draft']), TranslationCheck(**saved.content['check']))
            self.assertEqual(json.loads(json.dumps(asdict(repeat))), state['outcome'])
            assess_draft(session, MemoryRef(**state['draft']))
            self.assertEqual(len(self.calls), count)  # review cache and commit receipt both survive replay

    def test_reselection_changes_context_and_requires_fresh_review(self):
        self.mode, self.occurrence = 'reselect', self.rows[2]
        with self.runtime(selector=lambda pool: ()) as (store, _, app):
            app.invoke(self.initial(), self.cfg)
            state = app.get_state(self.cfg).values
            self.assertEqual(state['status'], 'committed')
            self.assertEqual([r['action'] for r in state['reviews']], ['reselect', 'accept'])
            self.assertEqual(state['reselection_count'], 1)
            self.assertNotEqual(state['history'][0]['context_hash'], state['history'][1]['context_hash'])
            saved = store.get(MemoryRef(**state['outcome']['translation']))
            self.assertEqual(saved.content['check']['context_hash'], state['history'][1]['context_hash'])
            self.assertNotEqual(state['reviews'][0]['draft'], state['reviews'][1]['draft'])

    def test_source_change_cannot_submit_old_draft(self):
        with self.runtime() as (store, _, app):
            app.invoke(self.initial(), self.cfg, interrupt_after=['generate_draft'])
            store.put_source(replace(self.occurrence, source_version=2, source='New Price: %d %s'))
            app.invoke(None, self.cfg)
            self.assertEqual(app.get_state(self.cfg).values['status'], 'failed')
            self.assertFalse(store.visible(store.view('r')))

    def test_repair_limit_stop_and_invalid_json_never_write_tm(self):
        for index, mode in enumerate(('format', 'stop', 'invalid', 'invalid_ids', 'no_context')):
            self.mode = mode
            cfg = {'configurable': {'thread_id': f'case-{index}'}, 'max_concurrency': 1}
            with self.runtime(selector=lambda pool: ()) as (store, _, app):
                app.invoke({**self.initial(max_repairs=0), 'operation_prefix': f'case-{index}'}, cfg)
                expected = {'format': 'repair_limit', 'stop': 'unresolved', 'invalid': 'failed',
                            'invalid_ids': 'failed', 'no_context': 'unresolved_context'}[mode]
                self.assertEqual(app.get_state(cfg).values['status'], expected)
                self.assertFalse(store.visible(store.view('r')))

    def test_unchanged_repair_stops_without_old_approval(self):
        self.mode = 'stalled'
        with self.runtime() as (store, _, app):
            app.invoke(self.initial(), self.cfg)
            self.assertEqual(app.get_state(self.cfg).values['status'], 'stalled')
            self.assertEqual(self.calls.count('review_game_autonomous'), 1)
            self.assertFalse(store.visible(store.view('r')))

    def test_crash_after_tm_before_receipt_does_not_recommit(self):
        with self.runtime() as (store, session, app):
            app.invoke(self.initial(), self.cfg)
            state = app.get_state(self.cfg).values
            saved = store.get(MemoryRef(**state['outcome']['translation']))
            with store.db:
                store.db.execute('DELETE FROM game_review_receipts')
            count = len(self.calls)
            result = session.complete_review(MemoryRef(**state['draft']), TranslationCheck(**saved.content['check']))
            self.assertEqual(result.translation, saved.ref)
            self.assertEqual(result.status, 'committed_with_warnings')
            self.assertEqual(len(self.calls), count)

    def test_approval_is_bound_to_exact_target(self):
        with self.runtime() as (store, _, app):
            app.invoke(self.initial(), self.cfg, interrupt_before=['commit_and_update'])
            self.assertEqual(app.get_state(self.cfg).values['status'], 'approved')
            self.assertFalse(store.visible(store.view('r')))
            app.update_state(self.cfg, {'target': '篡改后的译文'}, as_node='auto_review')
            app.invoke(None, self.cfg)
            self.assertEqual(app.get_state(self.cfg).values['status'], 'failed')
            self.assertFalse(store.visible(store.view('r')))

    def test_printf_order_tags_and_explicit_length(self):
        with self.runtime() as (_, session, _):
            check = lambda source, target, meta={}: format_checks(source, target, meta, session.placeholder_pattern)
            self.assertTrue(check('Buy %d %s', '购买 %s %d'))
            self.assertFalse(check('%1$d %2$s', '%2$s %1$d'))
            self.assertFalse(check('%%d %s', '%s %%d'))
            self.assertTrue(check('<b><i>A</i></b>', '<b><i>甲</b></i>'))
            self.assertTrue(check('Go', '前往目的地', {'max_length': '2'}))
            self.assertFalse(check('Go', '前往目的地'))


if __name__ == '__main__':
    unittest.main()
