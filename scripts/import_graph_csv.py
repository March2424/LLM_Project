"""Import explicitly mapped source CSV columns; emit a frozen graph snapshot."""
import argparse
import json
from pathlib import Path

from localization_agent.data import CSVConfig, import_csv
from localization_agent.memory import MemoryStore
from localization_agent.retrieval import GraphStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv')
    parser.add_argument('--config', required=True, help='JSON fields matching CSVConfig')
    parser.add_argument('--annotations', help='JSON mapping original CSV keys to reviewed metadata')
    parser.add_argument('--project', required=True)
    parser.add_argument('--db', required=True)
    parser.add_argument('--snapshot', required=True, help='New unique graph snapshot ID')
    args = parser.parse_args()
    config = CSVConfig(**json.loads(Path(args.config).read_text(encoding='utf-8')))
    annotations = json.loads(Path(args.annotations).read_text(encoding='utf-8')) if args.annotations else None
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(args.db)
    try:
        graph = GraphStore(store)
        imported = import_csv(args.csv, args.project, graph, config, annotations=annotations)
        snapshot = graph.create_snapshot(args.snapshot, args.project)
        print(json.dumps({'occurrences': len(imported.occurrences), 'blocks': len(imported.blocks),
                          'documents': sorted({o.document_id for o in imported.occurrences}),
                          'snapshot': snapshot.id, 'warnings': imported.warnings}, ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == '__main__':
    main()
