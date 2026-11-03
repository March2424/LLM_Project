"""Offline check that graph-only execution cannot start Teacher collection."""
import json
import runpy
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class MultidomainExtractionTests(unittest.TestCase):
    def test_graph_only_uses_source_direction_and_does_not_read_references_or_collect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'source').mkdir()
            text = '莫迪讨论能源补贴。'
            (root/'source/doc.csv').write_text('id,source_text,text_type,context\ns,'+text+',unknown,\n')
            unit = {'project_id':'news.train.zh-en.doc','csv':'source/doc.csv','source_records':1,
                    'source_lang':'zh','target_lang':'en','split':'train',
                    'graph_db':'graphs/doc/graph.sqlite','graph_result':'graphs/doc/result.json'}
            row = {'source_id':'s','project_id':unit['project_id'],'source_hash':sha256(text.encode()).hexdigest(),
                   'source_lang':'zh','target_lang':'en','split':'train'}
            (root/'extraction_units.jsonl').write_text(json.dumps(unit)+'\n')
            (root/'sources_manifest.jsonl').write_text(json.dumps(row)+'\n')
            (root/'offline').mkdir()
            (root/'offline/references.jsonl').write_text('invalid JSON: this file must never be read')
            config = json.loads(Path('configs/multidomain.extraction.json').read_text())
            config['model']['api_key_env'] = 'TEST_LLM_KEY'
            config_path = root/'config.json'
            config_path.write_text(json.dumps(config))
            script = Path('scripts/run_multidomain_collection.py').resolve()

            def graph_result(command, **kwargs):
                self.assertEqual(command[command.index('--source-lang')+1],'zh')
                self.assertEqual(command[command.index('--target-lang')+1],'en')
                result = Path(command[command.index('--output')+1])
                result.write_text(json.dumps({'status':'complete','budget_usage':{'calls':1,'cost':0}}))
                return SimpleNamespace(returncode=0)

            with patch.object(sys,'argv',[str(script),'--sources',str(root),'--extraction-config',str(config_path),
                    '--execute','--continue-after-pilot']), patch.object(sys,'path',[str(script.parent),*sys.path]), \
                    patch.dict('os.environ',{'TEST_LLM_KEY':'offline-fixture'}), \
                    patch('subprocess.run',side_effect=graph_result) as graph, \
                    patch('localization_agent.selection.teacher.prepare') as teacher_prepare, \
                    patch('localization_agent.selection.teacher.run') as teacher_run, \
                    patch('prepare_multidomain_teacher.prepare_plan') as teacher_plan:
                runpy.run_path(str(script),run_name='__main__')
                self.assertEqual(graph.call_count,1)
                teacher_plan.assert_not_called()
                teacher_prepare.assert_not_called()
                teacher_run.assert_not_called()
            self.assertEqual(json.loads((root/'pipeline.json').read_text())['status'],
                             'graphs_complete_teacher_not_started')


if __name__ == '__main__':
    unittest.main()
