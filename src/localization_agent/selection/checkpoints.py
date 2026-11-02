"""Atomic per-job checkpoints and a lightweight index, with legacy flat-state reads."""
import json
from pathlib import Path

DETAIL_FIELDS = ('teacher_prompt', 'student_prompt', 'translation_prompt', 'baseline_prompt',
                 'teacher_context', 'translation_context', 'observation', 'selected_contexts')


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def compact_job(job):
    """Keep completed-job progress in RAM; full evidence remains in its checkpoint."""
    job['_checkpoint_compact'] = True
    for key in DETAIL_FIELDS[:6]:
        job.pop(key, None)
    if 'observation' in job:
        job['observation'] = {'candidates': [
            {'memory_kind': c['memory_kind']} for c in job['observation']['candidates']]}
    if 'selected_contexts' in job:
        job['selected_contexts'] = [{'memory_kind': c['memory_kind']} for c in job['selected_contexts']]


def load_state(folder, *, compact_completed=False):
    folder = Path(folder)
    state = json.loads((folder / 'state.json').read_text())
    if state.pop('storage_format', None) == 'per_job_v1':
        jobs = []
        for row in state['jobs']:
            path = folder / row['checkpoint']
            job = (json.loads(path.read_text()) if path.exists()
                   else {k:v for k,v in row.items() if k != 'checkpoint'})
            if compact_completed and job['status'] in {'complete', 'failed'}:
                compact_job(job)
            jobs.append(job)
        state['jobs'] = jobs
    return state


class JobCheckpoints:
    fields = ('game','run_id','source_id','split','group_id','phase','status','trajectory_schema_version')

    def __init__(self, folder, state):
        self.folder, self.state = Path(folder), state
        (self.folder / 'jobs').mkdir(exist_ok=True)
        self.indices = {id(job):i for i,job in enumerate(state['jobs'])}
        legacy = json.loads((self.folder / 'state.json').read_text()).get('storage_format') != 'per_job_v1'
        if legacy:
            for job in state['jobs']:
                if job['status'] != 'pending' or 'view' in job:
                    self.save(job)
            self.save_index()

    def save(self, job):
        path = self.folder / 'jobs' / f'{self.indices[id(job)]:06d}.json'
        if job.get('_checkpoint_compact'):
            saved = json.loads(path.read_text())
            saved.update({k:v for k,v in job.items() if k not in DETAIL_FIELDS and k != '_checkpoint_compact'})
            write_json(path, saved)
        else:
            write_json(path, job)

    def save_index(self):
        rows = [{**{k:job[k] for k in self.fields if k in job},
                 'checkpoint':f'jobs/{i:06d}.json'} for i,job in enumerate(self.state['jobs'])]
        write_json(self.folder / 'state.json', {**self.state,'storage_format':'per_job_v1','jobs':rows})
