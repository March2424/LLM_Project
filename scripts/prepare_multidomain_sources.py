"""Prepare source-only unordered corpus inputs and isolated references; no API calls."""
import argparse
import csv
import json
import random
import re
from collections import Counter
from hashlib import sha256
from pathlib import Path


def digest(value):
    return sha256(value.encode()).hexdigest()


def paired_lines(left, right):
    a, b = left.read_text(encoding='utf-8-sig').splitlines(), right.read_text(encoding='utf-8-sig').splitlines()
    if len(a) != len(b):
        raise ValueError(f'Unaligned line counts: {left}, {right}')
    return list(zip(a, b))


def load_news(root):
    units, inputs = [], []
    folder = root / 'en-zh'
    files = sorted(folder.glob('en.*'), key=lambda p: int(p.suffix[1:]))
    if not files:
        raise ValueError(f'No en.N files in {folder}')
    for path in files:
        other = folder / ('zh' + path.suffix)
        lines = paired_lines(path, other)
        inputs.extend([path, other])
        # The reverse directory is a mirror, not additional independent examples.
        for original in (path, other):
            mirror = root / 'zh-en' / original.name
            if mirror.exists():
                if original.read_bytes() != mirror.read_bytes():
                    raise ValueError(f'Direction copies differ: {original}, {mirror}')
                inputs.append(mirror)
        units.append({'unit_id': 'nc_' + path.suffix[1:], 'boundary': 'upstream_file',
                      'origin': str(path), 'lines': lines})
    return units, inputs


def load_guofeng(root):
    left, right = root / 'test.zh', root / 'test.en'
    units, current, book, chapter = [], None, None, None
    for pos, (zh, en) in enumerate(paired_lines(left, right)):
        if zh.startswith('<') or en.startswith('<'):
            if zh != en:
                raise ValueError(f'GuoFeng tags differ at line {pos + 1}')
            match = re.fullmatch(r'<(BOOK|CHAPTER) id="([^"]+)">', zh)
            if match:
                if match[1] == 'BOOK':
                    book = match[2]
                else:
                    chapter = match[2]
                    current = {'unit_id': f'gf_{book}_{chapter}', 'boundary': 'upstream_book_chapter_tags',
                               'upstream_book_id': book, 'upstream_chapter_id': chapter,
                               'origin': str(left), 'lines': []}
                    units.append(current)
            elif zh == '</CHAPTER>':
                current = None
            elif zh == '</BOOK>':
                book = None
            else:
                raise ValueError(f'Unknown GuoFeng tag at line {pos + 1}: {zh}')
        else:
            if current is None:
                raise ValueError(f'GuoFeng text outside chapter at line {pos + 1}')
            current['lines'].append((en, zh))
    if not units:
        raise ValueError('No GuoFeng chapters found')
    # Aggregate tagged files are authoritative; suffixed files are not imported again.
    return units, [left, right]


def news_splits(units, seed):
    """Keep shared long literal templates in the same split, across both languages."""
    parent = {u['unit_id']: u['unit_id'] for u in units}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    owners = {}
    for unit in units:
        for pair in unit['lines']:
            for lang, text in zip(('en', 'zh'), pair):
                value = ' '.join(text.casefold().split())
                if len(value) < 24:
                    continue
                value = re.sub(r'\b\d+(?:\.\d+)?\b', '<slot>', value)
                key = (lang, value)
                if key in owners:
                    parent[find(unit['unit_id'])] = find(owners[key])
                owners[key] = unit['unit_id']
    groups = sorted({find(u['unit_id']) for u in units})
    if len(groups) < 2:
        raise ValueError('Cannot create a disjoint News Commentary file-group holdout')
    random.Random(seed).shuffle(groups)
    heldout = set(groups[:max(1, round(len(groups) * .2))])
    return {u['unit_id']: ('test' if find(u['unit_id']) in heldout else 'train') for u in units}


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')


