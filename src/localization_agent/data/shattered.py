"""Source-only extraction of the pinned Shattered Pixel Dungeon language bundles.

Java is parsed, never executed. Literal calls retain separate source positions;
dynamic receivers/keys remain explicitly unresolved rather than guessed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil

REVISION = '57a4e06a4caf162446d1c28caa7983f0493fecf0'
REPOSITORY = '00-Evan/shattered-pixel-dungeon'
PACKAGE = 'com.shatteredpixel.shatteredpixeldungeon.'
BUNDLES = ('actors', 'items', 'journal', 'levels', 'misc', 'plants', 'scenes', 'ui', 'windows')
MESSAGE_ROOT = Path('core/src/main/assets/messages')


def unescape(value: str) -> str:
    """Java Properties escapes; unlike unicode_escape, preserve literal UTF-8."""
    return re.sub(r'\\u([0-9a-fA-F]{4})|\\(.)', lambda m: chr(int(m[1], 16)) if m[1]
                  else {'n': '\n', 'r': '\r', 't': '\t', 'f': '\f'}.get(m[2], m[2]), value)


def properties(path: Path) -> dict[str, tuple[str, int]]:
    result = {}
    lines = iter(enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1))
    for number, line in lines:
        line = line.lstrip()
        if not line or line.startswith(('#', '!')):
            continue
        while (len(line) - len(line.rstrip('\\'))) % 2:
            _, continuation = next(lines)
            line = line[:-1] + continuation.lstrip()
        split = re.search(r'(?<!\\)(?:\\\\)*[=:\s]', line)
        if split:
            # Include escaped backslashes in the key, not the separator.
            pos = split.end() - 1
            key, rest = line[:pos], line[pos:]
            rest = rest.lstrip()
            value = rest[1:].lstrip() if rest.startswith(('=', ':')) else rest
        else:
            key, value = line, ''
        key = unescape(key)
        if key in result:
            raise ValueError(f'Duplicate key {key} at {path}:{number}')
        result[key] = (unescape(value), number)
    return result


def walk(node):
    yield node
    for child in node.named_children:
        yield from walk(child)


def text(node) -> str:
    return node.text.decode('utf-8') if node else ''


def location(path: str, node) -> dict:
    return {'path': path, 'line': node.start_point.row + 1,
            'column': node.start_point.column + 1, 'byte_offset': node.start_byte}


def enclosing_class(node, package: str) -> str | None:
    names = []
    while node:
        if node.type in ('class_declaration', 'enum_declaration', 'interface_declaration', 'record_declaration'):
            names.append(text(node.child_by_field_name('name')))
        # A bare `this` in an anonymous class is NOT the outer named class.
        if node.type == 'class_body' and node.parent.type == 'object_creation_expression':
            return None
        node = node.parent
    return package + '.' + '$'.join(reversed(names)) if names else None


def native_clues(node, path: str) -> list[dict]:
    clues = []
    child, parent = node, node.parent
    while parent:
        if parent.type == 'if_statement':
            condition = parent.child_by_field_name('condition')
            if child != condition:
                branch = 'then' if child == parent.child_by_field_name('consequence') else 'else'
                clues.append({'field': 'condition', 'value': text(condition) if branch == 'then'
                              else '!'+text(condition), 'source': location(path, condition)})
        elif parent.type == 'switch_block_statement_group':
            switch = parent.parent.parent
            condition = switch.child_by_field_name('condition')
            labels = [c for c in parent.named_children if c.type == 'switch_label']
            for label in labels:
                clues.append({'field': 'switch', 'value': text(condition), 'source': location(path, condition)})
                clues.append({'field': 'case', 'value': text(label), 'source': location(path, label)})
        elif parent.type == 'method_invocation':
            method = text(parent.child_by_field_name('name'))
            receiver = text(parent.child_by_field_name('object'))
            if method == 'yell':
                clues.append({'field': 'display', 'value': 'yell', 'source': location(path, parent)})
            elif method == 'tell' and '/actors/mobs/npcs/' in path:
                clues.append({'field': 'display', 'value': 'tell', 'source': location(path, parent)})
            elif receiver == 'GLog' and method in ('i', 'w', 'p', 'n', 'h'):
                clues.append({'field': 'display', 'value': 'GLog.' + method, 'source': location(path, parent)})
        child, parent = parent, parent.parent
    return list(reversed(clues))


def java_calls(raw: Path, keys: set[str]) -> tuple[dict, list, list]:
    from tree_sitter import Language, Parser
    import tree_sitter_java
    parser = Parser(Language(tree_sitter_java.language()))
    calls, unresolved, inputs = defaultdict(list), [], []
    for path in sorted(raw.rglob('*.java')):
        if '.git' in path.parts:
            continue
        data = path.read_bytes()
        tree = parser.parse(data)
        if tree.root_node.has_error:
            raise ValueError(f'Java parse error: {path}')
        rel = path.relative_to(raw).as_posix()
        inputs.append(rel)
        package = next((text(n).removeprefix('package ').rstrip(';') for n in tree.root_node.named_children
                        if n.type == 'package_declaration'), '')
        imports = {}
        for n in tree.root_node.named_children:
            if n.type == 'import_declaration':
                imported = text(n).removeprefix('import ').rstrip(';')
                imports[imported.split('.')[-1]] = imported
        for node in walk(tree.root_node):
            if node.type != 'method_invocation' or text(node.child_by_field_name('object')) != 'Messages' or text(node.child_by_field_name('name')) != 'get':
                continue
            args = node.child_by_field_name('arguments').named_children
            owner, key = None, None
            if args and args[0].type == 'string_literal':
                key = unescape(text(args[0])[1:-1])
            elif len(args) >= 2 and args[1].type == 'string_literal':
                suffix = unescape(text(args[1])[1:-1])
                receiver = text(args[0])
                if args[0].type == 'class_literal':
                    name = receiver.removesuffix('.class')
                    parts = name.split('.')
                    owner = imports.get(parts[0], package + '.' + parts[0])
                    owner += ''.join('$' + p for p in parts[1:])
                elif receiver == 'this':
                    owner = enclosing_class(node, package)
                elif receiver.endswith('.this'):
                    name = receiver.removesuffix('.this')
                    owner = package + '.' + name.replace('.', '$')
                if owner and owner.startswith(PACKAGE):
                    key = owner.removeprefix(PACKAGE).lower() + '.' + suffix.lower()
            evidence = {**location(rel, node), 'expression': text(node)}
            if key in keys:
                calls[key].append({**evidence, 'native_context': native_clues(node, rel),
                                   'receiver_dispatch': 'dynamic_this' if args and text(args[0]).endswith('this') else 'static'})
            else:
                unresolved.append({**evidence, 'candidate_key': key,
                    'reason': 'dynamic_key_or_receiver' if key is None else 'key_requires_inheritance_or_is_absent'})
    return calls, unresolved, inputs


def text_type(key: str, clues: list[dict]) -> str:
    _, _, field = key.rpartition('.')
    if any(c['field'] == 'display' and c['value'] in ('yell', 'tell') for c in clues):
        return 'dialogue'
    if any(c['field'] == 'display' and c['value'].startswith('GLog.') for c in clues):
        return 'system_message'
    if key.startswith(('actors.hero.talent.', 'actors.hero.abilities.', 'actors.hero.spells.')):
        return 'skill_name' if field in ('name', 'title', 'rat_title') else 'skill_description' if 'desc' in field else 'unknown'
    if key.startswith('items.'):
        if field.startswith('ac_'):
            return 'ui'
        return 'item_name' if field == 'name' else 'item_description' if 'desc' in field or field.startswith('stats_') else 'unknown'
    if key.startswith('actors.mobs.npcs.') and field.startswith(('intro_quest', 'quest_intro', 'reminder')):
        return 'dialogue'
    if key.startswith('actors.mobs.') and field == 'name':
        return 'character_name' if '.npcs.' in key else 'enemy_name'
    if key.startswith(('ui.', 'windows.', 'scenes.')):
        return 'ui'
    if key.startswith('journal.document.'):
        return 'narrative'
    if key.startswith('journal.'):
        return 'ui'
    return 'unknown'


def write_jsonl(path: Path, rows: list) -> None:
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


def build(raw: Path, output: Path) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output directory must be new or empty')
    source, reference, inputs = {}, {}, []
    for bundle in BUNDLES:
        for language, target in (('', source), ('_zh', reference)):
            rel = MESSAGE_ROOT / bundle / (bundle + language + '.properties')
            inputs.append(rel.as_posix())
            for key, (value, line) in properties(raw / rel).items():
                if key in target:
                    raise ValueError(f'Duplicate cross-bundle key: {key}')
                target[key] = {'text': value, 'path': rel.as_posix(), 'line': line}
    calls, unresolved, java_inputs = java_calls(raw, set(source))
    inputs += java_inputs
    rows, references, provenance, empty = [], [], [], []
    for key, definition in source.items():
        if not definition['text']:
            empty.append({'key': key, **definition})
            continue
        for use in calls.get(key) or [None]:
            clues = use['native_context'] if use else []
            identity = [definition['path'], key, use['path'] if use else None, use['byte_offset'] if use else None]
            rid = 'spd_' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20]
            rows.append({'id': rid, 'source_text': definition['text'], 'text_type': text_type(key, clues),
                         'context': '; '.join(c['field'] + '=' + c['value'] for c in clues)})
            ref = reference.get(key)
            if ref and ref['text']:
                references.append({'record_id': rid, 'reference_text': ref['text']})
            provenance.append({'record_id': rid, 'localization_key': key,
                'repository': REPOSITORY, 'revision': REVISION,
                'definition': {'path': definition['path'], 'line': definition['line']},
                'use': use, 'resolution': 'literal_call' if use else 'resource_definition',
                'reference_source': {k: ref[k] for k in ('path', 'line')} if ref else None,
                'alignment_status': 'aligned' if ref and ref['text'] else 'missing_reference'})
    output.mkdir(parents=True, exist_ok=True)
    background = output / 'background'
    background.mkdir()
    with (output / 'shattered_entity_input.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['id', 'source_text', 'text_type', 'context'], lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    write_jsonl(output / 'references.jsonl', references)
    write_jsonl(background / 'provenance.jsonl', provenance)
    write_jsonl(background / 'unresolved_calls.jsonl', unresolved)
    write_jsonl(background / 'empty_source_keys.jsonl', empty)
    shutil.copyfile(raw / 'LICENSE.txt', output / 'LICENSE.txt')
    (output / 'ATTRIBUTION.txt').write_text(
        'Shattered Pixel Dungeon — text and translations by Evan Debenham and upstream contributors/translators.\n'
        'Based on Pixel Dungeon by Oleg Dolya (Watabou).\n'
        f'Source: https://github.com/{REPOSITORY}/tree/{REVISION}\n'
        'License: GNU GPL version 3 or later; see LICENSE.txt and upstream source notices.\n'
        'This project adds extraction, occurrence IDs and source/reference separation.\n'
        'Upstream Chinese translations are unreviewed by this project and are not approved TM.\n', encoding='utf-8')
    manifest = {'schema_version': 'localization-text/0.2', 'profile': 'shattered-four-column-v1',
        'repository': REPOSITORY, 'revision': REVISION, 'source_language': 'en', 'reference_language': 'zh-Hans',
        'source_keys': len(source), 'nonempty_source_keys': len(source)-len(empty),
        'empty_source_keys': len(empty),
        'nonempty_reference_keys': sum(bool(reference.get(k, {}).get('text')) for k, v in source.items() if v['text']),
        'missing_reference_keys': [k for k, v in source.items() if v['text'] and not reference.get(k, {}).get('text')],
        'bundles': {b: {'source_keys': sum(v['path'].split('/')[-2] == b for v in source.values()),
                       'nonempty_source_keys': sum(v['path'].split('/')[-2] == b and bool(v['text']) for v in source.values())} for b in BUNDLES},
        'source_records': len(rows), 'reference_records': len(references),
        'missing_reference_records': len(rows)-len(references),
        'literal_call_records': sum(p['resolution'] == 'literal_call' for p in provenance),
        'resource_definition_records': sum(p['resolution'] == 'resource_definition' for p in provenance),
        'unresolved_calls': len(unresolved), 'by_type': dict(Counter(r['text_type'] for r in rows)),
        'records_with_context': sum(bool(r['context']) for r in rows),
        'split': 'unassigned', 'translation_quality': 'upstream_unreviewed',
        'coverage': 'All nonempty English keys in the nine runtime message bundles. Static literal uses are separate; unresolved dynamic and inherited uses are logged, not exhaustively expanded.',
        'source_visibility': 'CSV is source-only; references.jsonl is offline-only. Do not load Chinese properties into generation or extraction.',
        'adapter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'inputs': [{'path': path, 'sha256': hashlib.sha256((raw/path).read_bytes()).hexdigest()} for path in inputs],
        }
    (output / 'README.md').write_text(f'''# 破碎的像素地牢四列源侧数据 v4.0.2

固定上游提交：`{REVISION}`。采用 `localization-text/0.2` 的四列提取视图。

- [shattered_entity_input.csv](shattered_entity_input.csv)：模型源侧输入，仅有 `id,source_text,text_type,context` 四列，{len(rows):,} 条独立记录。
- [references.jsonl](references.jsonl)：离线中文参考，仅有 `record_id,reference_text` 两个字段，{len(references):,} 条；使用 CSV 的 id 关联。
- [background/provenance.jsonl](background/provenance.jsonl)：每条记录的语言键、定义位置、调用位置、原生上下文证据及缺译状态。
- [background/unresolved_calls.jsonl](background/unresolved_calls.jsonl)：动态键、动态接收者、继承回退等尚未静态解析的 {len(unresolved)} 个 Messages.get 调用，不是运行失败样本。
- [background/empty_source_keys.jsonl](background/empty_source_keys.jsonl)：{len(empty)} 个原文本身为空的资源键，保留在后台，不制造空的待翻译记录。
- [manifest.json](manifest.json)：数量、输入哈希、版本、覆盖边界与本目录文件哈希。

范围是游戏运行时加载的九组英文语言资源：UI、窗口、物品、角色、技能/天赋/法术、NPC 文本、日志等。全部 {len(source)-len(empty):,} 个非空英文键均有记录；重复原文不合并。可静态定位的同键不同调用点各有独立 ID；其余从资源定义保留一条，不能解读为只在游戏中出现一次。尚未穷举动态继承分派、动态键及运行时所有角色/场景组合，不承诺全部运行时 occurrence 已恢复。

context 只含 Java AST 明确包围调用的 if/else 条件、switch 分支及直接显示调用（yell/tell/GLog）。保留原始表达式，不把字段名当剧情事实，也不从类名猜测人物身份。调用本身位于条件求值中时不附加该条件；资源定义没有确定的调用上下文时留空。text_type 按原生语言字段和调用用途映射，无法确定时为 unknown；不会给未知项编造类型或背景。

文本保留 Java Properties 解码后的原文，包括换行、格式符和标记；参考缺失仍保留源侧记录。中文未做人工质量评级，不能作为批准 TM。仅为流程样本，split=unassigned，尚非正式评测集。参考文件与中文原始属性文件不得进入提取、检索、生成或在线审校。

复现和验证方式见 [接入说明](../../../docs/SHATTERED_DATA.md)。上游来源与许可见 [ATTRIBUTION.txt](ATTRIBUTION.txt) 和 [LICENSE.txt](LICENSE.txt)。本目录不包含游戏程序、图片或音频。
''', encoding='utf-8')
    manifest['files'] = [{'path': p.relative_to(output).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                         for p in sorted(output.rglob('*')) if p.is_file()]
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return manifest
