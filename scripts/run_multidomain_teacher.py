"""Prepare aligned selector demonstrations, supervise collection and export a mixed dataset."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from localization_agent.selection.teacher import prepare, export, file_hash
from localization_agent.selection.checkpoints import load_state, write_json
from localization_agent.selection.sft_contract import inspect_sft_rows


def align_game_data(folder, original):
    rows = json.loads(original.read_text())
    template = Path('prompts/selection/context_selector.txt').read_text()
    aligned = []
    for row in rows:
        observation = json.loads(row['instruction'].split('Observation: ',1)[1])
        instruction = template.format(observation=json.dumps(observation,ensure_ascii=False))
        if row['instruction'].replace('current game resource.','current text resource.',1) != instruction:
            raise ValueError('Old student template has other differences; inspect before aligning')
        aligned.append({**row,'instruction':instruction})
    directory = folder/'alignment'
    directory.mkdir()
    write_json(directory/'game_sft_aligned.json',aligned)
    provenance = original.parent/'provenance.json'
    write_json(directory/'game_provenance.json',json.loads(provenance.read_text()))
    write_json(directory/'game_alignment.json',{'rows':len(rows),'original_sha256':file_hash(original),
        'aligned_sha256':file_hash(directory/'game_sft_aligned.json'),
        'transformation':'student first sentence game resource -> text resource; observation and output preserved',
        'original_dataset':str(original.resolve()),'quality':'selection_program_only_unreviewed'})


def assemble(folder, dataset_dir):
    export(folder,dataset_dir=folder/'new_data')
    games = json.loads((folder/'alignment/game_sft_aligned.json').read_text())
    new = json.loads((folder/'new_data/selector_sft_train.json').read_text())
    jobs = [j for j in load_state(folder)['jobs'] if j['phase']=='target' and j.get('selection_checks_passed')]
    if len(jobs) != len(new):
        raise ValueError('New data/provenance row mismatch')
    provenance = [{**row,'dataset_part':'existing_game'} for row in
                  json.loads((folder/'alignment/game_provenance.json').read_text())]
    provenance += [{'dataset_part':'new_public','source_run':str(folder.resolve()),
        **{k:j.get(k) for k in ('game','source_id','source_lang','target_lang','corpus','origin_split',
            'seen_evaluation_overlap','translation_context_policy','memory_status','view')}} for j in jobs]
    if len(provenance) != len(games)+len(new):
        raise ValueError('Combined provenance row mismatch')
    sequence_check=inspect_sft_rows(games+new,json.loads((folder/'config.json').read_text())['tokenizer'])
    dataset_dir.mkdir(parents=True,exist_ok=False)
    write_json(dataset_dir/'selector_sft_train.json',games+new)
    write_json(dataset_dir/'dataset_info.json',json.loads(Path('configs/training/dataset_info.json').read_text()))
    write_json(dataset_dir/'provenance.json',provenance)
    write_json(dataset_dir/'sequence-check.json',sequence_check)
    import yaml
    training_config=yaml.safe_load(Path('configs/training/qwen3_8b_sft_multidomain_96gb.yaml').read_text())
    training_config['cutoff_len']=sequence_check['recommended_cutoff_len']
    training_config['dataset_dir']=str(dataset_dir)
    if training_config['cutoff_len']>8192:
        training_config['per_device_train_batch_size']=2 if training_config['cutoff_len']<=16384 else 1
        training_config['gradient_accumulation_steps']=16//training_config['per_device_train_batch_size']
    (dataset_dir/'resolved_sft_config.yaml').write_text(yaml.safe_dump(training_config,sort_keys=False))
    write_json(dataset_dir/'selector_sft_train.metadata.json',{
        'rows':len(games)+len(new),'existing_game_rows':len(games),'new_public_rows':len(new),
        'sha256':file_hash(dataset_dir/'selector_sft_train.json'),'quality':'program_only_not_semantically_approved',
        'source_run':str(folder.resolve()),'translation_is_label':False,'tokenization_validated':False,
        'text_template_length_checked':True,'max_sequence_tokens':sequence_check['max_sequence_tokens'],
        'required_cutoff_len':sequence_check['recommended_cutoff_len'],'gpu_profile_required':True,
        'guofeng_evaluation':'seen_in_training','training_started':False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--model-config',type=Path,default=Path('configs/flare.memory_trial.json'))
    parser.add_argument('--old-dataset',type=Path,default=Path('outputs/selector_training/data/selector_sft_train.json'))
    parser.add_argument('--dataset-dir',type=Path,default=Path('outputs/selector_training/multidomain_2026-11-03'))
    parser.add_argument('--credential-env-file',type=Path)
    parser.add_argument('--game-workers',type=int,default=2)
    parser.add_argument('--prepare-only',action='store_true')
    args = parser.parse_args()
    folder = args.output.resolve()
    if not folder.exists():
        prepare(folder,args.plan,args.model_config,None,max_calls=1000000,max_tokens=1000000000000)
        align_game_data(folder,args.old_dataset)
    if args.prepare_only:
        print(json.dumps({'status':'prepared_no_paid_calls','output':str(folder)}))
        return
    environment = dict(os.environ,PYTHONPATH='src',TOKENIZERS_PARALLELISM='false')
    if args.credential_env_file:
        for line in args.credential_env_file.read_text().splitlines():
            key,sep,value = line.strip().removeprefix('export ').partition('=')
            if sep and key=='LLM_API_KEY':
                environment[key] = value.strip().strip('\"\'')
    if not environment.get('LLM_API_KEY'):
        raise RuntimeError('Missing LLM_API_KEY')
    result = subprocess.run([sys.executable,'-u','scripts/run_teacher_background.py','--output',str(folder),
        '--game-workers',str(args.game_workers)],env=environment)
    state = json.loads((folder/'state.json').read_text())
    if result.returncode or not state['status'].startswith('complete'):
        raise RuntimeError('Collection needs attention: '+state['status'])
    if not args.dataset_dir.exists():
        assemble(folder,args.dataset_dir)
    write_json(folder/'dataset-export.json',{'status':'mixed_dataset_exported','dataset_dir':str(args.dataset_dir.resolve()),
                                          'training_started':False})


if __name__ == '__main__':
    main()
