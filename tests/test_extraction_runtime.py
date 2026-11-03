"""Offline service/budget and graph contract checks; no remote calls."""
import csv
import io
import json
import runpy
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from localization_agent.data.benchmark import import_benchmark, plan_batches
from localization_agent.memory import MemoryStore, MemoryService
from localization_agent.models.chat_completions import (ChatCompletionsModel, CallBudget, BudgetExceeded,
                                                       RetryableModelError)
from localization_agent.models.protocols import ModelReply
from localization_agent.retrieval import GraphStore
from localization_agent.retrieval.builder import AutoGraphBuilder
from localization_agent.schemas.extraction import extraction_schema


class ExtractionRuntimeTests(unittest.TestCase):
    @patch.dict('os.environ', {'TEST_LLM_KEY': 'test-only-value'})
    def test_keepalive_whitespace_cannot_extend_total_deadline(self):
        model = ChatCompletionsModel(endpoint='https://example.invalid/chat/completions',
            model='test-model', api_key_env='TEST_LLM_KEY', total_timeout=2)
        response = self.response()
        with patch('localization_agent.models.chat_completions.urlopen', return_value=response), \
             patch('localization_agent.models.chat_completions.time.monotonic', side_effect=[0, 1, 3]), \
             patch.object(response, 'read1', return_value=b' ') as read:
            with self.assertRaises(RetryableModelError):
                model('teacher_select', 'Return JSON')
            self.assertEqual(read.call_count, 1)
            self.assertTrue(response.closed)
            self.assertEqual(model.budget.calls, 1)  # Unknown charge remains reserved.

    def response(self, finish='stop', usage=True):
        reply = {'model': 'test-model', 'choices': [
            {'message': {'content': '{"entities":[],"relations":[]}'}, 'finish_reason': finish}]}
        if usage:
            reply['usage'] = {'prompt_tokens': 12, 'completion_tokens': 8}
        return io.BytesIO(json.dumps(reply).encode())

    @patch.dict('os.environ', {'TEST_LLM_KEY': 'test-only-value'})
    def test_http_contract_shared_budget_and_actual_usage(self):
        budget = CallBudget(max_calls=1, max_tokens=10000, max_cost=1)
        model = ChatCompletionsModel(endpoint='https://example.invalid/chat/completions',
            model='test-model', api_key_env='TEST_LLM_KEY', max_output_tokens=128,
            input_price_per_million=1, output_price_per_million=2, budget=budget)
        with patch('localization_agent.models.chat_completions.urlopen', return_value=self.response()) as send:
            reply = model('discover_graph', 'Return JSON')
            payload = json.loads(send.call_args.args[0].data)
            self.assertEqual(payload['response_format'], {'type': 'json_object'})
            self.assertEqual(payload['max_tokens'], 128)
            self.assertEqual((reply.input_tokens, reply.output_tokens), (12, 8))
            self.assertAlmostEqual(reply.cost, 28 / 1000000)
            self.assertEqual(budget.tokens, 20)
            with self.assertRaises(BudgetExceeded):
                model('discover_graph', 'Return JSON')
            self.assertEqual(send.call_count, 1)

    @patch.dict('os.environ', {'TEST_LLM_KEY': 'test-only-value'})
    def test_truncation_is_charged_and_not_silently_parsed(self):
        model = ChatCompletionsModel(endpoint='https://example.invalid/chat/completions',
            model='test-model', api_key_env='TEST_LLM_KEY', max_output_tokens=128)
        with patch('localization_agent.models.chat_completions.urlopen', return_value=self.response('length')) as send:
            with self.assertRaisesRegex(RuntimeError, 'truncated'):
                model('discover_graph', 'Return JSON')
            self.assertEqual(model.budget.tokens, 20)
            self.assertEqual(send.call_count, 1)
        unpriced = ChatCompletionsModel(endpoint='https://example.invalid/chat/completions',
            model='test-model', api_key_env='TEST_LLM_KEY', budget=CallBudget(max_cost=1))
        with patch('localization_agent.models.chat_completions.urlopen') as send:
            with self.assertRaisesRegex(ValueError, 'prices'):
                unpriced('discover_graph', 'Return JSON')
            send.assert_not_called()

    def test_batch_size_adapts_to_source_and_output_lengths(self):
        rows = [{'id': str(i), 'source_text': 'Ward', 'text_type': 'item_name', 'context': ''}
                for i in range(100)]
        short = plan_batches(rows)
        long = plan_batches([{**r, 'source_text': 'A longer description. ' * 40} for r in rows])
        self.assertLess(len(short), len(long))
        self.assertEqual([i for batch in long for i in batch], list(range(100)))
        self.assertLess(max(map(len, short)), 64)  # Output reserve also limits short-name batches.
        with self.assertRaisesRegex(ValueError, 'exceeds batch budget'):
            plan_batches([{**rows[0], 'source_text': 'x' * 30000}])

    def test_grounded_inferred_object_link_and_formatting_skip(self):
        with tempfile.TemporaryDirectory() as directory, closing(MemoryStore()) as store:
            path = Path(directory) / 'source.csv'
            with path.open('w', newline='') as handle:
                writer = csv.writer(handle)
                writer.writerow(['id', 'source_text', 'text_type', 'context'])
                writer.writerow(['s', 'Mara wields Emberblade.', 'narrative', ''])
                writer.writerow(['spacing', '   ', 'formatting', ''])
            graph = GraphStore(store)
            inputs = import_benchmark([path], 'p', graph)
            self.assertEqual(inputs.skipped_formatting_ids, ('spacing',))
            def call(task, prompt):
                if task == 'discover_graph':
                    ev = [{'id': 's', 'field': 'source', 'quote': 'Mara wields Emberblade'}]
                    proposal = {'entities': [
                        dict(key='m', name='Mara', entity_type='character', reuse_id=None, evidence=ev),
                        dict(key='d', name='Emberblade', entity_type='item', reuse_id=None, evidence=ev)],
                        'relations': [dict(key='uses', source='entity:m', target='entity:d',
                            relation='USES', conditions={}, evidence=ev, basis='inferred',
                            reason='Wielding a weapon entails using it.')]}
                    proposal['relations'].append(dict(key='bad',source='text:s',target='entity:d',
                        relation='USES',conditions={},evidence=ev))
                    catalog = json.loads(prompt.split('\nExisting catalog: ')[1])
                    self.assertTrue(all(set(item) == {'id', 'name', 'entity_type'} for item in catalog))
                    return ModelReply(json.dumps(proposal), 'offline-fixture')
                self.fail('Graph extraction must not make an extra review call')
            memory = MemoryService(store, chat=call)
            result = AutoGraphBuilder(graph, memory, keep_valid_items=True).build(inputs.batches[0])
            self.assertEqual(len(result.entities), 2)
            edge = graph.get(result.edges[0])
            self.assertTrue(graph.valid(edge.ref))
            self.assertEqual(edge.attributes['basis'], 'inferred')
            self.assertEqual(edge.relation, 'USES')
            self.assertEqual(edge.attributes['verification'], 'program_only')
            self.assertEqual(len(result.rejected),1)
            self.assertIn('Wrong relation endpoint kinds',result.rejected[0]['error'])

    def test_graph_rollback_replays_logged_response_without_paid_call(self):
        with tempfile.TemporaryDirectory() as directory, closing(MemoryStore()) as store:
            path = Path(directory) / 'source.csv'
            path.write_text('id,source_text,text_type,context\ns,Mara,character_name,\n')
            graph = GraphStore(store)
            inputs = import_benchmark([path], 'p', graph)
            evidence = [{'id':'s','field':'source','quote':'Mara'}]
            proposal = {'entities':[dict(key='m',name='Mara',entity_type='character',
                reuse_id=None,evidence=evidence)], 'relations':[dict(key='mentions',source='text:s',
                target='entity:m',relation='MENTIONS',conditions={},evidence=evidence)]}
            from unittest.mock import Mock
            model = Mock(return_value=ModelReply(json.dumps(proposal),'offline-fixture'))
            builder = AutoGraphBuilder(graph,MemoryService(store,chat=model),reuse_logged_calls=True)
            with patch.object(graph,'put_edge',side_effect=ValueError('interrupted graph write')):
                with self.assertRaises(ValueError), graph.atomic():
                    builder.build(inputs.batches[0])
            count = store.db.execute("SELECT COUNT(*) FROM graph_items WHERE payload LIKE '%auto:entity:%'").fetchone()[0]
            self.assertEqual(count,0)
            with graph.atomic():
                result = builder.build(inputs.batches[0])
            self.assertEqual(model.call_count,1)
            self.assertEqual(len(result.edges),1)

    def test_published_schema_matches_python_vocabulary(self):
        path = Path(__file__).resolve().parents[1] / 'configs/extraction.schema.json'
        self.assertEqual(json.loads(path.read_text()), extraction_schema())

    @patch.dict('os.environ', {'TEST_LLM_KEY': 'test-only-value'})
    def test_cli_splits_invalid_batch_without_losing_source_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source.csv'
            source.write_text('id,source_text,text_type,context\na,Hello,ui,\nb,Bye,ui,\n')
            config=root/'config.json'
            config.write_text(json.dumps({'model':{'endpoint':'https://example.invalid/chat/completions',
                'model':'test-model','api_key_env':'TEST_LLM_KEY','max_output_tokens':128,'context_tokens':8192},
                'budget':{'max_calls':3,'max_tokens':10000},'max_batches':0}))
            script=Path(__file__).resolve().parents[1]/'scripts/run_benchmark_agent.py'
            result=root/'result.json'
            argv=[str(script),str(source),'--project','test','--config',str(config),'--db',str(root/'graph.sqlite'),
                  '--output',str(result),'--split-failures']
            invalid=io.BytesIO(json.dumps({'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':12,'completion_tokens':8}}).encode())
            with patch('sys.argv',argv),patch('localization_agent.models.chat_completions.urlopen',
                side_effect=[invalid,self.response(),self.response()]) as send:
                runpy.run_path(str(script),run_name='__main__')
                self.assertEqual(send.call_count,3)
            state=json.loads(result.read_text())
            self.assertEqual(state['status'],'complete')
            self.assertEqual(state['split_batches'],1)
            self.assertEqual(state['completed_rows'],2)

    @patch.dict('os.environ', {'TEST_LLM_KEY': 'test-only-value'})
    def test_cli_preserves_completed_batches_and_cost_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.csv'
            source.write_text('id,source_text,text_type,context\na,Hello,ui,\nb,Bye,ui,\n')
            config = root / 'config.json'
            config.write_text(json.dumps({'model': {
                'endpoint': 'https://example.invalid/chat/completions', 'model': 'test-model',
                'api_key_env': 'TEST_LLM_KEY', 'max_output_tokens': 128, 'context_tokens': 8192},
                'budget': {'max_calls': 2, 'max_tokens': 10000},
                'batch': {'max_rows': 1}, 'max_batches': 0}))
            result = root / 'result.json'
            script = Path(__file__).resolve().parents[1] / 'scripts/run_benchmark_agent.py'
            argv = [str(script), str(source), '--project', 'test', '--config', str(config),
                    '--db', str(root / 'graph.sqlite'), '--output', str(result)]
            with patch('sys.argv', argv), patch('localization_agent.models.chat_completions.urlopen',
                    side_effect=[self.response(), self.response('length')]) as send:
                with self.assertRaisesRegex(RuntimeError, 'truncated'):
                    runpy.run_path(str(script), run_name='__main__')
                # Each batch makes exactly one extraction request.
                self.assertEqual(send.call_count, 2)
            state = json.loads(result.read_text())
            self.assertEqual(state['status'], 'failed')
            self.assertEqual(len(state['builds']), 1)
            self.assertEqual(state['budget_usage']['calls'], 2)
            self.assertEqual(state['budget_usage']['tokens'], 40)
            self.assertIsNone(state['budget_usage']['cost'])
            settings = json.loads(config.read_text())
            settings['budget']['max_calls'] = 3
            config.write_text(json.dumps(settings))
            with patch('sys.argv', argv + ['--resume']), patch(
                    'localization_agent.models.chat_completions.urlopen', return_value=self.response()) as send:
                runpy.run_path(str(script), run_name='__main__')
                self.assertEqual(send.call_count, 1)
            state = json.loads(result.read_text())
            self.assertEqual(state['status'], 'complete')
            self.assertEqual(state['completed_rows'], 2)
            self.assertEqual(state['budget_usage']['calls'], 3)
            with patch('sys.argv', argv + ['--resume']), patch(
                    'localization_agent.models.chat_completions.urlopen') as send:
                runpy.run_path(str(script), run_name='__main__')
                send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
