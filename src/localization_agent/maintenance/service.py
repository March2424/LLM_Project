"""M6: what a knowledge or source change actually costs, and what it deliberately does not.

Selective revision re-checks only the translations a change can reach, so the difference
against full re-review is a measured saving rather than an unverified claim.
"""
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from uuid import uuid4

from ..memory.store import _record
from ..schemas.maintenance import AffectedTranslation, UpdateEvent, UpdateReport
from ..schemas.memory import Evidence, MemoryRef, MemoryRecord, TranslationCheck, scope_match, text_hash

# The three ablation arms of README section 6.
ARMS = {'dependency': ('dependency',), 'retrieval': ('retrieval',),
        'dependency_retrieval': ('dependency', 'retrieval'),
        'full': ('dependency', 'scope', 'retrieval')}


class MaintenanceConfig:
    def __init__(self, discovery: str = 'dependency_retrieval', max_candidates: int = 200,
                 reviewer: str = 'agent:maintenance-reviewer', max_cascades: int = 2):
        if discovery not in ARMS:
            raise ValueError(f'discovery must be one of {sorted(ARMS)}')
        if min(max_candidates, max_cascades) < 1:
            raise ValueError('A candidate or cascade limit below one cannot run')
        self.discovery, self.max_candidates, self.reviewer = discovery, max_candidates, reviewer
        self.max_cascades = max_cascades


