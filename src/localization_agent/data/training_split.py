"""Offline resource-group splits and sampling plans; never generates translations."""
import csv
import json
import random
import re
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from .native_memory import native_binding, memory_blocks


def digest(value):
    return sha256(value.encode()).hexdigest()


def read_jsonl(path):
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def resource_key(game, item):
    """Use native source identities, not inferred entity identity or target text."""
    if game == 'flare':
        return (item['resource'], item.get('native_id') or '')
    if game == 'shattered':
        return (item['localization_key'].rsplit('.', 1)[0],)
    return (item.get('source', {}).get('path') or item['record_id'],)


def template_key(text):
    value = ' '.join(text.casefold().split())
    # Conservative literal/template checks, not semantic paraphrase detection.
    if len(value) >= 24:
        value = re.sub(r'\$?\{[^{}]+\}|%[-+\d.$]*[sdif]|\b\d+(?:\.\d+)?\b', '<slot>', value)
    return value


def assign_groups(game, rows, provenance, seed):
    parent = {row['id']: row['id'] for row in rows}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    owners = {}
    for row in rows:
        key = row['id']
        groups = [('resource', resource_key(game, provenance[key]))]
        template = template_key(row['source_text'])
        # Generic "Yes"/"OK" must not connect every scene into one giant component.
        # Their held-out targets are excluded later if the same text occurs in train.
        if len(template) >= 24:
            groups.append(('text', template))
        for group in groups:
            if group in owners:
                parent[find(key)] = find(owners[group])
            else:
                owners[group] = key
    members = defaultdict(list)
    for row in rows:
        members[find(row['id'])].append(row['id'])
    result = {}
    for ids in members.values():
        group_id = game + ':' + digest('\n'.join(sorted(ids)))[:16]
        score = int(digest(str(seed) + ':' + group_id)[:12], 16) / 16**12
        # Keep the original test groups fixed; the former dev groups now join train.
        split = 'external_test' if game == 'naev' else 'train' if score < .85 else 'test'
        for key in ids:
            result[key] = (group_id, split)
    return result


def build_game_sequences(manifests, warmup=10):
    """Resource groups control splits; each game/split has one persistent memory stream."""
    grouped = defaultdict(list)
    for item in manifests:
        if item['source_usable']:
            grouped[(item['game'], item['split'])].append(item)
    sequences = []
    for (game, split), items in sorted(grouped.items()):
        # This is resource processing order, never asserted story chronology.
        items.sort(key=lambda r: r['position'])
        count = min(warmup, max(0, len(items) - 1))
        sequences.append({'game': game, 'split': split, 'sequence_id': game + ':' + split,
            'memory_scope': 'game_split',
            'warmup_ids': [r['source_id'] for r in items[:count]],
            'target_ids': [r['source_id'] for r in items[count:]],
            'history_status': 'planned_not_translated', 'order_semantics': 'resource_processing_not_story'})
    return sequences


