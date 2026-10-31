"""Source/reference isolation and conservative Java occurrence/context extraction."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from localization_agent.data.shattered import BUNDLES, MESSAGE_ROOT, build, java_calls, properties


class ShatteredTest(unittest.TestCase):
    def test_properties_preserve_unicode_newlines_and_escaped_separators(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.properties'
            path.write_text('a=中文\\n%s\\u0021\nescaped\\=key : value\ncontinued=one\\\n  two\nempty=\n', encoding='utf-8')
            self.assertEqual(properties(path), {'a': ('中文\n%s!', 1), 'escaped=key': ('value', 2),
                                               'continued': ('onetwo', 3), 'empty': ('', 5)})

    def test_calls_have_separate_positions_and_conservative_conditions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Thing.java').write_text('''package com.shatteredpixel.shatteredpixeldungeon;
class Thing { void f() {
 if (ready) { Messages.get(Thing.class, "message"); }
 else { Messages.get(Thing.class, "message"); }
 if (Messages.get(Thing.class, "message").isEmpty()) {}
 Runnable r = new Runnable() { public void run() { Messages.get(this, "message"); } };
 Messages.get(Thing.class, dynamicKey);
} }''')
            calls, unresolved, _ = java_calls(root, {'thing.message'})
            self.assertEqual(len(calls['thing.message']), 3)
            self.assertEqual(len({c['byte_offset'] for c in calls['thing.message']}), 3)
            conditions = [[c['value'] for c in x['native_context']] for x in calls['thing.message']]
            self.assertEqual(conditions, [['(ready)'], ['!(ready)'], []])
            self.assertEqual(len(unresolved), 2)

    def test_all_definitions_and_missing_reference_survive_without_reference_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'raw'
            for bundle in BUNDLES:
                folder = root / MESSAGE_ROOT / bundle
                folder.mkdir(parents=True)
                (folder / (bundle + '.properties')).write_text(f'{bundle}.example=Original %s\n')
                (folder / (bundle + '_zh.properties')).write_text(f'{bundle}.example=秘密参考\n' if bundle != 'items' else '')
            (root / 'LICENSE.txt').write_text('test fixture license')
            first = Path(tmp) / 'one'
            manifest = build(root, first)
            self.assertEqual(manifest['source_records'], 9)
            self.assertEqual(manifest['reference_records'], 8)
            self.assertEqual(manifest['missing_reference_records'], 1)
            source_bytes = (first / 'shattered_entity_input.csv').read_bytes()
            self.assertNotIn('秘密参考'.encode(), source_bytes)
            (root / MESSAGE_ROOT / 'actors/actors_zh.properties').write_text('actors.example=Different reference\n')
            second = Path(tmp) / 'two'
            build(root, second)
            self.assertEqual(source_bytes, (second / 'shattered_entity_input.csv').read_bytes())
            with (first / 'shattered_entity_input.csv').open(newline='') as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, ['id', 'source_text', 'text_type', 'context'])
                ids = {row['id'] for row in reader}
            refs = [json.loads(line) for line in (first / 'references.jsonl').read_text().splitlines()]
            self.assertTrue({row['record_id'] for row in refs} < ids)


if __name__ == '__main__':
    unittest.main()
