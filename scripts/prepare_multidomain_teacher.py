"""Build the existing Teacher plan from completed source-only graph shards."""
import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from localization_agent.selection.teacher import file_hash, read_lines


def write_lines(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))


def prepare_plan(sources, output, *, seed=20261103, warmup=10, targets=None):
    sources, output = Path(sources).resolve(), Path(output)
    targets = targets or {'news_commentary_v18.1/en-zh': 600,
                          'news_commentary_v18.1/zh-en': 600, 'guofeng_v1_test2/zh-en': 700}
    manifests = read_lines(sources/'sources_manifest.jsonl')
    units = {r['project_id']: r for r in read_lines(sources/'extraction_units.jsonl')}
    grouped = defaultdict(list)
    for row in manifests:
        if row['sft_allowed']:
            grouped[row['project_id']].append(row)
    pools, sequences, splits, projects = defaultdict(list), {}, [], {}
    for project, rows in sorted(grouped.items()):
        unit = units[project]
        result = json.loads((sources/unit['graph_result']).read_text())
        csv_path = str(sources/unit['csv'])
        if result.get('status') != 'complete' or result['inputs'].get(csv_path) != file_hash(csv_path):
            raise ValueError(f'Graph not complete or source changed: {project}')
        first = rows[0]
        projects[project] = {'source_db': str(sources/unit['graph_db']),
            **{k: first[k] for k in ('source_lang','target_lang','corpus','split')},
            'source_graph_sha256': file_hash(sources/unit['graph_db'])}
        rng = random.Random(str(seed) + ':' + project)
        rng.shuffle(rows)
        count = min(warmup, max(0, len(rows)-1))
        direction = first['source_lang'] + '-' + first['target_lang']
        category = first['corpus'] + '/' + direction
        sequences[project] = {'game': project, 'split': 'train', 'memory_scope': 'game_split',
            'sequence_id': project, 'warmup_ids': [r['source_id'] for r in rows[:count]], 'target_ids': [],
            **{k: first[k] for k in ('corpus','source_lang','target_lang')},
            'origin_split': first['split'], 'seen_evaluation_overlap': first['split']=='adaptation_seen',
            'order_semantics': 'unordered_resource_processing_not_story'}
        pools[category].append((project, [r['source_id'] for r in rows[count:]]))
        splits.extend({'game': project, 'source_id': r['source_id'], 'group_id': r['unit_id'],
                       'split': 'train', 'source_usable': True, 'origin_split': r['split'],
                       'seen_evaluation_overlap': r['split']=='adaptation_seen'} for r in rows)
    for category, groups in pools.items():
        selected = 0
        while selected < targets[category] and any(ids for _, ids in groups):
            for project, ids in groups:
                if ids and selected < targets[category]:
                    sequences[project]['target_ids'].append(ids.pop())
                    selected += 1
    output.mkdir(parents=True, exist_ok=False)
    write_lines(output/'splits.jsonl', splits)
    write_lines(output/'pilot_sequences.jsonl', [s for s in sequences.values() if s['target_ids']])
    (output/'source_projects.json').write_text(json.dumps(projects, indent=2) + '\n')
    graph = json.loads(Path('configs/graph.json').read_text())
    graph['source_visibility'] = 'all'
    (output/'graph_config.json').write_text(json.dumps(graph, indent=2) + '\n')
    summary = {'status': 'prepared_no_paid_calls', 'source_manifest_sha256': file_hash(sources/'sources_manifest.jsonl'),
               'warmup': sum(len(s['warmup_ids']) for s in sequences.values() if s['target_ids']),
               'targets': sum(len(s['target_ids']) for s in sequences.values()), 'requested_targets': targets}
    (output/'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_plan(args.sources, args.output), indent=2))
