"""Reference-free game-localization review and small deterministic format checks."""
from collections import Counter
from dataclasses import asdict
import json
import re

from localization_agent.models.structured import parse_json_reply
from localization_agent.schemas.memory import ReadView, text_hash

WORKFLOW_VERSION = 'autonomous/1'
POLICY_VERSION = 'game-localization/2'


def format_checks(source: str, target: str, metadata: dict, placeholder_pattern) -> list[str]:
    issues = []
    if Counter(placeholder_pattern.findall(source)) != Counter(placeholder_pattern.findall(target)):
        issues.append('Placeholder/tag mismatch')
    # Unnumbered printf arguments are positional. Numbered/named arguments may reorder.
    printf = re.compile(r'%%|%(?:\d+\$)?[-+#0 ]*(?:\d+|\*)?(?:\.\d+)?[sdif]')
    unnumbered = lambda text: [x for x in printf.findall(text) if x != '%%' and not re.match(r'%\d+\$', x)]
    if unnumbered(source) != unnumbered(target):
        issues.append('Unnumbered printf argument order/type mismatch: preserve ' + repr(unnumbered(source)))
    tags = re.compile(r'<(/?)([A-Za-z][\w:-]*)\b[^>]*>')

    def balanced(text):
        stack = []
        for match in tags.finditer(text):
            close, name = match.group(1), match.group(2).lower()
            if match.group().endswith('/>') or name in {'br', 'hr', 'img', 'input', 'wbr'}:
                continue
            if close:
                if not stack or stack.pop() != name:
                    return False
            else:
                stack.append(name)
        return not stack

    if balanced(source) and not balanced(target):
        issues.append('Markup tags are unbalanced or incorrectly nested')
    if metadata.get('max_length') is not None:
        limit = int(metadata['max_length'])
        if len(target) > limit:
            issues.append(f'Target exceeds explicit max_length={limit} Unicode characters')
    return issues


def policy_hash(memory) -> str:
    template = (memory.config.prompt_dir / 'review_autonomous.txt').read_text(encoding='utf-8')
    return text_hash(POLICY_VERSION + template)


def assess_draft(session, draft_ref) -> dict:
    """A separate model call returns a decision, never a replacement translation."""
    store = session.memory.store
    draft = store.get(draft_ref)
    occurrence = store.source(draft.scope.project_id, draft.evidence[0])
    manifest, context_hash = draft.content['context_manifest'], draft.content['context_hash']
    pack = session.restore_context(occurrence, manifest, context_hash)
    pool = session.retriever.retrieve(occurrence, ReadView(**manifest['view']), manifest['graph_snapshot'])
    observation = session.retriever.selector_observation(pool)
    checks = session.check_warnings(occurrence, pack, draft.content['target'])
    inputs = {'policy': POLICY_VERSION, 'draft': asdict(draft_ref), 'source': occurrence.source,
        'source_version': occurrence.source_version, 'source_lang': occurrence.source_lang,
        'target_lang': occurrence.target_lang, 'target': draft.content['target'],
        'source_context': manifest['current_source_context'], 'context_hash': context_hash,
        'actual_translator_context': pack.model_context,
        'technical_issues': checks, 'reselection_observation': observation}
    # Draft identity and policy are in the prompt; replay reuses only this exact logged check.
    raw, call_id = session.memory._call('review_game_autonomous', 'review_autonomous.txt',
        _reuse_logged=True, inputs=json.dumps(inputs, ensure_ascii=False, sort_keys=True))
    result = parse_json_reply(raw)
    actions = {'accept', 'repair', 'reselect', 'stop'}
    if (not isinstance(result, dict) or result.get('action') not in actions
            or not isinstance(result.get('reason'), str) or not result['reason'].strip()
            or not isinstance(result.get('issues'), list) or not isinstance(result.get('selected_ids'), list)):
        raise ValueError('Automatic review requires action, reason, issues and selected_ids')
    for issue in result['issues']:
        if (not isinstance(issue, dict) or issue.get('category') not in
                {'accuracy', 'terminology', 'fluency', 'style', 'format', 'context'}
                or issue.get('severity') not in {'minor', 'major', 'critical'}
                or any(not isinstance(issue.get(k), str) for k in ('source_quote', 'target_quote', 'instruction'))
                or not issue['instruction'].strip()
                or not (issue['source_quote'] or issue['target_quote'])
                or (issue['source_quote'] and issue['source_quote'] not in occurrence.source)
                or (issue['target_quote'] and issue['target_quote'] not in draft.content['target'])):
            raise ValueError('Review defect must have an actual source/target quote and an actionable correction')
    if any(not isinstance(key, str) for key in result['selected_ids']):
        raise ValueError('Reselection IDs must be strings')
    if result['action'] != 'reselect' and result['selected_ids']:
        raise ValueError('Only reselect may choose evidence IDs')
    if result['action'] == 'reselect':
        # Uses the same frozen pool, with hard items and token limits enforced by M3.
        session.retriever.select_context(pool, tuple(result['selected_ids']))
    effective = result['action']
    if effective == 'accept' and (result['issues'] or checks):
        effective = 'repair'
    if effective == 'repair' and not result['issues'] and not checks:
        raise ValueError('Repair without a concrete defect is not allowed')
    response = json.loads(store.db.execute('SELECT response FROM model_calls WHERE id=?', (call_id,)).fetchone()[0])
    return {**result, 'model_action': result['action'], 'action': effective, 'technical_issues': checks,
        'call_id': call_id, 'reviewer': 'ai:auto-review:' + response['model'] + ':' + call_id,
        'kind': 'ai', 'draft': asdict(draft_ref), 'target_hash': text_hash(draft.content['target']),
        'context_hash': context_hash, 'policy_hash': policy_hash(session.memory),
        'model': response['model']}


def repair_feedback(decision: dict) -> str:
    return json.dumps({'reason': decision['reason'], 'defects': decision['issues'],
                       'technical_issues': decision['technical_issues']}, ensure_ascii=False)