def prepare(news, guofeng, output, seed):
    nc, nc_inputs = load_news(news)
    gf, gf_inputs = load_guofeng(guofeng)
    splits = news_splits(nc, seed)
    output.mkdir(parents=True, exist_ok=False)
    manifests, references, extraction_units, skipped = [], [], [], []
    for corpus, units, directions in [('news_commentary_v18.1', nc, [('en', 'zh'), ('zh', 'en')]),
                                      ('guofeng_v1_test2', gf, [('zh', 'en')])]:
        for unit in units:
            split = splits[unit['unit_id']] if corpus.startswith('news') else 'adaptation_seen'
            for source_lang, target_lang in directions:
                # Held-out Chinese is a test reference and must never be a source view.
                if split == 'test' and source_lang == 'zh':
                    continue
                direction = source_lang + '-' + target_lang
                project = f'{corpus}.{split}.{direction}.{unit["unit_id"]}'
                relative = Path('source') / corpus / split / direction / (unit['unit_id'] + '.csv')
                path = output / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                rows = []
                for pos, (en, zh) in enumerate(unit['lines']):
                    source, reference = (en, zh) if source_lang == 'en' else (zh, en)
                    occurrence_id = f'{corpus}:{unit["unit_id"]}:{source_lang}:{pos:05d}'
                    if not source.strip():
                        skipped.append({'source_id': occurrence_id, 'reason': 'empty_source',
                                        'reference_nonempty': bool(reference.strip())})
                        continue
                    rows.append({'id': occurrence_id, 'source_text': source,
                                 'text_type': 'unknown', 'context': ''})
                    manifests.append({'source_id': occurrence_id, 'project_id': project, 'corpus': corpus,
                        'unit_id': unit['unit_id'], 'split': split, 'source_lang': source_lang,
                        'target_lang': target_lang, 'source_version': 1, 'position': pos,
                        'source_csv': str(relative), 'source_hash': digest(source),
                        'source_visibility': 'all_within_unit', 'order_semantics': 'unordered_resources',
                        'sft_allowed': split != 'test', 'evaluation_role': 'heldout' if split == 'test'
                            else 'seen' if split == 'adaptation_seen' else None,
                        'reference_policy': 'offline_only', 'reference_available': bool(reference.strip()),
                        'boundary_status': unit['boundary'],
                        **{k: unit[k] for k in ('upstream_book_id', 'upstream_chapter_id') if k in unit}})
                    references.append({'source_id': occurrence_id, 'project_id': project, 'split': split,
                        'source_lang': source_lang, 'target_lang': target_lang,
                        'reference_text': reference, 'reference_quality': 'upstream_unreviewed',
                        'dpo_reference_allowed': corpus.startswith('news') and split == 'train'})
                with path.open('w', encoding='utf-8', newline='') as handle:
                    writer = csv.DictWriter(handle, fieldnames=['id', 'source_text', 'text_type', 'context'])
                    writer.writeheader()
                    writer.writerows(rows)
                extraction_units.append({'project_id': project, 'csv': str(relative), 'source_lang': source_lang,
                    'target_lang': target_lang, 'split': split, 'source_records': len(rows),
                    'graph_db': 'graphs/' + project + '/graph.sqlite',
                    'graph_result': 'graphs/' + project + '/result.json'})
    write_jsonl(output / 'sources_manifest.jsonl', manifests)
    write_jsonl(output / 'skipped_empty_sources.jsonl', skipped)
    write_jsonl(output / 'extraction_units.jsonl', extraction_units)
    write_jsonl(output / 'offline/references.jsonl', references)
    summary = {'status': 'source_inputs_prepared_no_model_calls', 'seed': seed, 'paid_calls': 0,
        'counts': dict(Counter(f'{r["corpus"]}/{r["split"]}/{r["source_lang"]}-{r["target_lang"]}' for r in manifests)),
        'extraction_units': len(extraction_units), 'news_unit_splits': splits,
        'skipped_empty_sources': len(skipped),
        'missing_references': sum(not r['reference_available'] for r in manifests),
        'guofeng_evaluation': 'seen_in_training_not_heldout',
        'graph_status': 'not_extracted', 'trajectory_status': 'not_collected',
        'input_sha256': {str(p): sha256(p.read_bytes()).hexdigest() for p in nc_inputs + gf_inputs},
        'limitations': ['News Commentary original article IDs are unavailable; split by upstream files and shared long templates.',
            'GuoFeng upstream BOOK tags are preserved without claiming verified original novel identity.',
            'No semantic near-duplicate detection or reference quality approval.',
            'Current teacher runtime still needs generic corpus plans and bidirectional run initialization.']}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--news', type=Path, required=True, help='news_commentary_v18.1 directory')
    parser.add_argument('--guofeng', type=Path, required=True, help='guofeng_v1/TEST_2 directory')
    parser.add_argument('--output', type=Path, required=True, help='New directory')
    parser.add_argument('--seed', type=int, default=20261103)
    args = parser.parse_args()
    result = prepare(args.news, args.guofeng, args.output, args.seed)
    print(json.dumps({k: result[k] for k in ('status', 'counts', 'extraction_units', 'paid_calls')}, indent=2))
