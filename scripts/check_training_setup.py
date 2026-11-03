"""CPU-only validation of selector configs and data registration; never starts training."""
import argparse
import json
import shutil
from pathlib import Path

import yaml


def verify_tokenization(config, data):
    """Use the real LLaMA-Factory template without loading model weights."""
    from transformers import AutoTokenizer
    from llamafactory.hparams import get_train_args
    from llamafactory.data import get_template_and_fix_tokenizer
    from llamafactory.data.processor.supervised import SupervisedDatasetProcessor
    from llamafactory.data.processor.pairwise import PairwiseDatasetProcessor
    _, data_args, _, _, _ = get_train_args({**config, 'bf16':False, 'use_cpu':True})
    tokenizer = AutoTokenizer.from_pretrained(config['model_name_or_path'], local_files_only=True)
    template = get_template_and_fix_tokenizer(tokenizer, data_args)
    processor = (SupervisedDatasetProcessor if config['stage']=='sft' else PairwiseDatasetProcessor)(
        template, tokenizer, None, data_args)
    maximum = 0
    for row in data:
        prompt = [{'role':'user','content':row['instruction'] + ('\n' + row['input'] if row.get('input') else '')}]
        fields = ('output',) if config['stage']=='sft' else ('chosen','rejected')
        response = [{'role':'assistant','content':row[field]} for field in fields]
        for completion in response:
            prefix, suffix = template.encode_oneturn(tokenizer, prompt + [completion])
            length = len(prefix) + len(suffix)
            if length > config['cutoff_len']:
                raise ValueError(f'Full observation/completion is {length} tokens; refusing silent cutoff at {config["cutoff_len"]}')
            maximum = max(maximum, length)
        encoded = processor._encode_data_example(prompt, response, None, None, [], [], [])
        if config['stage']=='sft':
            source_ids,target_ids=template.encode_oneturn(tokenizer,prompt+response)
            input_ids,label_ids=encoded[:2]
            boundary=len(source_ids)
            if input_ids[:boundary]!=source_ids or any(token!=-100 for token in label_ids[:boundary]):
                raise ValueError('Prompt boundary or prompt loss mask differs from the template')
            if label_ids[boundary:boundary+len(target_ids)]!=target_ids:
                raise ValueError('Full rationale/action/EOS tokens are not supervised intact')
        labels = (encoded[1],) if config['stage']=='sft' else (encoded[1],encoded[3])
        for field, item in zip(fields, labels):
            supervised = tokenizer.decode([token for token in item if token != -100], skip_special_tokens=True)
            if row[field] not in supervised:
                raise ValueError('Rationale or action was removed from supervised completion')
    return maximum


def check(config_path, registry_path, verify_tokens=False, require_common_prompt=False):
    config = yaml.safe_load(Path(config_path).read_text())
    registry = json.loads(Path(registry_path).read_text())
    if config.get('do_eval') or config.get('eval_dataset') or config.get('val_size', 0):
        raise ValueError('This run has no development set or development evaluation')
    if config.get('eval_strategy') != 'no' or config.get('load_best_model_at_end'):
        raise ValueError('Checkpoint selection must not use development evaluation')
    if config.get('train_on_prompt') or config.get('mask_history'):
        raise ValueError('Train the full rationale/action completion, not the prompt')
    stage = config['stage']
    if stage == 'sft' and config['num_train_epochs'] != 2:
        raise ValueError('SFT is fixed at two epochs; deploy the final checkpoint')
    directory = Path(config['dataset_dir'])
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'dataset_info.json'
    if target.exists() and json.loads(target.read_text()) != registry:
        raise ValueError('Existing dataset registry differs; inspect before replacing it')
    if not target.exists():
        shutil.copyfile(registry_path, target)
    name = config['dataset']
    entry = registry[name]
    data_path = directory / entry['file_name']
    pending = []
    count = 0
    max_tokens = None
    if not data_path.exists():
        pending.append('real_training_data_not_generated')
    else:
        data = json.loads(data_path.read_text())
        if not isinstance(data, list) or not data:
            raise ValueError('Training data must be a nonempty list of real trajectories')
        for row in data:
            if not row.get('instruction'):
                raise ValueError('Missing common selector prompt')
            fields = ('output',) if stage == 'sft' else ('chosen', 'rejected')
            actions = []
            for field in fields:
                action = json.loads(row[field])
                if not isinstance(action.get('rationale'), str) or not action['rationale'].strip():
                    raise ValueError('Explicit natural-language rationale is required')
                ids = action.get('selected_ids')
                if not isinstance(ids, list) or any(not isinstance(key, str) for key in ids) or len(ids) != len(set(ids)):
                    raise ValueError('Expected distinct selected candidate IDs')
                if len(ids) > 8:
                    raise ValueError('Selection exceeds the current eight-context limit')
                actions.append(set(ids))
            if stage == 'dpo' and actions[0] == actions[1]:
                raise ValueError('Different rationale wording alone is not a quality preference')
        count = len(data)
        if require_common_prompt:
            from localization_agent.selection.sft_contract import inspect_sft_rows
            tokenizer_path=Path(config['model_name_or_path'])/'tokenizer.json'
            if not tokenizer_path.exists():
                tokenizer_path=Path('artifacts/retrieval_tokenizer/tokenizer.json')
            contract=inspect_sft_rows(data,tokenizer_path)
            if contract['max_sequence_tokens']>config['cutoff_len']:
                raise ValueError('Common-template sequence exceeds cutoff_len; increase it before training')
        if verify_tokens:
            max_tokens = verify_tokenization(config, data)
    model = Path(config['model_name_or_path'])
    if not (model / 'config.json').exists():
        pending.append('local_model_not_ready' if stage == 'sft' else 'frozen_sft_model_not_merged')
    if max_tokens is None:
        pending.append('exact_template_length_and_loss_mask_check')
    pending.append('gpu_training_step_not_verified')
    return {'status': 'config_prepared_training_not_ready', 'stage': stage,
            'config': str(config_path), 'training_rows': count, 'dev_enabled': False,
            'epochs': config['num_train_epochs'], 'dataset_registry': str(target), 'pending': pending,
            'exact_max_sequence_tokens':max_tokens,
            'paid_calls': 0, 'model_weights_loaded': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--registry', default='configs/training/dataset_info.json')
    parser.add_argument('--output', required=True)
    parser.add_argument('--verify-tokenization', action='store_true', help='Requires the training environment; checks full real data and loss masks on CPU')
    parser.add_argument('--require-common-prompt',action='store_true',help='Check the shared selector observation schema and full untruncated text template')
    args = parser.parse_args()
    result = check(args.config, args.registry, args.verify_tokenization,args.require_common_prompt)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
