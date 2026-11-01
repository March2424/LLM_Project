"""Export a completed/checkpointed graph without reading translation references."""
import argparse
import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path


def export_graph(database: str, output: str):
    folder = Path(output)
    folder.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = connection.execute('SELECT g.kind,g.payload FROM graph_items g JOIN '
            '(SELECT kind,id,MAX(version) version FROM graph_items GROUP BY kind,id) latest '
            'USING(kind,id,version) WHERE NOT EXISTS (SELECT 1 FROM graph_invalidations i '
            'WHERE i.kind=g.kind AND i.id=g.id AND i.version=g.version)').fetchall()
        nodes = {r['id']: r for kind,payload in rows if kind=='node' for r in [json.loads(payload)]}
        entities = [n for n in nodes.values() if n['kind']=='entity']
        edges = [json.loads(payload) for kind,payload in rows if kind=='edge']
        for name,items in [('entities.jsonl',entities),('relations.jsonl',edges)]:
            (folder/name).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in items))
        def label(ref):
            node=nodes[ref['id']]
            return node['attributes'].get('name') or node['evidence'][0]['quote']
        with (folder/'relations.csv').open('w',newline='') as handle:
            writer=csv.writer(handle)
            writer.writerow(['id','source_id','source','relation','target_id','target','basis','reason','evidence_ids'])
            for edge in edges:
                writer.writerow([edge['id'],edge['source']['id'],label(edge['source']),edge['relation'],
                    edge['target']['id'],label(edge['target']),edge['attributes'].get('basis','direct'),
                    edge['attributes'].get('reason',''),';'.join(dict.fromkeys(e['occurrence_id'] for e in edge['evidence']))])
        summary={'entities':len(entities),'relations':len(edges),
            'source_records':sum(n['kind']=='occurrence' for n in nodes.values()),
            'by_entity_type':dict(Counter(n['attributes']['entity_type'] for n in entities)),
            'by_relation':dict(Counter(e['relation'] for e in edges)),
            'basis':dict(Counter(e['attributes'].get('basis','direct') for e in edges)),
            'verification':'program_only'}
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='extraction_batches'").fetchone():
            batches=[json.loads(row[0]) for row in connection.execute('SELECT payload FROM extraction_batches')]
            summary['processed_source_records']=sum(len(batch['refs']) for batch in batches if batch['status']=='complete')
            summary['pending_batches']=sum(batch['status']=='pending' for batch in batches)
            rejected=[dict(batch_id=row[0],**item) for row in
                connection.execute('SELECT id,payload FROM extraction_batches')
                for item in json.loads(row[1]).get('result',{}).get('rejected',[])]
            summary['rejected_items']=len(rejected)
            (folder/'rejected_items.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rejected))
        (folder/'graph_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        return summary
    finally:
        connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(json.dumps(export_graph(args.db,args.output),ensure_ascii=False))
