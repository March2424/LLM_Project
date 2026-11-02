"""Source-provenance identities for memory; no name merging or reference text."""
import json
import re
from collections import defaultdict
from hashlib import sha256
from pathlib import PurePosixPath


def native_binding(game, row, provenance):
    context = dict(part.split('=', 1) for part in row['context'].split('; ') if '=' in part)
    metadata, proof = {}, {}
    topic = speaker = None
    if game == 'flare' and provenance['module'] != 'engine':
        path = provenance['resource']
        owner = [game, provenance['module'], path]
        if '/npcs/' in path:
            topic = owner + ['dialogue-resource']
            metadata['topic_name'] = context.get('npc_name') or PurePosixPath(path).stem
            if context.get('utterance_role') == 'npc' and context.get('npc_name'):
                speaker = owner + [context['npc_name']]
                metadata['speaker_name'] = context['npc_name']
        elif provenance.get('native_id') not in (None, ''):
            topic = owner + [provenance['native_id']]
            metadata['topic_name'] = PurePosixPath(path).stem + ':' + str(provenance['native_id'])
        elif '/cutscenes/' in path and row['text_type'] in {'book_text','narrative'}:
            topic = owner + ['source-document']
            metadata['topic_name'] = PurePosixPath(path).stem
        proof = {'resource':path, 'native_id':provenance.get('native_id'),
                 'source':provenance['source'], 'utterance_role':context.get('utterance_role')}
    elif game == 'shattered':
        key = provenance['localization_key']
        stem = key.rsplit('.', 1)[0]
        if stem.startswith(('items.', 'actors.buffs.', 'actors.mobs.')):
            topic = [game, stem]
            metadata['topic_name'] = stem
        use = provenance.get('use') or {}
        if ('.npcs.' in key and row['text_type'] == 'dialogue'
                and PurePosixPath(use.get('path','')).stem.casefold() == stem.rsplit('.',1)[-1].casefold()
                and ('Messages.get(this,' in use.get('expression','')
                     or '.this,' in use.get('expression',''))):
            speaker = [game, stem]
            metadata['speaker_name'] = stem.rsplit('.',1)[-1]
        proof = {'localization_key':key, 'definition':provenance['definition'],
                 'use':{k:use[k] for k in ('path','line','expression') if k in use}}
    for kind, identity in [('topic',topic),('speaker',speaker)]:
        if identity:
            token = sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()[:20]
            metadata[kind+'_id'] = 'native:'+kind+':'+token
    if not topic and not speaker:
        return None
    metadata['identity_origin'] = 'native_source_provenance'
    requirements = row['context'] if re.search(
        r'(?:^|;\s*)(?:requires_\w+|condition|offer_condition|availability_condition|case)\s*=',row['context']) else None
    return {'source_version':1,'source_hash':sha256(row['source_text'].encode()).hexdigest(),
            'source_chars':len(row['source_text']),
            'conditions':{'branch_id':None,'phase_id':None,'requirements':requirements},
            'metadata':metadata,'evidence':proof}


def memory_blocks(rows, bindings, excluded, topic_limit=600):
    """Select complete compatible blocks; never imposes a selector action quota."""
    groups = {'role':defaultdict(list),'topic':defaultdict(list)}
    for row in rows:
        key=(row['game'],row['source_id'])
        binding=bindings.get(row['game'],{}).get(row['source_id'])
        if key in excluded or not row['source_usable'] or row['split']!='train' or not binding:
            continue
        conditions=json.dumps(binding['conditions'],sort_keys=True)
        for kind,field in [('role','speaker_id'),('topic','topic_id')]:
            if field in binding['metadata']:
                groups[kind][(row['game'],binding['metadata'][field],conditions)].append(row)
    ordered, blocks, used = [], [], set()
    for kind,seed in [('role',2),('topic',5)]:
        topic_rows=defaultdict(int)
        topic_games={key[0] for key in groups['topic']}
        for key,items in sorted(groups[kind].items()):
            items.sort(key=lambda r:r['position'])
            distinct={}
            for row in items:
                distinct.setdefault(bindings[row['game']][row['source_id']]['source_hash'],row)
            items=list(distinct.values())  # Sampling evidence only; occurrence IDs remain separate records.
            if len(items)<=seed:
                continue
            items=items if kind=='role' else items[:12]
            if kind=='topic' and sum(bindings[r['game']][r['source_id']]['source_chars']>=40 for r in items)<3:
                continue
            if kind=='topic' and topic_rows[key[0]]+len(items)>topic_limit//max(1,len(topic_games)):
                continue
            blocks.append({'kind':kind,'game':key[0],'identity':key[1],
                'conditions':json.loads(key[2]),'source_ids':[r['source_id'] for r in items],
                'seed_rows':seed,'later_observation_opportunities':len(items)-seed})
            topic_rows[key[0]]+=len(items) if kind=='topic' else 0
            for row in items:
                pair=(row['game'],row['source_id'])
                if pair not in used:
                    used.add(pair)
                    ordered.append(row)
    return ordered,blocks