class MaintenanceService:
    """Reverse dependency, scope and retrieval discovery plus optimistic selective revision."""

    def __init__(self, retriever, agent, *, config: MaintenanceConfig | None = None):
        self.retriever = retriever
        self.memory = retriever.memory
        self.store = retriever.memory.store
        self.agent = agent
        self.config = config or MaintenanceConfig()
        self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS maintenance_locks (
                project TEXT, occurrence TEXT, reviewer TEXT, note TEXT,
                PRIMARY KEY(project, occurrence));
            CREATE TABLE IF NOT EXISTS maintenance_audit (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, id TEXT NOT NULL, payload TEXT NOT NULL);
        """)

    # Human protection --------------------------------------------------------------------

    def lock(self, occurrence_id: str, reviewer: str, *, project: str, note: str = '') -> None:
        if not reviewer.strip():
            raise ValueError('A lock names the person it protects the row for')
        self.store.source(project, self._evidence(occurrence_id))
        with self.store.db:
            self.store.db.execute('INSERT OR REPLACE INTO maintenance_locks VALUES (?,?,?,?)',
                                  (project, occurrence_id, reviewer, note))

    def unlock(self, occurrence_id: str, *, project: str) -> None:
        with self.store.db:
            self.store.db.execute('DELETE FROM maintenance_locks WHERE project=? AND occurrence=?',
                                  (project, occurrence_id))

    def locked(self, occurrence_id: str, project: str) -> dict | None:
        row = self.store.db.execute('SELECT * FROM maintenance_locks WHERE project=? AND occurrence=?',
                                    (project, occurrence_id)).fetchone()
        return dict(row) if row else None

    # Discovery ---------------------------------------------------------------------------

    def _evidence(self, occurrence_id: str) -> Evidence:
        row = self.store.db.execute('SELECT MAX(version) FROM sources WHERE id=?', (occurrence_id,)).fetchone()
        if row[0] is None:
            raise ValueError(f'{occurrence_id} is not a registered source')
        return Evidence(occurrence_id, row[0], '')

    def changed(self, event: UpdateEvent) -> MemoryRecord | None:
        """The record as it is now, so every arm inspects the same change.

        An event names one version, but the decision must follow the current glossary: replaying
        a committed event has to cost nothing, not re-apply a wording that was later superseded.
        """
        if event.kind == 'source':
            return None
        record = self.store.get(event.ref)
        if record.scope.project_id != event.project_id:
            raise ValueError('Update event and record belong to different projects')
        latest = self.store.latest_version(record.record_id)
        return record if latest == record.version else self.store.get(MemoryRef(record.record_id, latest))

    def _dependency_hit(self, translation: MemoryRecord, event: UpdateEvent) -> bool:
        """The row was translated with this very knowledge item supplied (any version of it)."""
        if event.ref is None:
            return False
        same_item = lambda ref: ref.get('record_id') == event.ref.record_id if isinstance(ref, dict) \
            else ref.record_id == event.ref.record_id
        return any(same_item(d) for d in translation.dependencies) or any(
            same_item(r) for r in translation.content.get('context_manifest', {}).get('memory_refs', []))

    def _scope_hit(self, translation: MemoryRecord, record: MemoryRecord | None,
                   occurrence, event: UpdateEvent) -> bool:
        """Could this rule/knowledge change apply to this row at all, supplied or not?"""
        if record is None:
            if event.kind == 'source':
                return occurrence.occurrence_id == event.source_change.occurrence_id
            if event.kind == 'rule':
                return all(occurrence.metadata.get(k) == v
                           for k, v in (event.current or {}).get('metadata', {}).items())
            return True
        if scope_match(record.scope, occurrence) != 'match' \
                or not self.store.game_compatible(record, occurrence):
            return False
        if record.kind == 'term':
            return record.content['source'] in occurrence.source
        if record.kind == 'style':
            return occurrence.metadata.get('speaker_id') == record.scope.conditions.get('speaker_id')
        topic = record.content.get('metadata', {}).get('topic_id')
        return bool(topic) and (occurrence.metadata.get('topic_id') == topic
                                or occurrence.occurrence_id in {e.occurrence_id for e in record.evidence})

    @staticmethod
    def _items(translation: MemoryRecord) -> tuple[str, ...]:
        return tuple(item['id'] for item in translation.content.get('context_manifest', {}).get('items', ()))

    def _rebuild(self, translation: MemoryRecord, view, snapshot_id: str, occurrence):
        """Re-supply exactly what the row was translated under, then compare the context."""
        return self.retriever.build_context(self.retriever.retrieve(occurrence, view, snapshot_id),
                                            self._items(translation) or None)

    @staticmethod
    def _carries(pack, record_id: str) -> bool:
        return any(ref.get('record_id') == record_id for ref in pack.manifest['memory_refs'])

    def _retrieval_hit(self, translation: MemoryRecord, event: UpdateEvent, view,
                       snapshot_id: str, occurrence) -> tuple[bool, str, str]:
        """Drift under the old selection, plus what a fresh retrieval would now supply.

        The comparison is scoped to the changed item: run history grows after every commit, and
        that alone is not a reason to re-review an unrelated row.
        """
        try:
            pool = self.retriever.retrieve(occurrence, view, snapshot_id)
            same = self.retriever.build_context(pool, self._items(translation) or None)
            fresh = self.retriever.build_context(pool)
        except (KeyError, ValueError) as error:
            # The original selection no longer fits: that is a context change, not a crash.
            return True, '', f'Context can no longer be rebuilt as supplied: {type(error).__name__}'
        if event.ref is not None:
            reached = self._carries(same, event.ref.record_id) or self._carries(fresh, event.ref.record_id)
            return reached, fresh.context_hash, ('This row is supplied with the updated item'
                                                if reached else '')
        stored = translation.content.get('context_manifest', {}).get('model_context', '')
        changed = fresh.model_context != stored
        return changed, fresh.context_hash, ('Rendered context differs from the translated one'
                                             if changed else '')

    def candidates(self, event: UpdateEvent, run_id: str, snapshot_id: str):
        """Yield (translation, occurrence, arms) for every row one of the selected arms can reach."""
        view, record, arms = self.store.view(run_id), self.changed(event), ARMS[self.config.discovery]
        for translation in (r for r in self.store.visible(view)
                            if r.kind == 'translation' and r.scope.project_id == event.project_id):
            occurrence = self.store.source(event.project_id, translation.evidence[0])
            hits = [arm for arm in arms if self._arm(arm, translation, event, record, occurrence, view, snapshot_id)]
            if hits:
                yield translation, occurrence, tuple(hits)

    def _arm(self, arm, translation, event, record, occurrence, view, snapshot_id) -> bool:
        if arm == 'dependency':
            return self._dependency_hit(translation, event)
        if arm == 'scope':
            # The full re-review ablation arm reaches every committed row; selective arms do not.
            return self.config.discovery == 'full' or self._scope_hit(translation, record, occurrence, event)
        return self._retrieval_hit(translation, event, view, snapshot_id, occurrence)[0]

    def plan(self, event: UpdateEvent, run_id: str, snapshot_id: str) -> tuple[list[AffectedTranslation], dict]:
        """Classify without writing; a plan needs no model call and costs nothing to inspect."""
        record = self.changed(event)
        found = list(self.candidates(event, run_id, snapshot_id))[:self.config.max_candidates]
        items, view = [], self.store.view(run_id)
        for translation, occurrence, hits in found:
            lock = self.locked(occurrence.occurrence_id, event.project_id)
            if lock:
                items.append(AffectedTranslation(translation.ref, occurrence.occurrence_id,
                                                 occurrence.source_version, hits, 'locked',
                                                 f"Locked by {lock['reviewer']}"
                                                 + (f": {lock['note']}" if lock['note'] else ''),
                                                 old_target=translation.content['target']))
                continue
            if record is not None and record.kind == 'term':
                if record.content['source'] not in occurrence.source:
                    items.append(self._no_change(translation, occurrence, hits,
                                                 'Updated term does not occur in this source'))
                elif record.content['target'] in translation.content['target']:
                    items.append(self._no_change(translation, occurrence, hits,
                                                 'Existing target already satisfies the term'))
                else:
                    items.append(AffectedTranslation(
                        translation.ref, occurrence.occurrence_id, occurrence.source_version, hits, 'unresolved',
                        f"Term {record.content['source']} now requires {record.content['target']}",
                        old_target=translation.content['target']))
                continue
            changed, context_hash, reason = self._retrieval_hit(translation, event, view, snapshot_id, occurrence)
            if changed:
                items.append(AffectedTranslation(translation.ref, occurrence.occurrence_id,
                                                 occurrence.source_version, hits, 'unresolved', reason,
                                                 old_target=translation.content['target'],
                                                 context_hash=context_hash))
            else:
                items.append(self._no_change(translation, occurrence, hits,
                                             'Rebuilt context is identical to the translated one',
                                             context_hash=context_hash))
        return items, {'reached': len(found), 'reviewed': len(found), 'run_id': run_id}

    @staticmethod
    def _no_change(translation, occurrence, hits, reason, *, context_hash: str = '') -> AffectedTranslation:
        return AffectedTranslation(translation.ref, occurrence.occurrence_id, occurrence.source_version,
                                   hits, 'no_change', reason, old_target=translation.content['target'],
                                   new_target=translation.content['target'], context_hash=context_hash)

    # Revision ---------------------------------------------------------------------------

    def supersede(self, ref: MemoryRef, content: dict, reviewer: str) -> MemoryRecord:
        """Publish a new version of one knowledge item.

        The superseded version is not invalidated here: that cascade would mark the very
        translations M6 is about to revise as gone before they can be found.
        """
        old = self.store.get(ref)
        if reviewer != self.config.reviewer and not reviewer.strip():
            raise ValueError('A knowledge revision names its reviewer')
        draft = self.store.draft(old.kind, old.scope, {**old.content, **content}, old.evidence,
                                 dependencies=old.dependencies, run_id=old.run_id, position=old.position,
                                 record_id=old.record_id,
                                 expected_version=self.store.latest_version(old.record_id))
        return self.store.approve(draft.ref, reviewer)

    def _translations(self, run_id: str) -> dict:
        """Latest stored version per translation row, retired ones included."""
        latest = {}
        for row in self.store.db.execute('SELECT payload FROM records WHERE run=? ORDER BY version', (run_id,)):
            record = _record(row['payload'])
            if record.kind == 'translation':
                latest[record.evidence[0].occurrence_id] = record
        return latest

    def _alive(self, run_id: str) -> set:
        """Rows the store still serves; a cascade takes a row out of this set."""
        return {r.evidence[0].occurrence_id for r in self.store.visible(self.store.view(run_id))
                if r.kind == 'translation'}

    def _revise(self, item: AffectedTranslation, event: UpdateEvent, run_id: str, snapshot_id: str,
                *, dry_run: bool, reuse_calls: bool, conflicts: list,
                alive: set) -> tuple[AffectedTranslation, tuple]:
        if item.decision != 'unresolved' or dry_run:
            return item, ()
        occurrence = self.store.source(event.project_id, self._evidence(item.occurrence_id))
        was_alive = occurrence.occurrence_id in alive
        outcome = self.agent.run(occurrence, run_id, snapshot_id, reuse_calls=reuse_calls,
                                 expected_version=item.translation.version)
        if outcome.status == 'conflict':
            # The row moved underneath this plan; the newer version stays and the human is told.
            conflicts.append(f'{item.occurrence_id}: {outcome.failure}')
            return replace(item, reason=f'{item.reason}; {outcome.failure}'), outcome.model_calls
        if outcome.status != 'committed':
            return replace(item, reason=f'{outcome.status}: {outcome.failure or "no verdict"}'), outcome.model_calls
        if outcome.target == item.old_target:
            # A new version may exist while the wording does not move: that is the saving M6 claims.
            reason = ('Re-review kept the existing target' if was_alive
                      else 'Re-anchored after a dependency was replaced; target kept')
            return (replace(item, translation=outcome.translation or item.translation, decision='no_change',
                            new_target=item.old_target, context_hash=outcome.context_hash,
                            reason=reason), outcome.model_calls)
        return (replace(item, translation=outcome.translation or item.translation,
                        decision='needs_revision', new_target=outcome.target,
                        context_hash=outcome.context_hash,
                        reason=f'{item.reason}; revised by {self.config.reviewer}'), outcome.model_calls)

    def _net(self, item: AffectedTranslation, base: str,
             earlier: AffectedTranslation | None) -> AffectedTranslation:
        """A row can be written twice while the cascade settles; the report carries the net change."""
        if item.decision not in ('needs_revision', 'no_change'):
            return replace(item, old_target=base)
        target = item.new_target or item.old_target
        decision = 'needs_revision' if target != base else 'no_change'
        reason = item.reason
        if decision != item.decision:
            reason = ('Re-review kept the existing target' if decision == 'no_change'
                      else f'{(earlier or item).reason}; target moved while the row was re-anchored')
        elif earlier is not None and earlier.reason != item.reason:
            # Both hops are facts the reviewer needs: what the plan concluded and what the cascade did.
            reason = f'{earlier.reason}; {item.reason}'
        joined = tuple(dict.fromkeys(((earlier.discovery if earlier else ()) + item.discovery)))
        return replace(item, decision=decision, new_target=target, old_target=base,
                       reason=reason, discovery=joined)

    def apply(self, event: UpdateEvent, run_id: str, snapshot_id: str, *,
              dry_run: bool = False, reuse_calls: bool = False) -> UpdateReport:
        """Revise what the change reached, then follow the invalidations the revisions themselves cause.

        Replacing a translation retires every row that was translated with it as history, so a
        selective run is a bounded work list rather than one flat pass.
        """
        items, plan = self.plan(event, run_id, snapshot_id)
        results, conflicts, calls, cascaded = {}, [], [], set()
        baseline = {item.occurrence_id: item.old_target for item in items}
        queue = list(items)
        for round_ in range(self.config.max_cascades + 1):
            batch, queue = queue, []
            alive_before = self._alive(run_id)
            for item in batch:
                revised, calls_of = self._revise(item, event, run_id, snapshot_id, dry_run=dry_run,
                                                 reuse_calls=reuse_calls, conflicts=conflicts,
                                                 alive=alive_before)
                results[revised.occurrence_id] = self._net(revised, baseline[revised.occurrence_id],
                                                            results.get(revised.occurrence_id))
                calls.extend(calls_of)
            if dry_run:
                break
            # A dependency cascade can retire a row this plan never touched; it must be re-anchored.
            for occurrence_id in sorted(alive_before - self._alive(run_id)):
                if occurrence_id not in results:
                    cascaded.add(occurrence_id)
                queued = self._cascade_item(occurrence_id, event, run_id)
                baseline.setdefault(occurrence_id, queued.old_target)
                queue.append(queued)
            if not queue:
                break
        for item in queue:
            conflicts.append(f'{item.occurrence_id}: still retired after {self.config.max_cascades} cascade rounds')
            results[item.occurrence_id] = replace(item, reason=f'{item.reason}; cascade limit reached')
        ordered = tuple(results[key] for key in sorted(results))
        report = UpdateReport(event, plan['reached'] + len(cascaded), ordered,
                              tuple(conflicts), tuple(calls))
        self._audit({'action': 'apply', 'event': asdict(event), 'report': asdict(report), 'run_id': run_id,
                     'snapshot_id': snapshot_id, 'discovery': self.config.discovery, 'dry_run': dry_run,
                     'model': getattr(self.agent.chat, 'model', None)})
        return report

    def _cascade_item(self, occurrence_id: str, event: UpdateEvent, run_id: str) -> AffectedTranslation:
        record = self._translations(run_id)[occurrence_id]
        occurrence = self.store.source(event.project_id, self._evidence(occurrence_id))
        return AffectedTranslation(record.ref, occurrence.occurrence_id, occurrence.source_version,
                                   ('dependency',), 'unresolved',
                                   'Depends on a translation that this run replaced',
                                   old_target=record.content['target'])

    # Traceability -----------------------------------------------------------------------

    def rollback(self, translation: MemoryRef, *, run_id: str, snapshot_id: str, reason: str) -> MemoryRecord:
        """Re-commit the earlier wording as a new version; history is appended, never rewritten."""
        current = self.store.get(translation)
        if current.kind != 'translation' or translation.version < 2:
            raise ValueError('Only a replaced translation can be rolled back')
        if not reason.strip():
            raise ValueError('A rollback states why the newer target was rejected')
        # A cascade can leave several versions of one wording; the rollback restores the last different one.
        previous = None
        for version in range(translation.version - 1, 0, -1):
            candidate = self.store.get(MemoryRef(translation.record_id, version))
            if candidate.content['target'] != current.content['target']:
                previous = candidate
                break
        if previous is None:
            raise ValueError('No earlier version of this row carries different wording')
        occurrence = self.store.source(current.scope.project_id, current.evidence[0])
        pack = self._rebuild(current, self.store.view(run_id), snapshot_id, occurrence)
        check = TranslationCheck(occurrence.occurrence_id, occurrence.source_version,
                                 text_hash(previous.content['target']), pack.context_hash,
                                 f'maintenance:rollback ({reason})', True)
        saved = self.retriever.commit(occurrence, previous.content['target'], check, pack,
                                      expected_version=translation.version)
        self._audit({'action': 'rollback', 'translation': asdict(translation), 'reason': reason,
                     'restored_version': previous.version, 'new_version': saved.version,
                     'kept_sources': [asdict(e) for e in previous.evidence]})
        return saved

    def audit(self) -> list[dict]:
        return [json.loads(row['payload']) for row in
                self.store.db.execute('SELECT payload FROM maintenance_audit ORDER BY seq')]

    def _audit(self, payload: dict) -> None:
        with self.store.db:
            # A receipt sequence, not a timestamp: several entries can land in the same second.
            self.store.db.execute('INSERT INTO maintenance_audit (ts, id, payload) VALUES (?,?,?)',
                                  (datetime.now(timezone.utc).isoformat(timespec='seconds'), uuid4().hex,
                                   json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)))

    @staticmethod
    def export(report: UpdateReport) -> dict:
        """The conflict report a reviewer or another member is handed."""
        return {'event': asdict(report.event), 'candidates': report.candidates, 'counts': report.counts,
                'conflicts': list(report.conflicts), 'model_calls': list(report.model_calls),
                'revised': len(report.revised), 'untouched': len(report.untouched),
                'items': [{**asdict(i), 'diff': i.diff} for i in report.items]}
