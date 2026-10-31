"""Naev reference states, source isolation, occurrences and native Lua context."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

import polib
from tree_sitter import Language, Parser
import tree_sitter_lua

from localization_agent.data.naev import code_occurrences, export


class NaevTest(unittest.TestCase):
    def test_lua_branches_and_explicit_speaker_without_guessing(self):
        parser=Parser(Language(tree_sitter_lua.language()))
        code=b'''local person=vn.newCharacter(_("Aster"))
if ready then person(_("Hello")) elseif later then vn.na(_("Wait")) else vn.na(_("Leave")) end
_(dynamic_text)
'''
        keys={('',s,''):None for s in ('Aster','Hello','Wait','Leave')}
        rows,unknown,error=code_occurrences(code,'dat/missions/fixture.lua','lua',{'lua':parser},keys)
        self.assertFalse(error)
        by_text={r['key'][1]:r for r in rows}
        self.assertEqual(by_text['Hello']['kind'],'dialogue')
        self.assertTrue(any(c['field']=='speaker' and c['value']=='Aster' for c in by_text['Hello']['clues']))
        self.assertEqual([c['value'] for c in by_text['Leave']['clues'] if c['field']=='condition'],['not (later)','not (ready)'])
        self.assertEqual(len(unknown),1)

    def test_duplicate_uses_plural_fuzzy_missing_and_reference_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source'
            for folder in ('src','po/its','dat/naevpedia','build/dat/outfits','build/dat/naevpedia','dat/damagetype','dat/slots','assets/gfx/loading'):
                (root/folder).mkdir(parents=True,exist_ok=True)
            (root/'dat/start.toml').write_text('')
            (root/'dat/AUTHORS').write_text('')
            (root/'po/its/translation.its').write_text('<rules/>')
            (root/'LICENSE').write_text('fixture');(root/'gpl.txt').write_text('fixture')
            (root/'src/example.c').write_text('''void f(){_("Hello");_("Hello");_("Blank");_("Review");_("New"); n_("One", "Many", n);p_("unit"," ");}''')
            pot=polib.POFile()
            for text in ('Hello','Blank','Review','New'):
                pot.append(polib.POEntry(msgid=text,occurrences=[('src/example.c','1')]))
            pot.append(polib.POEntry(msgid='One',msgid_plural='Many',occurrences=[('src/example.c','1')]))
            pot.append(polib.POEntry(msgctxt='unit',msgid=' ',occurrences=[('src/example.c','1')]))
            pot.save(str(root/'po/naev-rebuilt.pot'))
            zh=polib.POFile()
            zh.append(polib.POEntry(msgid='Hello',msgstr='参考专用甲'))
            zh.append(polib.POEntry(msgid='Blank',msgstr=''))
            zh.append(polib.POEntry(msgid='Review',msgstr='参考专用乙',flags=['fuzzy']))
            zh.append(polib.POEntry(msgid='One',msgid_plural='Many',msgstr_plural={0:'参考专用丙'}))
            zh.append(polib.POEntry(msgctxt='unit',msgid=' ',msgstr=' '))
            zh.save(str(root/'po/zh_Hans.po'))
            generation={'potfiles':['src/example.c'],'generated':[]}
            first=Path(tmp)/'out'
            m=export(root,generation,first)
            with (first/'naev_entity_input.csv').open(newline='') as stream:
                reader=csv.DictReader(stream);self.assertEqual(reader.fieldnames,['id','source_text','text_type','context']);rows=list(reader)
            self.assertEqual(len(rows),8)
            self.assertEqual(len({r['id'] for r in rows}),8)
            self.assertTrue(any(r['source_text']==' ' and r['text_type']=='formatting' for r in rows))
            self.assertEqual(sum(r['source_text']=='Hello' for r in rows),2)
            self.assertNotIn('参考专用',(first/'naev_entity_input.csv').read_text())
            self.assertEqual(m['reference_status_records'],{'translated':5,'missing':2,'fuzzy':1})
            refs=[json.loads(s) for s in (first/'references.jsonl').read_text().splitlines()]
            self.assertEqual({r['source_form'] for r in refs if r['reference_plural_index']==0},{'singular','plural'})
            self.assertTrue(any(r['fuzzy'] and r['reference_text']=='参考专用乙' for r in refs))
            before=(first/'naev_entity_input.csv').read_bytes()
            zh[0].msgstr='changed reference';zh[2].flags=[];zh.save(str(root/'po/zh_Hans.po'))
            second=Path(tmp)/'out2';export(root,generation,second)
            self.assertEqual(before,(second/'naev_entity_input.csv').read_bytes())


if __name__=='__main__':unittest.main()
