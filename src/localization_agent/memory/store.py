"""SQLite source of truth: immutable payload versions, pinned knowledge, online runs."""

import json
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

from localization_agent.schemas.memory import (
    Evidence, MemoryRecord, MemoryRef, Occurrence, ReadView, Scope, TranslationCheck, scope_match, text_hash,
)


class VersionConflict(ValueError):
    pass


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _record(payload: str) -> MemoryRecord:
    data = json.loads(payload)
    scope = data["scope"]
    scope["conditions"] = {k: tuple(v) for k, v in scope["conditions"].items()}
    data["scope"] = Scope(**scope)
    data["evidence"] = tuple(Evidence(**e) for e in data["evidence"])
    data["dependencies"] = tuple(MemoryRef(**d) for d in data["dependencies"])
    return MemoryRecord(**data)


class MemoryStore:
    def __init__(self, path: str | Path = ":memory:"):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                project TEXT, id TEXT, version INTEGER, payload TEXT NOT NULL,
                PRIMARY KEY(project, id, version));
            CREATE TABLE IF NOT EXISTS records (
                id TEXT, version INTEGER, project TEXT, run TEXT, seq INTEGER,
                status TEXT, payload TEXT NOT NULL, PRIMARY KEY(id, version));
            CREATE INDEX IF NOT EXISTS records_run ON records(run, seq);
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY, project TEXT, refs TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, project TEXT, document TEXT,
                source_lang TEXT, target_lang TEXT, snapshot TEXT);
            CREATE TABLE IF NOT EXISTS invalidations (
                id TEXT, version INTEGER, reason TEXT, PRIMARY KEY(id, version));
            CREATE TABLE IF NOT EXISTS model_calls (
                id TEXT PRIMARY KEY, task TEXT, prompt TEXT, prompt_hash TEXT,
                response TEXT, error TEXT);
        """)

    def close(self) -> None:
        self.db.close()

    def put_source(self, occurrence: Occurrence) -> None:
        if not occurrence.source.strip() or occurrence.source_version < 1 or occurrence.position < 0:
            raise ValueError("Source, positive source_version and nonnegative position required")
        payload = _json(asdict(occurrence))
        key = (occurrence.project_id, occurrence.occurrence_id, occurrence.source_version)
        with self.db:
            old = self.db.execute("SELECT payload FROM sources WHERE project=? AND id=? AND version=?", key).fetchone()
            if old and old["payload"] != payload:
                raise VersionConflict("Source version already exists with different content")
            self.db.execute("INSERT OR IGNORE INTO sources VALUES (?, ?, ?, ?)", (*key, payload))
            if not old:
                for row in self.db.execute("SELECT payload FROM records WHERE project=?", (occurrence.project_id,)).fetchall():
                    record = _record(row["payload"])
                    if any(e.occurrence_id == occurrence.occurrence_id and e.source_version < occurrence.source_version
                           for e in record.evidence):
                        self._invalidate(record.ref, "Source version changed")

    def source(self, project: str, evidence: Evidence) -> Occurrence:
        row = self.db.execute("SELECT payload FROM sources WHERE project=? AND id=? AND version=?",
                              (project, evidence.occurrence_id, evidence.source_version)).fetchone()
        if row is None:
            raise KeyError(f"Unknown source: {evidence.occurrence_id}@{evidence.source_version}")
        return Occurrence(**json.loads(row["payload"]))

    def get(self, ref: MemoryRef) -> MemoryRecord:
        row = self.db.execute("SELECT payload FROM records WHERE id=? AND version=?",
                              (ref.record_id, ref.version)).fetchone()
        if row is None:
            raise KeyError(ref)
        return _record(row["payload"])

    def latest_version(self, record_id: str) -> int:
        return self.db.execute("SELECT COALESCE(MAX(version), 0) FROM records WHERE id=?",
                               (record_id,)).fetchone()[0]

    def run(self, run_id: str) -> dict:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown run: {run_id}")
        return dict(row)

    def view(self, run_id: str) -> ReadView:
        self.run(run_id)
        seq = self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM records WHERE run=?", (run_id,)).fetchone()[0]
        return ReadView(run_id, seq)

    def _insert(self, record: MemoryRecord, expected: int) -> MemoryRecord:
        # Start the writer transaction before checking the optimistic version.
        if not self.db.in_transaction:
            self.db.execute("BEGIN IMMEDIATE")
        actual = self.latest_version(record.record_id)
        if actual != expected:
            raise VersionConflict(f"{record.record_id}: expected {expected}, found {actual}")
        if actual:
            old = self.get(MemoryRef(record.record_id, actual))
            if (old.kind, old.scope, old.run_id) != (record.kind, record.scope, record.run_id):
                raise ValueError("A record cannot change kind, scope or run; create a new ID")
        if not record.evidence:
            raise ValueError("Memory requires locatable source evidence")
        for evidence in record.evidence:
            source = self.source(record.scope.project_id, evidence)
            current = self.db.execute("SELECT MAX(version) FROM sources WHERE project=? AND id=?",
                                      (record.scope.project_id, evidence.occurrence_id)).fetchone()[0]
            if current != evidence.source_version:
                raise VersionConflict("Evidence source version has advanced")
            if not evidence.quote or evidence.quote not in source.source:
                raise ValueError("Evidence quote does not occur in the registered source")
            if record.scope.document_id and source.document_id != record.scope.document_id:
                raise ValueError("Evidence belongs to another document")
        if record.run_id:
            run = self.run(record.run_id)
            if (record.scope.project_id, record.scope.document_id, record.scope.source_lang, record.scope.target_lang) != (run["project"], run["document"], run["source_lang"], run["target_lang"]):
                raise ValueError("Online memory scope must match its run")
            if record.position is None:
                raise ValueError("Online memory requires a source position")
        for ref in record.dependencies:
            dependency = self.get(ref)
            if not self.is_valid(ref) or dependency.status != "approved":
                raise ValueError("Dependency is not approved or is stale")
            if dependency.scope.project_id != record.scope.project_id:
                raise ValueError("Cross-project dependency")
            if dependency.run_id is not None and dependency.run_id != record.run_id:
                raise ValueError("Cross-run dependency")
            if record.kind == "summary" and (dependency.kind not in {"summary", "translation"}
                                             or dependency.scope != record.scope):
                raise ValueError("Summary dependencies must be compatible translations/summaries")
        seq = self.view(record.run_id).watermark + 1 if record.run_id else 0
        record = replace(record, version=actual + 1, commit_seq=seq)
        self.db.execute("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (record.record_id, record.version, record.scope.project_id, record.run_id,
                         seq, record.status, _json(asdict(record))))
        return record

    def draft(self, kind: str, scope: Scope, content: dict, evidence: tuple[Evidence, ...],
              *, dependencies: tuple[MemoryRef, ...] = (), run_id: str | None = None,
              position: int | None = None, record_id: str | None = None,
              expected_version: int = 0) -> MemoryRecord:
        required = {"term": ("source", "target"), "summary": ("source", "target"), "style": ("note",)}
        if kind not in required or any(not isinstance(content.get(k), str) or not content[k].strip()
                                       for k in required[kind]):
            raise ValueError("Draft requires term/source+target, summary/source+target, or style/note")
        if kind == "summary" and (run_id is None or not dependencies):
            raise ValueError("Bilingual summaries require run-local verified translation dependencies")
        if kind == "summary" and not any(self.get(ref).kind == "translation" for ref in dependencies):
            raise ValueError("Summary update needs new verified translation evidence")
        if kind == "style" and not scope.conditions.get("speaker_id"):
            raise ValueError("Style notes require a speaker scope")
        record = MemoryRecord(record_id or uuid4().hex, 0, kind, scope, content, evidence,
                              dependencies, run_id=run_id, position=position)
        with self.db:
            return self._insert(record, expected_version)

    def approve(self, ref: MemoryRef, reviewer: str) -> MemoryRecord:
        record = self.get(ref)
        if record.status != "draft" or not reviewer.strip() or not self.is_valid(ref):
            raise ValueError("Approve a valid draft with an explicit reviewer")
        with self.db:
            return self._insert(replace(record, status="approved", reviewer=reviewer), ref.version)

    def commit_translation(self, occurrence: Occurrence, run_id: str, target: str,
                           check: TranslationCheck, *, used: tuple[MemoryRef, ...] = (),
                           expected_version: int = 0, view: ReadView | None = None,
                           context_sources: tuple[Evidence, ...] = (),
                           context_manifest: dict | None = None) -> MemoryRecord:
        if (not check.passed or not check.reviewer.strip() or not check.context_hash
                or check.occurrence_id != occurrence.occurrence_id
                or check.source_version != occurrence.source_version
                or check.target_hash != text_hash(target) or not target.strip()):
            raise ValueError("Translation check does not bind this candidate, source and context")
        evidence = Evidence(occurrence.occurrence_id, occurrence.source_version, occurrence.source)
        if self.source(occurrence.project_id, evidence) != occurrence:
            raise VersionConflict("Occurrence differs from registered source")
        current_source_version = self.db.execute("SELECT MAX(version) FROM sources WHERE project=? AND id=?",
                                                 (occurrence.project_id, occurrence.occurrence_id)).fetchone()[0]
        if current_source_version != occurrence.source_version:
            raise VersionConflict("Source version has advanced; regenerate and recheck")
        run = self.run(run_id)
        view = view or self.view(run_id)
        if view.run_id != run_id:
            raise ValueError("Read view belongs to another run")
        available = {r.ref: r for r in self.visible(view)}
        for ref in used:
            if ref not in available or (available[ref].position is not None and available[ref].position >= occurrence.position):
                raise ValueError("Supplied dependency is outside the run snapshot or prior history")
            dependency = available[ref]
            if scope_match(dependency.scope, occurrence) != "match":
                raise ValueError("Supplied dependency is outside the occurrence scope")
            if dependency.kind in {"translation", "summary"} and dependency.content.get("metadata", {}) != occurrence.metadata:
                raise ValueError("Target-side context is not compatible with this occurrence")
        record_id = "tm:" + text_hash(_json([run_id, occurrence.occurrence_id]))
        record = MemoryRecord(record_id, 0, "translation", Scope.for_occurrence(occurrence),
                              {"source": occurrence.source, "target": target,
                               "metadata": occurrence.metadata, "check": asdict(check),
                               "knowledge_snapshot": run["snapshot"], "input_view": asdict(view),
                               "context_manifest": context_manifest or {}},
                              tuple(dict.fromkeys((evidence,) + context_sources)), used,
                              "approved", run_id, occurrence.position, reviewer=check.reviewer)
        with self.db:
            saved = self._insert(record, expected_version)
            if expected_version:
                self._invalidate(MemoryRef(record_id, expected_version), "Translation replaced")
            # A late insertion into already summarized history requires rebuilding that summary.
            for summary in self._approved(view=self.view(run_id)):
                if summary.kind == "summary" and summary.scope == saved.scope and summary.position >= saved.position:
                    self._invalidate(summary.ref, "Translation inserted or revised inside summary coverage")
            return saved

    def _approved(self, *, project: str | None = None, view: ReadView | None = None) -> list[MemoryRecord]:
        if view:
            rows = self.db.execute("SELECT payload FROM records WHERE run=? AND seq<=? AND status='approved' ORDER BY version",
                                   (view.run_id, view.watermark))
        else:
            rows = self.db.execute("SELECT payload FROM records WHERE project=? AND run IS NULL AND status='approved' ORDER BY version",
                                   (project,))
        latest = {}
        for row in rows:
            record = _record(row["payload"])
            latest[record.record_id] = record
        return [r for r in latest.values() if self.is_valid(r.ref)]

    def create_snapshot(self, snapshot_id: str, project: str) -> None:
        with self.db:
            refs = [asdict(r.ref) for r in self._approved(project=project)]
            self.db.execute("INSERT INTO snapshots VALUES (?, ?, ?)", (snapshot_id, project, _json(refs)))

    def start_run(self, run_id: str, project: str, document: str, source_lang: str,
                  target_lang: str, snapshot_id: str) -> None:
        snapshot = self.db.execute("SELECT project FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if snapshot is None or snapshot["project"] != project:
            raise ValueError("Run needs an existing knowledge snapshot from its project")
        with self.db:
            self.db.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?)",
                            (run_id, project, document, source_lang, target_lang, snapshot_id))

    def visible(self, view: ReadView) -> list[MemoryRecord]:
        run = self.run(view.run_id)
        if not 0 <= view.watermark <= self.view(view.run_id).watermark:
            raise ValueError("Read watermark is outside this run")
        snapshot = self.db.execute("SELECT refs FROM snapshots WHERE id=?", (run["snapshot"],)).fetchone()
        knowledge = [self.get(MemoryRef(**r)) for r in json.loads(snapshot["refs"])]
        return [r for r in knowledge if self.is_valid(r.ref)] + self._approved(view=view)

    def is_valid(self, ref: MemoryRef) -> bool:
        return self.db.execute("""SELECT 1 FROM records r WHERE r.id=? AND r.version=?
                               AND NOT EXISTS (SELECT 1 FROM invalidations i WHERE i.id=r.id AND i.version=r.version)""",
                               (ref.record_id, ref.version)).fetchone() is not None

    def _invalidate(self, ref: MemoryRef, reason: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO invalidations VALUES (?, ?, ?)",
                        (ref.record_id, ref.version, reason))
        # Small-project baseline. M6 can replace this scan with an indexed reverse graph.
        pending = [ref]
        records = [_record(row[0]) for row in self.db.execute("SELECT payload FROM records")]
        while pending:
            parent = pending.pop()
            for record in records:
                if parent in record.dependencies and self.is_valid(record.ref):
                    self.db.execute("INSERT INTO invalidations VALUES (?, ?, ?)",
                                    (record.record_id, record.version, f"Dependency {parent}: {reason}"))
                    pending.append(record.ref)

    def invalidate(self, ref: MemoryRef, reason: str) -> None:
        self.get(ref)
        with self.db:
            self._invalidate(ref, reason)

    def log_call(self, task: str, prompt: str, response: dict | None, error: str | None = None) -> str:
        call_id = uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO model_calls VALUES (?, ?, ?, ?, ?, ?)",
                            (call_id, task, prompt, text_hash(prompt), _json(response), error))
        return call_id