def prepare(root, output, seed=20261101, pilot_size=128, warmup=10, *, memory_coverage=False, retain_plan=None):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    catalog_path = root / 'data/game_samples.json'
    catalog = json.loads(catalog_path.read_text())
    manifests, sources, references, inputs, bindings = [], [], [], {}, {}
    for dataset in catalog['datasets']:
        game = dataset['game']
        source_path = root / dataset['model_input_csv']
        provenance_path = root / ('data/flare/v0.2/background/provenance.jsonl' if game == 'flare'
                                  else dataset['status_provenance'])
        reference_path = root / dataset['offline_references']
        for path in (source_path, provenance_path, reference_path):
            inputs[str(path.relative_to(root))] = sha256(path.read_bytes()).hexdigest()
        with source_path.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        provenance = {r['record_id']: r for r in read_jsonl(provenance_path)}
        refs = {r['record_id']: r for r in read_jsonl(reference_path)}
        groups = assign_groups(game, rows, provenance, seed)
        for position, row in enumerate(rows):
            key = row['id']
            group, split = groups[key]
            ref = refs.get(key, {})
            eligible = bool(ref.get('reference_text', '').strip()) and not ref.get('fuzzy', False)
            usable = row['text_type'] != 'formatting' and bool(row['source_text'].strip())
            manifests.append({'game': game, 'source_id': key, 'group_id': group, 'split': split,
                'text_type': row['text_type'], 'has_context': bool(row['context']), 'source_usable': usable,
                'reference_available': eligible, 'reference_quality': 'upstream_unreviewed',
                'position': position, 'template_hash': digest(template_key(row['source_text']))})
            sources.append({'game': game, 'split': split, **row})
            if memory_coverage and split=='train' and usable:
                binding=native_binding(game,row,provenance[key])
                if binding:
                    bindings.setdefault(game,{})[key]=binding
            if ref:
                references.append({'game': game, 'split': split, **ref})
    # Cross-game identical/template overlap is excluded from held-out evaluation targets.
    train_keys = {r['template_hash'] for r in manifests if r['split'] == 'train'}
    for item in manifests:
        item['train_overlap'] = item['split'] != 'train' and item['template_hash'] in train_keys
        item['score_candidate'] = item['source_usable'] and item['reference_available'] and not item['train_overlap']
    sequences = build_game_sequences(manifests, warmup)
    # Round-robin target text types; one pilot does not try to translate the entire source corpus.
    targets = {(s['game'], key) for s in sequences if s['split'] == 'train' for key in s['target_ids']}
    buckets = defaultdict(list)
    for item in manifests:
        if ((item['game'], item['source_id']) in targets
                and (item['source_usable'] if memory_coverage else item['score_candidate'])):
            buckets[(item['game'], item['text_type'], item['has_context'])].append(item)
    rng = random.Random(seed)
    for values in buckets.values():
        rng.shuffle(values)
    pilot = []
    while any(buckets.values()) and len(pilot) < pilot_size:
        for category in sorted(buckets):
            if buckets[category] and len(pilot) < pilot_size:
                item = buckets[category].pop()
                pilot.append({k: item[k] for k in ('game', 'source_id', 'group_id', 'split', 'text_type')})
    coverage=[]
    if memory_coverage:
        retained=[]
        if retain_plan:
            retained=list(read_jsonl(Path(retain_plan)/'teacher_pilot.jsonl'))
        known={(r['game'],r['source_id']):r for r in manifests}
        retained_keys={(r['game'],r['source_id']) for r in retained}
        warmup_keys={(s['game'],key) for s in sequences for key in s['warmup_ids']}
        priority,coverage=memory_blocks(manifests,bindings,retained_keys|warmup_keys)
        combined,seen=[],set()
        for row in retained+priority+pilot:
            pair=(row['game'],row['source_id'])
            if pair not in seen:
                seen.add(pair)
                combined.append({k:known[pair][k] for k in ('game','source_id','group_id','split','text_type')})
        if len(retained)>pilot_size or any(known[key]['split']!='train' for key in retained_keys):
            raise ValueError('Retained pilot must fit the target size and train split')
        pilot=combined[:pilot_size]
    pilot_keys = {(r['game'], r['source_id']) for r in pilot}
    coverage=[b for b in coverage if all((b['game'],key) in pilot_keys for key in b['source_ids'])]
    pilot_sequences = []
    for sequence in sequences:
        selected = ([r['source_id'] for r in pilot if r['game']==sequence['game'] and r['split']==sequence['split']]
                    if memory_coverage else [key for key in sequence['target_ids'] if (sequence['game'], key) in pilot_keys])
        if selected:
            pilot_sequences.append({**sequence, 'target_ids': selected,
                'target_sampling': 'compatible_memory_blocks_then_balanced_remainder' if memory_coverage
                                   else 'declared_subset_in_game_processing_order'})
    summary = {'status': 'draft_offline_preparation', 'seed': seed, 'paid_calls': 0,
        'history_generated': False, 'teacher_trajectories_generated': False,
        'source_visibility': 'not_frozen_filter_graph_and_native_context_before_sampling',
        'split_policy': 'resource_and_long_literal_template_components_85_15_no_dev_naev_holdout',
        'checkpoint_policy': 'fixed_sft_epoch_2_no_dev_selection',
        'memory_policy': 'per_game_split_stage_v1', 'warmup_per_game': warmup,
        'limitations': ['No semantic near-duplicate detector; review large connected groups.',
            'Reference availability is not quality approval.',
            'Memory/graph snapshots and evidence-need coverage require the later sampler.',
            'Short generic duplicates are excluded from held-out scoring if present in train.',
            'Pilot uses declared target subsets and one warmup per game, not full-game translation.',
            'Resource groups isolate train/test; switching groups does not reset run memory.'],
        'counts': dict(Counter(r['game'] + '/' + r['split'] for r in manifests)),
        'reference_candidates': dict(Counter(r['game'] + '/' + r['split'] for r in manifests if r['score_candidate'])),
        'overlap_excluded': sum(r['train_overlap'] for r in manifests),
        'largest_groups': Counter(r['group_id'] for r in manifests).most_common(10),
        'pilot_targets': len(pilot), 'pilot_warmup_records': sum(len(s['warmup_ids']) for s in pilot_sequences),
        'pilot_sequence_count': len(pilot_sequences), 'sequence_count': len(sequences), 'input_sha256': inputs}
    def write_jsonl(path, rows):
        path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    write_jsonl(output / 'splits.jsonl', manifests)
    write_jsonl(output / 'sources.jsonl', sources)
    (output / 'offline').mkdir()
    write_jsonl(output / 'offline/references.jsonl', references)
    write_jsonl(output / 'sequences.jsonl', sequences)
    write_jsonl(output / 'teacher_pilot.jsonl', pilot)
    write_jsonl(output / 'pilot_sequences.jsonl', pilot_sequences)
    if memory_coverage:
        (output/'native_bindings.json').write_text(json.dumps(bindings,ensure_ascii=False,indent=2)+'\n')
        write_jsonl(output/'memory_coverage_blocks.jsonl',coverage)
        summary['memory_coverage']={
            'sft_requires_reference':False,
            'retained_targets':len(retained),'role_blocks':sum(b['kind']=='role' for b in coverage),
            'topic_blocks':sum(b['kind']=='topic' for b in coverage),
            'role_rows':sum(len(b['source_ids']) for b in coverage if b['kind']=='role'),
            'topic_rows':sum(len(b['source_ids']) for b in coverage if b['kind']=='topic'),
            'planned_later_observations':{kind:sum(b['later_observation_opportunities'] for b in coverage
                                                 if b['kind']==kind) for kind in ('role','topic')},
            'actual_candidate_availability':'not_generated_no_paid_calls',
            'selector_category_quotas':False}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    return summary
