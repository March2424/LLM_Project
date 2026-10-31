"""Check the source-only Flare extraction export and its native clues."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from scripts.export_flare_entity_input import export

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/flare/v0.2'
OUTPUT = ROOT / 'data/flare/flare_entity_input_v0.2.csv'
NATIVE = ROOT / 'data/flare/entity_input_background.jsonl'


class FlareEntityInputTest(unittest.TestCase):
    def test_export_preserves_records_and_native_clues(self):
        sources = [json.loads(line) for line in (DATA / 'source.jsonl').read_text().splitlines()]
        origins = {row['record_id']: row for row in
                   (json.loads(line) for line in (DATA / 'background/provenance.jsonl').read_text().splitlines())}
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'input.csv'
            export(DATA / 'source.jsonl', NATIVE, output)
            self.assertEqual(output.read_bytes(), OUTPUT.read_bytes())
            with output.open(encoding='utf-8', newline='') as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, ['id', 'source_text', 'text_type', 'context'])
                rows = list(reader)
        self.assertEqual(len(rows), len(sources))
        self.assertEqual(len({row['id'] for row in rows}), len(sources))
        for row, source in zip(rows, sources):
            self.assertEqual((row['id'], row['source_text'], row['text_type']),
                             (source['record_id'], source['source_text'], source['text_type']))
            origin = origins[row['id']]
            field = origin['source']['field']
            if field in ('you', 'him', 'her'):
                self.assertIn('utterance_role=' + ('player' if field == 'you' else 'npc'), row['context'])
            for condition in origin.get('conditions', []):
                self.assertIn(condition['field'] + '=' + condition['value'], row['context'])
        self.assertTrue(any('utterance_role=player' in row['context'] for row in rows))
        self.assertTrue(any('utterance_role=npc' in row['context'] for row in rows))
        self.assertTrue(any(not row['context'] for row in rows))

    def test_native_references_ui_and_action_conditions(self):
        with OUTPUT.open(encoding='utf-8', newline='') as stream:
            rows = {row['id']: row for row in csv.DictReader(stream)}
        self.assertIn('book=books/merchants_journal.txt', rows['flr_2379a63a396e1802d10b']['context'])
        self.assertEqual(rows['flr_b3f982c935b23adc903f']['context'], 'panel=audio; option=SFX')
        player = rows['flr_c40fc839a723285f7b3a']['context']
        self.assertIn('utterance_role=player', player)
        self.assertIn('requires_not_status=emp_journal_init', player)
        self.assertIn('set_status=emp_journal_init', player)
        # Ambiguous descriptions do not inherit the adjacent item's name or ID.
        self.assertTrue(all('item_name=' not in r['context'] and 'group_id=' not in r['context'] for r in rows.values()))

    @unittest.skipUnless((ROOT / 'artifacts/flare_full_v0.2/raw').is_dir(), 'Optional raw snapshots missing')
    def test_every_native_clue_has_matching_raw_evidence(self):
        raw = ROOT / 'artifacts/flare_full_v0.2/raw'
        files = {}
        for row in map(json.loads, NATIVE.read_text().splitlines()):
            for clue in row['native_context']:
                evidence = clue['source']
                path = raw / evidence['repository'].split('/')[-1] / evidence['path']
                if path not in files:
                    files[path] = path.read_text(encoding='utf-8-sig').splitlines()
                line = files[path][evidence['line'] - 1].strip()
                if clue['field'] == 'utterance_role':
                    self.assertTrue(line.startswith(('you=' if clue['value'] == 'player' else ('him=', 'her='))))
                elif clue['field'] in ('panel', 'option'):
                    self.assertIn(clue['value'].upper() if clue['field'] == 'panel' else clue['value'], line)
                else:
                    self.assertEqual(line, evidence['field'] + '=' + clue['value'])


if __name__ == '__main__':
    unittest.main()
