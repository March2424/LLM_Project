"""Shared selector schema and text-only qwen3_nothink sequence accounting.

Template slots match LLaMA-Factory v0.9.5 data/template.py qwen3_nothink.
This lightweight tokenizer check does not replace the real processor/mask check.
"""
import json
from collections import Counter
from pathlib import Path


def inspect_sft_rows(rows, tokenizer_path, prompt_path='prompts/selection/context_selector.txt'):
    from tokenizers import Tokenizer
    tokenizer=Tokenizer.from_file(str(tokenizer_path))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    template=Path(prompt_path).read_text()
    maximum=0
    completion_maximum=0
    budgets=Counter()
    schema={'pool_hash','source','source_id','source_lang','target_lang','source_version','metadata',
            'max_selected','soft_token_budget','hard_context','candidates'}
    for index,row in enumerate(rows):
        if row.get('input',''):
            raise ValueError('Unexpected separate input at row '+str(index))
        observation=json.loads(row['instruction'].split('Observation: ',1)[1])
        if set(observation)!=schema:
            raise ValueError('Observation schema differs at row '+str(index))
        if row['instruction']!=template.format(observation=json.dumps(observation,ensure_ascii=False)):
            raise ValueError('Student prompt differs at row '+str(index))
        action=json.loads(row['output'])
        ids=action.get('selected_ids')
        if (not isinstance(action.get('rationale'),str) or not action['rationale'].strip()
                or not isinstance(ids,list) or any(not isinstance(k,str) for k in ids)
                or len(ids)!=len(set(ids)) or len(ids)>observation['max_selected']):
            raise ValueError('Invalid rationale/action schema at row '+str(index))
        if not set(ids)<={c['id'] for c in observation['candidates']}:
            raise ValueError('Selected ID outside observation at row '+str(index))
        prefix='<|im_start|>user\n'+row['instruction']+'<|im_end|>\n<|im_start|>assistant\n'
        suffix=row['output']+'<|im_end|>\n'
        prompt_ids=tokenizer.encode(prefix,add_special_tokens=False).ids
        response_ids=tokenizer.encode(suffix,add_special_tokens=False).ids
        maximum=max(maximum,len(prompt_ids)+len(response_ids))
        completion_maximum=max(completion_maximum,len(response_ids))
        budgets[str(observation['soft_token_budget'])]+=1
    recommended=8192
    while recommended<maximum:
        recommended*=2
    return {'rows':len(rows),'common_prompt_schema_verified':True,'max_sequence_tokens':maximum,
        'max_completion_tokens':completion_maximum,'recommended_cutoff_len':recommended,
        'observed_soft_token_budgets':dict(budgets),'template':'qwen3_nothink',
        'train_on_prompt':False,'mask_history':False,'packing':False,
        'loss_mask_contract':'all prompt tokens -100; full rationale+IDs and assistant terminator supervised',
        'framework_loss_mask_verified':False,'silent_truncation_allowed':False}
