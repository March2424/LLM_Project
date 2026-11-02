"""Supervise a teacher run and record abnormal exits without retrying paid requests."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def write(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--credential-file', type=Path)
    parser.add_argument('--game-workers', type=int, default=1)
    args = parser.parse_args()
    output = args.output.resolve()
    import fcntl
    with (output/'.launcher.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        environment = dict(os.environ, PYTHONPATH='src', TOKENIZERS_PARALLELISM='false')
        if args.credential_file:
            environment['LLM_API_KEY'] = args.credential_file.read_text().strip()
        if not environment.get('LLM_API_KEY'):
            raise RuntimeError('Provide LLM_API_KEY or --credential-file')
        with (output/'console.log').open('a', buffering=1) as log:
            worker = subprocess.Popen([
                sys.executable, '-X', 'faulthandler', '-u',
                'scripts/generate_teacher_trajectories.py', 'run', '--output', str(output),
                '--resume', '--game-workers', str(args.game_workers), '--http-workers', '4',
            ], env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            if args.credential_file:
                args.credential_file.unlink()
            (output/'worker.pid').write_text(str(worker.pid)+'\n')
            record = {'status': 'worker_started', 'pid': worker.pid,
                      'supervisor_pid': os.getpid(), 'started_at_unix': time.time()}
            write(output/'launcher.json', record)
            print('Supervisor started worker', worker.pid, file=log, flush=True)
            peak_rss = 0
            while worker.poll() is None:
                try:
                    for line in Path(f'/proc/{worker.pid}/status').read_text().splitlines():
                        if line.startswith('VmRSS:'):
                            peak_rss = max(peak_rss, int(line.split()[1]))
                except FileNotFoundError:
                    pass
                time.sleep(5)
            code = worker.wait()
            record.update(status='worker_exited', returncode=code,
                          sampled_peak_rss_kib=peak_rss, exited_at_unix=time.time())
            write(output/'launcher.json', record)
            if code != 0:
                for name in ('state.json', 'progress.json'):
                    path = output/name
                    value = json.loads(path.read_text())
                    if value['status'] == 'running':
                        value.update(status='worker_exited', worker_returncode=code)
                        write(path, value)
            print(f'Worker exited: returncode={code}, peak_rss={peak_rss} KiB; no automatic retry',
                  file=log, flush=True)


if __name__ == '__main__':
    main()
