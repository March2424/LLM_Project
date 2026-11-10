"""Serve a run's translations, their review receipts and their version chain on one page.

The demo mutates a session copy of the database, never the run it was given: a presentation can
roll back, lock a row or plan a glossary change and then reset to the original state.
"""
import argparse
import json
import shutil
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from localization_agent.maintenance import MaintenanceConfig, MaintenanceService
from localization_agent.memory import MemoryConfig, MemoryService, MemoryStore
from localization_agent.retrieval import GraphConfig, GraphRetriever, GraphStore
from localization_agent.schemas.maintenance import UpdateEvent
from localization_agent.schemas.memory import MemoryRef

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'demo' / 'index.html'
sys.path.insert(0, str(ROOT / 'scripts'))
from run_agent_workflow import load, services  # noqa: E402  (scripts/ is not a package)


class StubAgent:
    """Locking and rolling back need no model; the page only reports what M6 would ask for."""

    chat = None

    def run(self, occurrence, run_id, snapshot_id, **values):
        raise RuntimeError('This demo has no model attached; plan the change instead of applying it')


class Session:
    def __init__(self, source: Path, session: Path, config: dict | None, run_id: str | None):
        self.source, self.session, self.config = Path(source), Path(session), config
        shutil.copyfile(self.source, self.session)
        self.run_id, self.project = run_id, (config or {}).get('project')
        self.open()

    def open(self):
        self.store = MemoryStore(self.session)
        self.graph = GraphStore(self.store)
        if self.run_id is None:
            row = self.store.db.execute('SELECT id, project FROM runs LIMIT 1').fetchone()
            self.run_id, self.project = (row['id'], row['project']) if row else ('run', None)
        memory = MemoryService(self.store, MemoryConfig(summary_step=99), chat=None)
        graph_config = (self.config or {}).get('graph_config') or {'max_characters': 5000, 'max_soft_tokens': None}
        self.retriever = GraphRetriever(self.graph, memory, GraphConfig(**graph_config))
        if self.config:
            from localization_agent.models.chat_completions import CallBudget, ChatCompletionsModel
            chat = ChatCompletionsModel(**self.config['model'],
                                        budget=CallBudget(**self.config.get('budget', {})))
            agent = services(self.config, self.store, self.graph, chat)[0]
        else:
            agent = StubAgent()
        self.maintenance = MaintenanceService(self.retriever, agent,
                                              config=MaintenanceConfig(reviewer='human:demo'))
        self.snapshot = (self.config or {}).get('graph_snapshot') or 'graph'

    def reset(self):
        self.store.close()
        shutil.copyfile(self.source, self.session)
        self.open()

    # Reads ------------------------------------------------------------------------------

    def rows(self) -> list[dict]:
        view = self.store.view(self.run_id)
        result = []
        for record in self.store.visible(view):
            if record.kind != 'translation':
                continue
            occurrence = record.evidence[0]
            manifest = record.content.get('context_manifest', {})
            result.append({
                'id': occurrence.occurrence_id, 'source': record.content['source'],
                'target': record.content['target'], 'version': record.version,
                'text_type': record.content.get('metadata', {}).get('text_type'),
                'reviewer': record.content.get('check', {}).get('reviewer'),
                'passed': bool(record.content.get('check', {}).get('passed')),
                'locked': self.maintenance.locked(occurrence.occurrence_id, record.scope.project_id),
                'context': [{'id': item.get('id'), 'category': item.get('category'),
                             'text': item.get('text', '')[:300]} for item in manifest.get('items', ())],
                'dependencies': [ref.get('record_id') for ref in manifest.get('memory_refs', ())],
                'versions': self.versions(record),
            })
        return sorted(result, key=lambda row: (row['text_type'] or '', row['id']))

    def versions(self, record) -> list[dict]:
        rows = self.store.db.execute('SELECT version, payload FROM records WHERE id=? ORDER BY version',
                                     (record.record_id,))
        history = []
        for row in rows:
            payload = json.loads(row['payload'])
            history.append({'version': row['version'], 'target': payload['content'].get('target', ''),
                            'reviewer': payload['content'].get('check', {}).get('reviewer'),
                            'status': payload['status'],
                            'valid': row['version'] == record.version and payload['status'] == 'approved'})
        return history

    def terms(self) -> list[dict]:
        latest = {}
        for row in self.store.db.execute("SELECT id, version, status, payload FROM records WHERE payload LIKE "
                                        "'%\"kind\": \"term\"%' ORDER BY version"):
            payload = json.loads(row['payload'])
            latest[row['id']] = {'record_id': row['id'], 'version': row['version'],
                                 'source': payload['content'].get('source', ''),
                                 'target': payload['content'].get('target', ''),
                                 'approved': row['status'] == 'approved'}
        # A retired term cannot take a new version: its evidence died with the change that
        # expired it, and re-anchoring that is a rebuild, not a wording change.
        usable = [term for term in latest.values()
                  if term['approved'] and self.store.is_valid(MemoryRef(term['record_id'], term['version']))]
        return sorted(usable, key=lambda term: term['source'].lower())

    def state(self) -> dict:
        return {'run_id': self.run_id, 'project': self.project, 'rows': self.rows(),
                'terms': self.terms(), 'snapshot': self.snapshot,
                'model': (self.config or {}).get('model', {}).get('model'),
                'has_model': bool(self.config)}

    # Writes -----------------------------------------------------------------------------

    def rollback(self, occurrence_id: str) -> dict:
        record = next(r for r in self.store.visible(self.store.view(self.run_id))
                      if r.kind == 'translation' and r.evidence[0].occurrence_id == occurrence_id)
        if record.version < 2:
            raise ValueError('This row has only one version')
        restored = self.maintenance.rollback(record.ref, run_id=self.run_id, snapshot_id=self.snapshot,
                                             reason='Demo: reviewer rejected the newer target')
        return {'occurrence': occurrence_id, 'version': restored.version,
                'target': restored.content['target']}

    def plan(self, record_id: str, target: str, apply: bool) -> dict:
        latest = self.store.get(MemoryRef(record_id, self.store.latest_version(record_id)))
        # 预演 already supersedes the term, so a following 执行 on the same wording reuses
        # that version instead of stacking a second identical one.
        current = latest if latest.content['target'] == target else \
            self.maintenance.supersede(latest.ref, {'target': target}, 'human:demo')
        event = UpdateEvent(self.project, current.kind, f'Glossary: {current.content["source"]} -> {target}',
                            ref=current.ref, previous={'target': latest.content['target']},
                            current={'target': target})
        report = self.maintenance.apply(event, self.run_id, self.snapshot, dry_run=not apply)
        return MaintenanceService.export(report)


