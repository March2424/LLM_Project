"""Durable workflow state for one run: a finished case is never re-paid, an unfinished one resumes.

A crash between a model call and its commit leaves the call in model_calls; the agent replays
that reply by prompt hash instead of issuing it again (see TranslationAgent._ask).
"""
import json
import os
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from ..schemas.workflow import TranslationOutcome, outcome_from_state


class CommittedWithoutCheckpoint(RuntimeError):
    pass


# A transport or limit failure is not a verdict: resume must retry it, unlike a stopped case.
RETRIABLE = {'failed', 'budget_exhausted'}


class RunJournal:
    def __init__(self, folder: Path, *, run_id: str, config: dict | None = None, store=None):
        self.folder = Path(folder)
        self.path = self.folder / 'state.json'
        self.run_id = run_id
        self.folder.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            state = json.loads(self.path.read_text(encoding='utf-8'))
            if state['run_id'] != run_id:
                raise ValueError(f'{self.path} belongs to run {state["run_id"]}, not {run_id}')
            if config and state['config_hash'] != config_hash(config):
                raise ValueError('Run configuration changed; start a new journal folder')
        else:
            self._write({'run_id': run_id, 'config_hash': config_hash(config or {}), 'cases': {},
                         'budget': {}})
            state = json.loads(self.path.read_text(encoding='utf-8'))
        self.state = state
        if store is not None:
            # A committed TM with no checkpoint means the journal, not the model, is behind.
            committed = {r.evidence[0].occurrence_id for r in store.visible(store.view(run_id))
                         if r.kind == 'translation'}
            missing = sorted(committed - set(state['cases']))
            if missing:
                raise CommittedWithoutCheckpoint(
                    f'{run_id}: committed rows without a checkpoint: {missing}; inspect the database first')

    def pending(self, occurrence_ids) -> list[str]:
        return [key for key in occurrence_ids if key not in self.state['cases']
                or self.state['cases'][key]['status'] in RETRIABLE]

    def outcome(self, occurrence_id: str) -> TranslationOutcome | None:
        case = self.state['cases'].get(occurrence_id)
        return outcome_from_state(case['outcome']) if case else None

    def record(self, outcome: TranslationOutcome, budget: dict) -> None:
        self.state['cases'][outcome.occurrence_id] = {'status': outcome.status,
                                                      'outcome': json.loads(json.dumps(asdict(outcome)))}
        self.state['budget'] = budget
        self._write(self.state)

    def counts(self) -> dict:
        result = {}
        for case in self.state['cases'].values():
            result[case['status']] = result.get(case['status'], 0) + 1
        return result

    def export(self) -> list[dict]:
        return [case['outcome'] for case in self.state['cases'].values()]

    def _write(self, value: dict) -> None:
        temporary = self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        os.replace(temporary, self.path)  # A reader never sees a half-written checkpoint.


def config_hash(config: dict) -> str:
    return sha256(json.dumps(config, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
