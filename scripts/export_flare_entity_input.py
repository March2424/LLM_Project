#!/usr/bin/env python3
"""Export independent, source-only graph extraction rows from the published Flare snapshot."""
import argparse
import csv
import json
from pathlib import Path
import tempfile


def rebuild_native_context(raw_root, source_path, context_path):
    # Reuse the adapter's INCLUDE/APPEND resolution and occurrence identities.
    from localization_agent.data.flare_full import build_full
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp)
        build_full(raw_root, output)
        if read_jsonl(source_path) != read_jsonl(output / 'source.jsonl'):
            raise ValueError('Raw snapshot does not reproduce the published source records')
        rows = read_jsonl(output / 'background/provenance.jsonl')
        context_path.write_text(''.join(json.dumps({'record_id': row['record_id'],
            'native_context': row['native_context']}, ensure_ascii=False) + '\n'
            for row in rows), encoding='utf-8')


def read_jsonl(path):
    with path.open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def export(source_path, provenance_path, output_path):
    sources = read_jsonl(source_path)
    provenance = {row['record_id']: row for row in read_jsonl(provenance_path)}
    with output_path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(['id', 'source_text', 'text_type', 'context'])
        for row in sources:
            origin = provenance[row['record_id']]
            clues = [f"{clue['field']}={clue['value']}" for clue in origin['native_context']]
            writer.writerow([row['record_id'], row['source_text'], row['text_type'] or 'unknown', '; '.join(clues)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('data/flare/v0.2/source.jsonl'))
    parser.add_argument('--provenance', type=Path, default=Path('data/flare/entity_input_background.jsonl'))
    parser.add_argument('--output', type=Path, default=Path('data/flare/flare_entity_input_v0.2.csv'))
    parser.add_argument('--raw-root', type=Path, help='Rebuild native clues from the pinned raw snapshots first')
    args = parser.parse_args()
    if args.raw_root:
        rebuild_native_context(args.raw_root, args.source, args.provenance)
    export(args.source, args.provenance, args.output)


if __name__ == '__main__':
    main()
