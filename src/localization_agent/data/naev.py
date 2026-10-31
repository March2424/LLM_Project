"""Export rebuilt Naev gettext sources by usage, with isolated reference states."""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import tomllib

from lxml import etree as ET
import polib
import yaml
from tree_sitter import Language, Parser
import tree_sitter_c
import tree_sitter_lua
import tree_sitter_rust

from .naev_resources import REVISION, ASSET_REVISION


def key(entry) -> tuple:
    return (entry.msgctxt or '', entry.msgid, entry.msgid_plural or '')


def text(node) -> str:
    return node.text.decode('utf-8') if node else ''


def walk(node):
    yield node
    for child in node.named_children:
        yield from walk(child)


def literal(node, language: str) -> str | None:
    if node is None:
        return None
    value = text(node)
    if language == 'lua' and node.type == 'string':
        long = re.fullmatch(r'\[(=*)\[([\s\S]*)\]\1\]', value)
        if long:
            return re.sub(r'^\r?\n', '', long[2], count=1)
        value = re.sub(r'\\z\s*', '', value[1:-1])
        pattern = r'\\u\{([0-9a-fA-F]+)\}|\\x([0-9a-fA-F]{2})|\\([0-9]{1,3})|\\(\r?\n|.)'
        def escape(m):
            if m[1] or m[2]: return chr(int(m[1] or m[2], 16))
            if m[3]: return chr(int(m[3], 10))
            return {'n':'\n','r':'\r','t':'\t','a':'\a','b':'\b','f':'\f','v':'\v','\r\n':'\n'}.get(m[4],m[4])
        return re.sub(pattern, escape, value)
    if node.type == 'concatenated_string':
        values = [literal(c, language) for c in node.named_children]
        return ''.join(values) if all(v is not None for v in values) else None
    if node.type == 'string_literal':
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return None
    if node.type == 'raw_string_literal':
        match = re.fullmatch(r'r(#+)?"([\s\S]*)"\1', value)
        return match[2] if match else value[value.index('"')+1:value.rindex('"')]
    return None


def where(path: str, node, offset: int = 0) -> dict:
    return {'path': path, 'line': node.start_point.row+1+offset, 'column': node.start_point.column+1,
            'byte_offset': node.start_byte}


def clues_for(node, path, offset=0) -> list:
    clues = []
    child, parent = node, node.parent
    while parent:
        if parent.type in ('if_statement', 'elseif_statement', 'if_expression'):
            condition = parent.child_by_field_name('condition')
            if condition and child != condition:
                positive = child == parent.child_by_field_name('consequence')
                clues.append({'field':'condition', 'value':text(condition) if positive else 'not ('+text(condition)+')',
                              'source':where(path, condition, offset)})
                if not positive:
                    for sibling in parent.named_children:
                        if sibling == child: break
                        if sibling.type == 'elseif_statement':
                            c = sibling.child_by_field_name('condition')
                            clues.append({'field':'condition','value':'not ('+text(c)+')','source':where(path,c,offset)})
        child, parent = parent, parent.parent
    return list(reversed(clues))


def lua_header(code: str, path: str) -> list:
    match = re.search(r'<(mission|event)\b[\s\S]*?</\1>', code)
    if not match: return []
    root = ET.fromstring(match[0].encode())
    result = []
    for field in ('cond', 'location', 'done'):
        for node in root.findall(field):
            if node.text and node.text.strip():
                result.append({'field':'offer_'+{'cond':'condition'}.get(field,field), 'value':node.text.strip(),
                               'source':{'path':path,'line':code[:match.start()].count('\n')+node.sourceline,'field':field}})
    return result


