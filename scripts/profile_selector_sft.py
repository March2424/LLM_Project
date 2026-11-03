"""Compare three small longest-sample SFT probes, keeping effective batch 16."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

import torch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--select-only', action='store_true', help='Reassess saved results without GPU steps')
    args = parser.parse_args()
    root = Path('artifacts/sft-pro6000')
    useful = json.loads((root/'pressure-data.json').read_text())['useful_tokens_per_update']
    total = torch.cuda.get_device_properties(0).total_memory
    results = json.loads((root/'profile-results.json').read_text()) if args.select_only else []
    cases = [] if args.select_only else [('batch8_gc',8,False), ('batch16_gc',16,False), ('batch4_no_gc',4,True)]
    for name, batch, no_gc in cases:
        command = [sys.executable,'-u','scripts/run_selector_sft.py','--profile',name,'--micro-batch',str(batch)]
        if no_gc:
            command.append('--no-checkpointing')
        with (root/(name+'.log')).open('w') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        out = root/'profiles'/name
        row = {'name':name,'batch':batch,'gradient_accumulation':16//batch,
               'gradient_checkpointing':not no_gc,'returncode':result.returncode}
        if result.returncode == 0:
            progress = json.loads((out/'progress.json').read_text())
            seconds = progress['optimizer_step_seconds'][-1]
            row.update(progress, useful_tokens_per_second=useful/seconds,
                       memory_fraction=progress['peak_reserved_bytes']/total)
        else:
            row['failure'] = json.loads((out/'failure.json').read_text()) if (out/'failure.json').exists() else 'See log'
        results.append(row)
        (root/'profile-results.json').write_text(json.dumps(results,indent=2)+'\n')
        print(json.dumps(row),flush=True)
    # Reserved memory includes reusable allocator cache; capacity decisions use live tensors.
    for row in results:
        if row['returncode'] == 0:
            row['allocated_fraction'] = row['peak_allocated_bytes'] / total
    (root/'profile-results.json').write_text(json.dumps(results,indent=2)+'\n')
    usable = [r for r in results if r['returncode']==0 and r['allocated_fraction'] < 0.90]
    if not usable:
        raise RuntimeError('No probe completed with sufficient memory headroom')
    chosen = max(usable,key=lambda r:r['useful_tokens_per_second'])
    (root/'selected-profile.json').write_text(json.dumps(chosen,indent=2)+'\n')
    print('Selected:',chosen['name'],flush=True)


if __name__ == '__main__':
    main()
