"""Quarantine reviewed type errors in new graph copies; preserve all extraction originals."""
import argparse
import json
import sqlite3
from pathlib import Path

from localization_agent.memory import MemoryStore
from localization_agent.retrieval import GraphStore
from localization_agent.schemas.graph import GraphRef
from localization_agent.selection.teacher import file_hash, read_lines
from export_extraction_graph import export_graph


def align(sources):
    root = Path(sources).resolve()
    destination = root/'aligned_graphs_v1'
    destination.mkdir(exist_ok=False)
    projects, decisions = {}, []
    for unit in read_lines(root/'extraction_units.jsonl'):
        original = root/unit['graph_db']
        folder = destination/unit['project_id']
        folder.mkdir()
        path = folder/'graph.sqlite'
        before_hash = file_hash(original)
        reader = sqlite3.connect(original.as_uri()+'?mode=ro',uri=True)
        store = MemoryStore(path)
        try:
            reader.backup(store.db)
            if store.db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]:
                raise ValueError('Alignment requires a source-only graph')
            graph = GraphStore(store)
            snapshot = graph.snapshot('graph')
            for ref in snapshot.refs:
                node = graph.get(ref)
                if ref.kind != 'node' or node.kind != 'entity':
                    continue
                typ, name = node.attributes['entity_type'], node.attributes['name']
                if typ not in {'skill','quest'}:
                    continue
                news = unit['project_id'].startswith('news_commentary_')
                invalid = ((news and typ=='skill') or
                    (news and typ=='quest' and name not in {'Operation Sophia','索菲亚行动'}) or
                    (typ=='skill' and name=='尼姆语'))
                reason = ('Reviewed mismatch: policy/technology/course/historical event/language is not an ability or mission'
                          if invalid else 'Source-supported fictional ability or explicit named operation retained')
                decisions.append({'project_id':unit['project_id'],'ref':ref.__dict__,'name':name,
                    'entity_type':typ,'decision':'invalidate' if invalid else 'retain',
                    'reason':reason,'evidence':[e.__dict__ for e in node.evidence],
                    'reviewer':'assistant:focused-type-review-v1'})
                if invalid:
                    graph.invalidate(ref,reason)
            # Keep the old snapshot as an audit record and publish a filtered active graph.
            payload = json.loads(store.db.execute("SELECT payload FROM graph_snapshots WHERE id='graph'").fetchone()[0])
            payload['id'] = 'graph-extracted-original'
            with store.db:
                store.db.execute('INSERT INTO graph_snapshots VALUES (?,?)',('graph-extracted-original',json.dumps(payload)))
                store.db.execute("DELETE FROM graph_snapshots WHERE id='graph'")
            graph.create_snapshot('graph',unit['project_id'])
        finally:
            reader.close()
            store.close()
        assert before_hash == file_hash(original), 'Original graph changed'
        summary = export_graph(str(path),str(folder))
        projects[unit['project_id']] = {'source_db':str(path),'sha256':file_hash(path),
            'original_source_db':str(original),'original_sha256':before_hash,
            'active_entities':summary['entities'],'active_relations':summary['relations']}
    review = {'review_scope':'all 39 skill/quest nodes; no full semantic approval',
              'decisions':decisions,'invalidated_entities':sum(d['decision']=='invalidate' for d in decisions)}
    (destination/'corrections.json').write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
    manifest = {'version':'source-graph-alignment/1','projects':projects,
        'corrections_path':str(destination/'corrections.json'),
        'corrections_sha256':file_hash(destination/'corrections.json'),
        'originals_preserved':True,'verification':'program_only_with_focused_assistant_corrections'}
    (root/'graph_alignment.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return {'units':len(projects),'invalidated_entities':review['invalidated_entities'],
            'active_entities':sum(p['active_entities'] for p in projects.values()),
            'active_relations':sum(p['active_relations'] for p in projects.values())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(align(args.sources),ensure_ascii=False,indent=2))