def code_occurrences(code: bytes, path: str, language: str, parsers: dict, catalog: dict,
                     offset=0, header=()) -> tuple[list, list, bool]:
    tree = parsers[language].parse(code)
    rows, unresolved = [], []
    # Only unique, literal local vn.newCharacter bindings are safe to reuse.
    # Reassignments, repeated names and dynamic names stay unknown.
    assignments=defaultdict(list)
    if language=='lua':
        for binding in walk(tree.root_node):
            if binding.type!='assignment_statement': continue
            children=binding.named_children
            if len(children)!=2: continue
            names=children[0].named_children;values=children[1].named_children
            if len(names)==1 and names[0].type=='identifier':
                assignments[text(names[0])].append((binding,values))

    def speaker_binding(name,node):
        bindings=assignments.get(name,[])
        if len(bindings)!=1: return None
        binding,values=bindings[0]
        if binding.parent.type!='variable_declaration' or len(values)!=1 or binding.start_byte>=node.start_byte: return None
        scope=binding.parent
        while scope and scope.type not in ('block','chunk'): scope=scope.parent
        ancestor=node
        while ancestor and ancestor!=scope:
            parameters=ancestor.child_by_field_name('parameters')
            if parameters and any(text(p)==name for p in parameters.named_children): return None
            ancestor=ancestor.parent
        if ancestor is None: return None
        call=values[0]
        if call.type!='function_call' or text(call.child_by_field_name('name'))!='vn.newCharacter': return None
        arg=call.child_by_field_name('arguments').named_children[0]
        if arg.type=='function_call' and text(arg.child_by_field_name('name'))=='_':
            arg=arg.child_by_field_name('arguments').named_children[0]
        value=literal(arg,'lua')
        return (value,binding) if value else None
    for node in walk(tree.root_node):
        if node.type not in ('function_call','call_expression'): continue
        function = node.child_by_field_name('name') if language=='lua' else node.child_by_field_name('function')
        name = text(function)
        short = name.split('::')[-1].split('.')[-1]
        signatures = {'_':(None,0,None),'N_':(None,0,None),'gettext':(None,0,None),
                      'gettext_noop':(None,0,None),'n_':(None,0,1),'ngettext':(None,0,1),
                      'p_':(0,1,None),'pgettext':(0,1,None)}
        if short not in signatures: continue
        args_node = node.child_by_field_name('arguments')
        args = args_node.named_children if args_node else []
        ci, si, pi = signatures[short]
        if len(args) <= max(i for i in (ci,si,pi) if i is not None): continue
        context = literal(args[ci],language) if ci is not None else ''
        singular = literal(args[si],language)
        plural = literal(args[pi],language) if pi is not None else ''
        identity = (context, singular, plural)
        if identity not in catalog:
            unresolved.append({**where(path,node,offset), 'reason':'dynamic_argument_or_not_in_rebuilt_catalog',
                               'function':name, 'literal_msgid':singular})
            continue
        clues = list(header)+clues_for(node,path,offset)
        kind = 'unknown'
        ancestor = node.parent
        while ancestor:
            if ancestor.type == 'function_call':
                caller = text(ancestor.child_by_field_name('name'))
                if caller in ('vn.na','vn.menu','vn.newCharacter','vntk.msg','vntk.yesno','tk.msg','tk.yesno','misn.setDesc','misn.setTitle'):
                    clues.append({'field':'display','value':caller,'source':where(path,ancestor,offset)})
                    kind = {'vn.na':'narrative','vn.menu':'dialogue_option','vn.newCharacter':'character_name',
                            'misn.setDesc':'quest_description','misn.setTitle':'quest_name'}.get(caller,'ui')
                    break
                speaker=speaker_binding(caller,ancestor)
                if speaker:
                    clues.append({'field':'speaker','value':speaker[0],'source':where(path,speaker[1],offset)})
                    kind='dialogue'
                    break
            ancestor = ancestor.parent
        if '/bioship/skills' in path:
            ancestor = node.parent
            while ancestor:
                if ancestor.type == 'field':
                    field = text(ancestor.child_by_field_name('name'))
                    if field in ('name','desc'):
                        kind = 'skill_name' if field=='name' else 'skill_description'
                    break
                ancestor = ancestor.parent
        if kind=='unknown' and path.startswith('src/'):
            kind = 'ui' if Path(path).stem.startswith(('menu','options','land','gui','dialogue','input')) else 'system_message'
        rows.append({'key':identity,'source':{**where(path,node,offset),'end_line':node.end_point.row+1+offset},'kind':kind,'clues':clues,'resolution':'literal_call'})
    return rows, unresolved, tree.root_node.has_error