def handler_for(session: Session):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status: int, body: bytes, kind: str = 'application/json'):
            self.send_response(status)
            self.send_header('Content-Type', f'{kind}; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ('/', '/index.html'):
                return self.send(200, PAGE.read_bytes(), 'text/html')
            if path == '/api/run':
                return self.send(200, json.dumps(session.state(), ensure_ascii=False).encode('utf-8'))
            self.send(404, b'{"error": "not found"}')

        def do_POST(self):
            path = urlparse(self.path).path
            values = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            try:
                if path == '/api/rollback':
                    result = session.rollback(values['occurrence'])
                elif path == '/api/lock':
                    session.maintenance.lock(values['occurrence'], values.get('reviewer', 'human:demo'),
                                             project=session.project, note=values.get('note', ''))
                    result = {'locked': values['occurrence']}
                elif path == '/api/unlock':
                    session.maintenance.unlock(values['occurrence'], project=session.project)
                    result = {'unlocked': values['occurrence']}
                elif path == '/api/event':
                    result = session.plan(values['record_id'], values['target'], bool(values.get('apply')))
                elif path == '/api/reset':
                    session.reset()
                    result = {'reset': True}
                else:
                    return self.send(404, b'{"error": "not found"}')
            except Exception as error:  # The page shows the reason instead of a blank failure.
                return self.send(400, json.dumps({'error': f'{type(error).__name__}: {error}'},
                                                 ensure_ascii=False).encode('utf-8'))
            self.send(200, json.dumps(result, ensure_ascii=False).encode('utf-8'))

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, help='Run database to display; a session copy is used')
    parser.add_argument('--config', help='Same config as the run; enables real apply and shows the model name')
    parser.add_argument('--run', help='Run id; defaults to the only run in the database')
    parser.add_argument('--port', type=int, default=7000)
    parser.add_argument('--session', default='outputs/demo/session.sqlite', help='Session copy path')
    args = parser.parse_args()
    config = load(args.config) if args.config else None
    if not Path(args.db).exists():
        raise SystemExit(f'{args.db} does not exist')
    Path(args.session).parent.mkdir(parents=True, exist_ok=True)
    session = Session(Path(args.db), Path(args.session), config, args.run)
    # One request thread: the session store is opened here and sqlite3 connections are thread-bound.
    server = HTTPServer(('127.0.0.1', args.port), handler_for(session))
    print(json.dumps({'url': f'http://127.0.0.1:{args.port}/', 'run_id': session.run_id,
                      'project': session.project, 'rows': len(session.rows()),
                      'session_db': str(args.session)}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        session.store.close()


if __name__ == '__main__':
    main()
