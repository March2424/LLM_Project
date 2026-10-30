"""Key checks for complete coverage, identity and source/reference isolation."""
from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest
from localization_agent.data.flare_full import build_full, MODULE_NAMES

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless((ROOT / "artifacts/flare_full_v0.2/raw/flare-game/mods/empyrean_campaign/languages/data.zh.po").is_file(), "Optional local Flare snapshots are not downloaded")
class FlareFullTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.output=Path(cls.temp.name)
        cls.report=build_full(ROOT/'artifacts/flare_full_v0.2/raw',cls.output)
        cls.source=cls.read('source.jsonl')
        cls.refs=cls.read('references.jsonl')
        cls.provenance=cls.read('background/provenance.jsonl')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def read(cls,name):
        return [json.loads(l) for l in (cls.output/name).read_text().splitlines()]

    def test_complete_catalog_coverage_and_accounting(self):
        self.assertEqual(len(self.report['catalogs']),5)
        self.assertEqual(sum(c['template_keys'] for c in self.report['catalogs']),2464)
        for c in self.report['catalogs']:
            self.assertEqual(c['unrepresented_keys'],[])
        self.assertEqual(self.report['issues'],[])
        self.assertEqual(len(self.source),len(self.refs)+self.report['untranslated_records'])
        self.assertEqual(len({s['record_id'] for s in self.source}),len(self.source))
        self.assertEqual({r['record_id'] for r in self.refs},{r['record_id'] for r in self.provenance if r['alignment_status']=='aligned'})

    def test_context_and_distinct_reused_dialogue(self):
        rows=[s for s in self.source if s['source_text']=='Any luck finding my journal?' and s['context']['module']=='Empyrean 主战役']
        self.assertEqual(len(rows),3)
        self.assertEqual(len({r['record_id'] for r in rows}),3)
        self.assertEqual({place for r in rows for place in r['context']['locations']},{'Goblin Camp','Nazia Highlands','Halls of Infinity'})
        for r in rows:
            self.assertEqual(r['context']['speaker'],'Abasi, Traveling Merchant')
            self.assertEqual(r['context']['next'][0]['text'],'Not yet.')
        item=next(r for r in self.source if r['source_text']=='Mana flows inside its primitive exterior.')
        self.assertEqual(item['context']['item_name'],'Goblin Branch')
        potion=next(r for r in self.source if r['source_text']=='Restore 50% of maximum HP')
        self.assertNotIn('placeholders',potion['context'])
        inherited=next(r for r in self.source if r['source_text']=='Warrior' and r['context']['module']=='旧版缩放资源')
        ref=next(r for r in self.refs if r['record_id']==inherited['record_id'])
        self.assertEqual(ref['reference_text'],'战士')
        self.assertEqual(inherited['text_type'],'ui')

    def test_minimal_input_and_reference_isolation(self):
        def strings(value):
            if isinstance(value,str):
                yield value
            elif isinstance(value,dict):
                for v in value.values():yield from strings(v)
            elif isinstance(value,list):
                for v in value:yield from strings(v)
        for row in self.source:
            self.assertEqual(set(row),{'record_id','text_type','source_text','context'})
            for s in strings(row['context']):
                if s not in MODULE_NAMES.values():
                    self.assertFalse(any('\u3400'<=c<='\u9fff' for c in s),s)
        raw={r['record_id']:r for r in self.provenance}
        for ref in self.refs:
            self.assertEqual(ref['reference_text'],raw[ref['record_id']]['catalog']['raw_translation'])
            self.assertEqual(raw[ref['record_id']]['quality_status'],'upstream_unreviewed')


if __name__=='__main__':unittest.main()