def xml_occurrences(path: Path, rel: str, rules: list, catalog: dict) -> list:
    tree = ET.parse(str(path))
    root = tree.getroot()
    result = []
    for selector in rules:
        attribute = '/@' in selector
        for value in tree.xpath(selector):
            node = value.getparent() if attribute else value
            content = str(value) if attribute else ''.join(node.itertext())
            identity = ('',content,'')
            if identity not in catalog or not content: continue
            xpath = tree.getpath(node)+('/@'+selector.rsplit('/@',1)[1] if attribute else '')
            clues=[]
            field = selector.rsplit('/',1)[-1]
            kind='unknown'
            skill = '/bioship/skills/' in rel or '/flow/abilities/' in rel
            if root.tag in ('outfit','commodity'):
                kind = ('skill_name' if skill else 'item_name') if field in ('@name','name','display','shortname') else ('skill_description' if skill else 'item_description') if field in ('description','desc_extra','condstr') else 'item_property'
                for native in ('slot','size','license'):
                    for extra in root.findall('general/'+native):
                        if extra.text:
                            clues.append({'field':native,'value':extra.text,'source':{'path':rel,'line':extra.sourceline,'xpath':tree.getpath(extra)}})
            elif root.tag=='ship': kind='ship_name' if field in ('@name','display') else 'ship_description' if field in ('description','desc_extra') else 'unknown'
            elif root.tag in ('spob','ssys'): kind='location_name' if field in ('@name','display') else 'narrative' if field in ('description','bar','features','feature') else 'unknown'
            elif root.tag=='faction': kind='faction_name' if field in ('@name','display','longname','mapname') else 'narrative'
            elif root.tag=='effect': kind='skill_name' if field=='@name' else 'skill_description'
            result.append({'key':identity,'source':{'path':rel,'line':node.sourceline,'xpath':xpath},
                           'kind':kind,'clues':clues,'resolution':'xml_field'})
    return result


def markdown_occurrences(path: Path, rel: str, catalog: dict, parsers: dict) -> tuple[list,list]:
    code = path.read_text()
    rows, unresolved = [], []
    head = re.match(r'^---\n([\s\S]*?)---\n',code)
    start = head.end() if head else 0
    meta = yaml.safe_load(head[1]) if head else {}
    def header_line(field):
        found=re.search(r'^'+field+r'\s*:',head[1],re.M) if head else None
        return 2+head[1][:found.start()].count('\n') if found else 1
    clues = [{'field':'availability_condition','value':str(meta['cond']), 'source':{'path':rel,'line':header_line('cond'),'field':'cond'}}] if meta and meta.get('cond') else []
    if meta and meta.get('title') and ('',str(meta['title']),'') in catalog:
        rows.append({'key':('',str(meta['title']),''),'source':{'path':rel,'line':header_line('title'),'field':'title'},'kind':'ui','clues':clues,'resolution':'markdown_title'})
    body=code[start:]
    rendered, positions=[],[]
    cursor=0
    for block in re.finditer(r'<%[^=][\s\S]*?%>',body):
        segment=body[cursor:block.start()]
        rendered.append(segment);positions.extend(range(start+cursor,start+block.start()))
        fragment=block[0][2:-2]
        part, missing, _ = code_occurrences(fragment.encode(),rel,'lua',parsers,catalog,
                                            code[:start+block.start()+2].count('\n'),clues)
        for row in part:
            row['source']['template_block_offset']=start+block.start()
        # Fragment parsers report local byte positions; normalize them back to
        # the actual template rather than publishing misleading file offsets.
        prefix=code[:start+block.start()+2]
        for evidence in [r['source'] for r in part]+[c['source'] for r in part for c in r['clues']]+missing:
            if 'byte_offset' in evidence:
                evidence['byte_offset']+=len(prefix.encode())
                if evidence['line']==prefix.count('\n')+1:
                    evidence['column']+=len(prefix.rsplit('\n',1)[-1].encode())
        rows.extend(part);unresolved.extend(missing)
        rendered.append('<%lua%>');positions.extend([start+block.start()]*7)
        cursor=block.end()
    rendered.append(body[cursor:]);positions.extend(range(start+cursor,len(code)))
    rendered=''.join(rendered)
    cursor=0
    for line in rendered.splitlines(keepends=True):
        value=line.rstrip('\r\n')
        if value and not (value.strip().startswith('<%') and value.strip().endswith('%>')):
            identity=('',value,'')
            if identity in catalog:
                original=positions[cursor]
                rows.append({'key':identity,'source':{'path':rel,'line':code[:original].count('\n')+1,'byte_offset':len(code[:original].encode())},
                             'kind':'narrative','clues':clues,'resolution':'markdown_line'})
        cursor+=len(line)
    return rows,unresolved


