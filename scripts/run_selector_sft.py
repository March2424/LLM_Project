"""Run the fixed selector SFT with complete checkpoints and a persistent SwanLab run."""
import argparse
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

import torch
from transformers import TrainerCallback
import yaml


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def chart_metrics(values):
    """Keep end-of-stage summaries separate from per-update training curves."""
    return {('summary/' if key.startswith('train_') or key == 'total_flos' else 'train/') + key:value
            for key,value in values.items()}


REQUIRED = ('adapter_model.safetensors', 'adapter_config.json', 'optimizer.pt',
            'scheduler.pt', 'rng_state.pth', 'trainer_state.json', 'training_args.bin')


def complete_checkpoints(output):
    return sorted((p for p in output.glob('checkpoint-*')
                   if (p / 'resume_ready.json').exists()
                   and all((p / name).is_file() for name in REQUIRED)),
                  key=lambda p: int(p.name.split('-')[-1]))


class RunState(TrainerCallback):
    def __init__(self, output, resume, stop_after, tracker=None):
        self.output, self.resume, self.stop_after = output, resume, stop_after
        self.tracker = tracker
        self.stop_requested = False
        self.started = time.monotonic()
        self.steps = []

    def request_stop(self, *_):
        self.stop_requested = True

    def on_train_begin(self, args, state, control, **kwargs):
        torch.cuda.reset_peak_memory_stats()
        if self.resume:
            saved = torch.load(self.resume / 'scheduler.pt', map_location='cpu', weights_only=True)
            actual = kwargs['lr_scheduler'].state_dict()
            assert actual['last_epoch'] == saved['last_epoch'] == state.global_step
            expected_lr = saved['_last_lr']
            restored_lr = [g['lr'] for g in kwargs['optimizer'].param_groups]
            assert all(math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-15)
                       for a, b in zip(expected_lr, restored_lr))
            assert kwargs['optimizer'].state_dict()['state'], 'Optimizer moments were not restored'
            write_json(self.output / 'resume-validation.json', {
                'checkpoint':str(self.resume), 'global_step':state.global_step,
                'scheduler_last_epoch':actual['last_epoch'], 'restored_lr':restored_lr,
                'optimizer_state_present':True, 'status':'passed'})

    def on_step_begin(self, args, state, control, **kwargs):
        torch.cuda.synchronize()
        self.step_started = time.monotonic()

    def on_step_end(self, args, state, control, **kwargs):
        torch.cuda.synchronize()
        seconds = time.monotonic() - self.step_started
        self.steps.append(seconds)
        write_json(self.output / 'progress.json', {
            'status':'running', 'global_step':state.global_step, 'max_steps':state.max_steps,
            'epoch':state.epoch, 'learning_rate':kwargs['optimizer'].param_groups[0]['lr'],
            'step_seconds':seconds, 'optimizer_step_seconds':self.steps,
            'peak_allocated_bytes':torch.cuda.max_memory_allocated(),
            'peak_reserved_bytes':torch.cuda.max_memory_reserved(),
            'elapsed_seconds':time.monotonic()-self.started})
        if self.stop_after and state.global_step >= self.stop_after:
            self.stop_requested = True
        if self.stop_requested or state.global_step == state.max_steps:
            control.should_save = True
        if self.stop_requested:
            control.should_training_stop = True
        return control

    def on_epoch_end(self, args, state, control, **kwargs):
        control.should_save = True
        return control

    def on_save(self, args, state, control, **kwargs):
        checkpoint = self.output / f'checkpoint-{state.global_step}'
        missing = [name for name in REQUIRED if not (checkpoint / name).is_file()]
        if missing:
            raise RuntimeError('Incomplete resumable checkpoint: ' + ', '.join(missing))
        write_json(checkpoint / 'resume_ready.json', {
            'global_step':state.global_step, 'epoch':state.epoch,
            'learning_rate':kwargs['optimizer'].param_groups[0]['lr'],
            'files':{name:(checkpoint/name).stat().st_size for name in REQUIRED}})

    def on_log(self, args, state, control, logs=None, **kwargs):
        values = {k:v for k,v in (logs or {}).items() if isinstance(v,(int,float,str,bool))}
        with (self.output/'metrics.jsonl').open('a') as stream:
            stream.write(json.dumps({'global_step':state.global_step, **values})+'\n')
        if self.tracker:
            self.tracker.log({'train/global_step':state.global_step,
                              **chart_metrics(values)}, step=state.global_step)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='configs/training/qwen3_8b_sft_96gb.yaml')
    parser.add_argument('--resume', action='store_true', help='Restore the latest complete checkpoint')
    parser.add_argument('--offline', action='store_true', help='Record SwanLab locally when the API is unreachable')
    parser.add_argument('--stop-after', type=int, help='Save and stop at this optimizer step')
    parser.add_argument('--profile', help='Isolated two-step longest-sample probe; no tracking run')
    parser.add_argument('--micro-batch', type=int, choices=(4, 8, 16))
    parser.add_argument('--no-checkpointing', action='store_true')
    args = parser.parse_args()
    config = yaml.safe_load(Path(args.config).read_text())
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError('A CUDA GPU with BF16 support is required')
    if args.profile:
        config.update(dataset_dir='outputs/selector_training/profile_data',
                      output_dir='artifacts/sft-pro6000/profiles/' + args.profile,
                      max_steps=2, report_to='none', logging_steps=1, save_steps=2,
                      dataloader_num_workers=0)
        config['per_device_train_batch_size'] = args.micro_batch or 8
        config['gradient_accumulation_steps'] = 16 // config['per_device_train_batch_size']
        config['gradient_checkpointing'] = not args.no_checkpointing
        config['disable_gradient_checkpointing'] = args.no_checkpointing
    elif args.micro_batch or args.no_checkpointing:
        parser.error('Batch overrides are for isolated profiles; freeze the formal config before training')
    output = Path(config['output_dir'])
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.training.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        checkpoints = complete_checkpoints(output)
        if args.resume and not checkpoints:
            raise RuntimeError('No complete checkpoint with optimizer/scheduler/RNG state exists')
        if checkpoints and not args.resume:
            raise RuntimeError('Existing training state: use --resume to retain optimizer and learning rate')
        resume = checkpoints[-1] if args.resume else None
        data = Path(config['dataset_dir']) / 'selector_sft_train.json'
        versions = {p:importlib.metadata.version(p) for p in
                    ('torch','transformers','llamafactory','peft','accelerate')}
        fingerprint = {'config_sha256':digest(args.config), 'data_sha256':digest(data),
                       'model_config_sha256':digest(Path(config['model_name_or_path'])/'config.json'),
                       'model_index_sha256':digest(Path(config['model_name_or_path'])/'model.safetensors.index.json'),
                       'versions':versions}
        metadata_path = output / 'run.json'
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text())
            if metadata['fingerprint'] != fingerprint:
                previous = metadata['fingerprint']
                old_config = {k:v for k,v in metadata['config'].items() if k != 'report_to'}
                new_config = {k:v for k,v in config.items() if k != 'report_to'}
                same_assets = {k:v for k,v in previous.items() if k != 'config_sha256'} == {
                    k:v for k,v in fingerprint.items() if k != 'config_sha256'}
                if not (same_assets and old_config == new_config
                        and metadata['config'].get('report_to') == 'wandb'
                        and config.get('report_to') == 'swanlab'):
                    raise RuntimeError('Frozen training config/data/model/environment changed; use a new experiment')
                metadata.setdefault('tracking_migrations',[]).append({
                    'from':'wandb','to':'swanlab','checkpoint':str(resume),
                    'previous_config_sha256':previous['config_sha256'],
                    'new_config_sha256':fingerprint['config_sha256'],
                    'training_hyperparameters_unchanged':True,'at_unix':time.time()})
                metadata.update(fingerprint=fingerprint,config=config)
        else:
            metadata = {'fingerprint':fingerprint, 'config':config,
                        'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                        'gpu':torch.cuda.get_device_name(0), 'created_at_unix':time.time()}
            write_json(metadata_path, metadata)
        tracker = None
        if not args.profile:
            import swanlab
            metadata.setdefault('swanlab_run_id',uuid.uuid4().hex[:21])
            metadata.setdefault('swanlab_project','LLM_Project')
            metadata.setdefault('swanlab_mode','offline' if args.offline else 'online')
            metadata['tracking_provider'] = 'swanlab'
            metadata['tracking_client_version'] = importlib.metadata.version('swanlab')
            write_json(metadata_path, metadata)
            tracker = swanlab.init(project=metadata['swanlab_project'], id=metadata['swanlab_run_id'],
                                   name=config['run_name'], mode=metadata['swanlab_mode'], public=False,
                                   log_dir=str(output/'swanlog'), config=config,
                                   resume='must' if metadata.get('swanlab_initialized') else 'allow')
            swanlab.define_metric('summary/*',section_name='阶段汇总')
            metadata.update(swanlab_url=tracker.url,swanlab_initialized=True)
            if not metadata.get('swanlab_history_imported'):
                metrics = output/'metrics.jsonl'
                for line in metrics.read_text().splitlines() if metrics.exists() else []:
                    row = json.loads(line)
                    tracker.log(chart_metrics({k:v for k,v in row.items() if k != 'global_step'}),
                                step=row['global_step'])
                metadata['swanlab_history_imported'] = True
            write_json(metadata_path, metadata)
            # Explicit global-step logging also makes separate offline segments traceable.
            config['report_to'] = 'none'
        if resume:
            config['resume_from_checkpoint'] = str(resume)
        callback = RunState(output, resume, args.stop_after, tracker)
        signal.signal(signal.SIGTERM, callback.request_stop)
        signal.signal(signal.SIGINT, callback.request_stop)
        try:
            from llamafactory.train.tuner import run_exp
            run_exp(config, callbacks=[callback])
            progress = json.loads((output/'progress.json').read_text())
            progress['status'] = 'complete' if progress['global_step'] >= progress['max_steps'] else 'stopped'
            write_json(output/'progress.json', progress)
        except BaseException as error:
            write_json(output/'failure.json', {'type':type(error).__name__, 'message':str(error),
                       'peak_allocated_bytes':torch.cuda.max_memory_allocated(),
                       'peak_reserved_bytes':torch.cuda.max_memory_reserved()})
            if tracker:
                tracker.finish(state='crashed',error=str(error))
            raise
        if tracker:
            tracker.finish(state='success' if progress['status']=='complete' else 'aborted')


if __name__ == '__main__':
    main()