def write_jsonl(path: Path, rows) -> None:
    with path.open('w',encoding='utf-8') as stream:
        for row in rows: stream.write(json.dumps(row,ensure_ascii=False)+'\n')


def export(source: Path, generation: dict, output: Path) -> dict:
    if output.exists() and any(output.iterdir()): raise ValueError('Output must be new or empty')
    pot=polib.pofile(str(source/'po/naev-rebuilt.pot'))
    catalog={key(e):e for e in pot if not e.obsolete and e.msgid}
    zh={key(e):e for e in polib.pofile(str(source/'po/zh_Hans.po')) if not e.obsolete and e.msgid}
    parsers={lang:Parser(Language(module.language())) for lang,module in [('lua',tree_sitter_lua),('c',tree_sitter_c),('rust',tree_sitter_rust)]}
    occurrences,unresolved,parse_warnings=[],[],[]
    rules=ET.parse(str(source/'po/its/translation.its')).xpath('//*[local-name()="translateRule"][@translate="yes"]/@selector')
    inputs=set(generation['potfiles'])
    for rel in generation['potfiles']:
        path=source/rel
        if path.suffix=='.xml': occurrences.extend(xml_occurrences(path,rel,rules,catalog))
        elif path.suffix in ('.lua','.c','.h','.rs'):
            language='lua' if path.suffix=='.lua' else 'rust' if path.suffix=='.rs' else 'c'
            content=path.read_text()
            rows,missing,errors=code_occurrences(content.encode(),rel,language,parsers,catalog,
                                                header=lua_header(content,rel) if language=='lua' else ())
            occurrences.extend(rows);unresolved.extend(missing)
            if errors:parse_warnings.append(rel)
    for path in sorted((source/'build/dat/outfits').rglob('*.xml')):
        rel=path.relative_to(source).as_posix();inputs.add(rel)
        occurrences.extend(xml_occurrences(path,rel,rules,catalog))
    for path in sorted((source/'dat/naevpedia').rglob('*.md'))+sorted((source/'build/dat/naevpedia').rglob('*.md')):
        rel=path.relative_to(source).as_posix();inputs.add(rel)
        rows,missing=markdown_occurrences(path,rel,catalog,parsers)
        occurrences.extend(rows);unresolved.extend(missing)
    for directory,fields,context in [('damagetype',('name','display'),'damagetype'),('slots',('display','description'),'slotproperty')]:
        for path in sorted((source/'dat'/directory).glob('*.toml')):
            rel=path.relative_to(source).as_posix();inputs.add(rel)
            data=tomllib.loads(path.read_text())
            for field in fields:
                identity=(context,data.get(field),'')
                if identity in catalog:
                    occurrences.append({'key':identity,'source':{'path':rel,'field':field},'kind':'skill_description' if field=='description' else 'ui','clues':[],'resolution':'toml_field'})
    rel='dat/start.toml';inputs.add(rel);data=tomllib.loads((source/rel).read_text())
    for field in ('ship_name','ship_acquired','scenario_name'):
        identity=('',data.get(field),'')
        if identity in catalog:occurrences.append({'key':identity,'source':{'path':rel,'field':field},'kind':'ui','clues':[],'resolution':'toml_field'})
    for path in [source/'dat/AUTHORS',*sorted((source/'assets/gfx/loading').glob('*.txt'))]:
        rel=path.relative_to(source).as_posix();inputs.add(rel)
        for number,line in enumerate(path.read_text().splitlines(),1):
            identity=('',line,'')
            if line and not line.startswith('[') and identity in catalog:
                occurrences.append({'key':identity,'source':{'path':rel,'line':number},'kind':'credits','clues':[],'resolution':'credit_line'})
    # Keep every rebuilt source entry, including generator-only and dependency
    # templates. Such fallback definitions are not asserted to be runtime uses.
    # Rust/C macro token trees may hide calls from the grammar. Preserve every
    # additional genuine gettext file/line location, not just one row per key.
    located=defaultdict(list)
    for row in occurrences:located[(row['key'],row['source']['path'])].append(row['source'])
    for identity,entry in catalog.items():
        for rel,line in entry.occurrences:
            if Path(rel).suffix not in ('.lua','.c','.h','.rs','.py') or not (source/rel).is_file() or not line.isdigit(): continue
            number=int(line)
            if any(p.get('line',0)<=number<=p.get('end_line',p.get('line',0)) for p in located[(identity,rel)]):continue
            occurrences.append({'key':identity,'source':{'path':rel,'line':number},
                                'kind':'system_message' if rel.startswith('src/') else 'unknown',
                                'clues':[],'resolution':'gettext_location'})
    represented={r['key'] for r in occurrences}
    for identity,entry in catalog.items():
        if identity not in represented:
            occurrences.append({'key':identity,'source':{'path':'po/naev-rebuilt.pot','line':entry.linenum},'kind':'unknown',
                                'clues':[],'resolution':'catalog_definition','catalog_locations':entry.occurrences})
    # Stable source ordering and position + gettext identity, never reference text.
    unique={}
    for row in occurrences:
        token=json.dumps([row['source'],row['key']],sort_keys=True,ensure_ascii=False)
        if token not in unique:unique[token]=row
    occurrences=[unique[k] for k in sorted(unique)]
    output.mkdir(parents=True,exist_ok=True);background=output/'background';background.mkdir()
    rows,refs,provenance=[],[],[]
    states=Counter();kstates={}
    for item in occurrences:
        identity=item['key'];entry=catalog[identity];ref=zh.get(identity)
        value=(ref.msgstr_plural.get(0,'') if ref.msgid_plural else ref.msgstr) if ref else ''
        fuzzy=bool(ref and 'fuzzy' in ref.flags)
        status='fuzzy' if fuzzy else 'translated' if value else 'missing'
        rid='naev_'+hashlib.sha256(json.dumps([item['source'],identity],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
        clues=list(item['clues'])
        if identity[0]:clues.insert(0,{'field':'gettext_context','value':identity[0],'source':item['source']})
        for form,value_source in [('singular',entry.msgid),*([('plural',entry.msgid_plural)] if entry.msgid_plural else [])]:
            record_id=rid+('_plural' if form=='plural' else '')
            form_clues=clues+([{'field':'plural_form','value':form,'source':item['source']}] if entry.msgid_plural else [])
            rows.append({'id':record_id,'source_text':value_source,'text_type':item['kind'] if value_source.strip() else 'formatting',
                         'context':'; '.join(c['field']+'='+str(c['value']) for c in form_clues)})
            refs.append({'record_id':record_id,'reference_text':value,'status':status,'fuzzy':fuzzy,
                         'reference_available':bool(value),'match':'exact_gettext_identity' if ref else 'no_catalog_entry',
                         'flags':ref.flags if ref else [],
                         'source_form':form,'reference_plural_index':0 if entry.msgid_plural else None})
            provenance.append({'record_id':record_id,'repository':'naev/naev','revision':REVISION,
                'source':item['source'],'msgctxt':identity[0],'msgid':entry.msgid,'msgid_plural':entry.msgid_plural,
                'source_form':form,'context_evidence':form_clues,'resolution':item['resolution'],
                'formatting_only':not bool(value_source.strip()),
                'reference_status':status,'reference_line':ref.linenum if ref else None,
                'catalog_locations':item.get('catalog_locations',[])})
            states[status]+=1
        kstates[identity]=status
    with (output/'naev_entity_input.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['id','source_text','text_type','context'],lineterminator='\n');writer.writeheader();writer.writerows(rows)
    write_jsonl(output/'references.jsonl',refs)
    write_jsonl(background/'provenance.jsonl',provenance)
    write_jsonl(background/'unresolved_calls.jsonl',unresolved)
    write_jsonl(background/'parse_warnings.jsonl',[{'path':p,'status':'partial_syntax_tree','note':'catalog definitions retain untranslated source coverage'} for p in parse_warnings])
    write_jsonl(background/'source_catalog.jsonl',[{'msgctxt':e.msgctxt or '', 'msgid':e.msgid,'msgid_plural':e.msgid_plural,'locations':e.occurrences} for e in catalog.values()])
    write_jsonl(output/'orphan_references.jsonl',[{'msgctxt':e.msgctxt or '', 'msgid':e.msgid,'msgid_plural':e.msgid_plural,
        'reference_text':e.msgstr,'reference_plural':e.msgstr_plural,'flags':e.flags,'status':'not_in_rebuilt_source_catalog'} for k,e in zh.items() if k not in catalog])
    (background/'generation.json').write_text(json.dumps(generation,ensure_ascii=False,indent=2)+'\n')
    shutil.copyfile(source/'LICENSE',output/'LICENSE.txt');shutil.copyfile(source/'gpl.txt',output/'GPL-3.0.txt')
    shutil.copyfile(Path(__file__).resolve().parents[3]/'licenses/CC-BY-SA-4.0.txt',output/'CC-BY-SA-4.0.txt')
    (output/'ATTRIBUTION.txt').write_text(f'Naev text and translations by the Naev Dev Team and upstream contributors/translators.\nSource: https://github.com/naev/naev/tree/{REVISION}\nAssets: https://codeberg.org/naev/naev-assets-lossy/src/commit/{ASSET_REVISION}\nCode/Lua GPL-3.0 with upstream version-specific terms; other files including XML CC BY-SA 4.0 or later except where noted. See LICENSE.txt.\nThis project adds extraction and source/reference separation; translations are upstream and unreviewed, not approved TM.\n')
    inputs.update(g['generator'] for g in generation['generated'])
    inputs.update(p for g in generation['generated'] for p in g['inputs'])
    inputs.update(p.relative_to(source).as_posix() for root in ('dat/utils','utils','po') for p in (source/root).rglob('*.py') if p.is_file())
    inputs.update(p.relative_to(source).as_posix() for p in (source/'dat').rglob('meson.build'))
    inputs.add('po/its/translation.its')
    manifest={'schema_version':'localization-text/0.2','profile':'naev-four-column-v1','revision':REVISION,'asset_revision':ASSET_REVISION,
        'source_language':'en','reference_language':'zh-Hans','source_catalog_entries':len(catalog),'source_records':len(rows),
        'reference_status_records':dict(states),'reference_status_catalog_entries':dict(Counter(kstates.values())),
        'by_type':dict(Counter(r['text_type'] for r in rows)),'by_resolution':dict(Counter(p['resolution'] for p in provenance)),
        'formatting_records':sum(r['text_type']=='formatting' for r in rows),
        'records_with_context':sum(bool(r['context']) for r in rows),'generated_files':len(generation['generated']),
        'unresolved_calls':len(unresolved),'parse_warning_files':len(parse_warnings),
        'split':'unassigned','translation_quality':'upstream_unreviewed',
        'reference_policy':'Exact msgctxt+msgid+msgid_plural only; fuzzy flags preserved without automatic approval. Singular/plural English forms remain separate; Chinese plural index 0 is shared.',
        'coverage':'All entries in rebuilt official gettext source catalog; source-locatable fields/calls separated, dynamic uses and fallback catalog definitions explicitly logged.',
        'adapter_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'resource_builder_sha256':hashlib.sha256(Path(__file__).with_name('naev_resources.py').read_bytes()).hexdigest(),
        'inputs':[{'path':p,'sha256':hashlib.sha256((source/p).read_bytes()).hexdigest()} for p in sorted(inputs)],
        'reference_catalog_sha256':hashlib.sha256((source/'po/zh_Hans.po').read_bytes()).hexdigest()}
    (output/'README.md').write_text(f'''# Naev 固定快照四列本地化文本

主仓库提交 `{REVISION}`，资源仓库提交 `{ASSET_REVISION}`。

- [naev_entity_input.csv](naev_entity_input.csv)：源侧输入，只有 `id,source_text,text_type,context` 四列，{len(rows):,} 条记录。
- [references.jsonl](references.jsonl)：每个 ID 对应一条离线中文记录，`status` 为 translated / missing / fuzzy，保留空译文和原始 fuzzy 标记。非空非 fuzzy 也未经过本项目质量审核。
- [background/provenance.jsonl](background/provenance.jsonl)：原生字段／调用位置、版本、英文 gettext 身份、上下文证据与参考状态。
- [background/generation.json](background/generation.json)：已实际生成的装备与百科文件、生成器、输入和哈希。
- [background/source_catalog.jsonl](background/source_catalog.jsonl)：重建后的 {len(catalog):,} 个源 gettext 条目，供覆盖审计，不是模型公共背景包装。
- [background/unresolved_calls.jsonl](background/unresolved_calls.jsonl)：动态参数或非目录调用；相应资源定义照常保留。
- [background/parse_warnings.jsonl](background/parse_warnings.jsonl)：部分语法树的源码文件，gettext 位置和目录定义提供回补，不静默删文本。
- [orphan_references.jsonl](orphan_references.jsonl)：旧中文目录中不再对应当前源清单的项，仅离线保存，不并入源侧。

逐记录状态：translated={states['translated']:,}，missing={states['missing']:,}，fuzzy={states['fuzzy']:,}。缺译和 fuzzy 都有源侧行，参考状态不增加 CSV 列，也不改变源侧 ID。复数英文单复数分成独立 ID，在 context 标记 plural_form；中文按其单一复数类别索引 0 对齐，参考记录明确标注源形式。

已覆盖固定源码按官方 gettext 规则标记的 UI／系统文字、任务剧情、装备物品、技能与能力说明、地点、阵营、百科和加载文字等。每个可定位的源码位置／XML XPath／资源定义独立编号，不按原文合并。原生 context 来自 gettext 语义上下文、Lua/C/Rust if 分支、任务 offer 条件、百科可见条件、明确的显示调用、唯一字面量角色绑定及 XML 装备元数据。未知身份、类型和动态条件不靠模型补写。

这不是全部运行时字符串组合的穷举：动态分派、动态 gettext 参数和部分宏语法需进一步解析。全量覆盖指当前重建 gettext 清单；未国际化硬编码、图片字和第三方模组不在此范围。split=unassigned，尚未进行真实翻译或正式评测。

CSV 中原样保留 3 条纯空格格式条目，text_type=formatting。它们参与全文本覆盖和参考状态统计；现有 MemoryStore 拒绝纯空白源文，普通翻译／建图任务导入时需按类型单独处理，不将其误报为数据丢失或擅自改写空格。

模型只读 CSV；中文 references、orphan_references 以及上游中文 PO 仅用于允许的离线核对／评测，不进入提取、检索、生成或在线审校。

复现与实际验证见 [Naev 接入说明](../../../docs/NAEV_DATA.md)。来源及许可见 [ATTRIBUTION.txt](ATTRIBUTION.txt)、[LICENSE.txt](LICENSE.txt)、[GPL-3.0.txt](GPL-3.0.txt)、[CC-BY-SA-4.0.txt](CC-BY-SA-4.0.txt)。
''',encoding='utf-8')
    manifest['files']=[{'path':p.relative_to(output).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                       for p in sorted(output.rglob('*')) if p.is_file()]
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return manifest
