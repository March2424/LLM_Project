"""Generate source/target evidence review artifacts; no model calls and no reference input."""
import argparse
import html
import json
from collections import Counter
from pathlib import Path

LABELS={'committed':'已写回，待人工验收','failed_check':'检查未通过，未写回',
        'memory_update_failed':'译文已写回，记忆更新失败','error':'执行错误',
        'budget_exhausted':'预算耗尽','review_pending':'草稿已保存，待 Agent Reviewer',
        'needs_revision':'Reviewer 建议修改，草稿保留',
        'committed_with_warnings':'译例已写回，部分记忆更新有提示',
        'recorded_unreviewed':'已记录，未审试跑',
        'recorded_unreviewed_with_warnings':'已记录，未审试跑有提示'}


def report(folder):
    folder=Path(folder)
    cases=json.loads((folder/'cases.json').read_text())
    config=json.loads((folder/'config.json').read_text())
    budget=json.loads((folder/'budget.json').read_text())
    state=json.loads((folder/'state.json').read_text())
    trial=not config.get('require_reviewer',True)
    inventory=json.loads((folder/'memory_inventory.json').read_text()) if (folder/'memory_inventory.json').exists() else []
    persistence=json.loads((folder/'persistence.json').read_text()) if (folder/'persistence.json').exists() else None
    statuses=Counter(c['outcome']['status'] for c in cases)
    memories=Counter(r['kind'] for c in cases for r in c.get('new_memories',[]))
    probes=json.loads((folder/'memory_probes.json').read_text()) if (folder/'memory_probes.json').exists() else []
    read_counts=Counter(c['category'] for p in probes for c in p['pool']['candidates'] if c.get('memory_ref'))
    role_counts={role:sum(len(p.get('memory_roles',{}).get(role,[])) for p in probes)
                 for role in ['terms','styles','summaries','stm','ltm']}
    selected_counts=Counter(c['category'] for row in cases for c in row['pack']['selected'] if c.get('memory_ref'))
    isolation=json.loads((folder/'isolation.json').read_text()) if (folder/'isolation.json').exists() else None
    summary={'state':state,'game':config['project'],'model':config['model']['model'],
        'actual_models':sorted({call['reply']['model'] for c in cases for call in c['calls'] if call['reply']}),
        'selection':config['selection'],'seed':config['seed'],'statuses':dict(statuses),
        'committed_translations':sum(bool(c.get('committed_record')) for c in cases),
        'recorded_unreviewed_translations':sum(bool(c.get('unreviewed_record')) for c in cases),
        'semantic_review':'skipped_by_user' if trial else 'reviewer_required',
        'inventory_counts':dict(Counter(r['kind'] for r in inventory)),'persistence':persistence,
        'calls_by_task':dict(Counter(call['task'] for c in cases for call in c['calls'])),
        'recorded_new_memories':dict(memories),'approved_new_memories':{} if trial else dict(memories),
        'memory_selected_during_translation':dict(selected_counts),
        'memory_categories_in_postrun_pools':dict(read_counts),
        'memory_roles_in_postrun_reads':role_counts,
        'source_target_input_consistent':all(c.get('input_consistent',True) for c in cases),
        'other_run_isolated':not isolation['other_run_memory_candidates'] if isolation else None,
        'max_soft_tokens':max((c['pack']['soft_tokens'] for c in cases),default=0),
        'fewer_than_5_soft_cases':[c['number'] for c in cases if len(c['selection']['selected_short_ids'])<5],
        'budget':budget,'human_review':'pending','references_used':False}
    (folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    lines=['# Flare 真实翻译闭环试跑','',
        '本次按用户要求跳过 Reviewer，重点验证记忆生成、落库和重读；所有记录均未审，不是正式 TM。未读取参考译文。' if trial else
        '随机软证据＋固定闭源翻译器；自动审校结果不等于人工通过或独立质量评测。未读取参考译文。','',
        '人工核验请打开 **review.html**：可筛选、展开实际证据，填写判断/修改译文/备注，并导出人工核验 JSON。','',
        '```json',json.dumps(summary,ensure_ascii=False,indent=2),'```','',
        '| 序号 | 类型 | 原文 | 真实译文 | 状态 |','| --- | --- | --- | --- | --- |']
    for c in cases:
        clean=lambda s:s.replace('|','/').replace('\n','<br>')
        lines.append(f"| {c['number']} | {c['source']['metadata'].get('text_type','unknown')} | {clean(c['source']['source'])} | {clean(c['outcome']['target'])} | {LABELS.get(c['outcome']['status'],c['outcome']['status'])} |")
    lines += ['', '完整证据与输入：cases.json；逐次原始模型 prompt/回复：calls.jsonl；记忆/译例：run.sqlite；最终清单：memory_inventory.json；落库检查：persistence.json；后续读取：memory_probes.json；费用检查点：budget.json。', '',
        '本轮软预算按固定公开 tokenizer 统计，不冒充 DeepSeek 精确计费 token。费用为实际返回 usage 按配置峰时费率折算的保守估算，不等于服务账单。译文与记忆审查使用同一后端，不是独立 Judge。', '',
        '原提取库只读复制；本试跑库与运行记忆隔离。失败/部分写回状态保留，不使用参考修正模型结果。']
    (folder/'REPORT.md').write_text('\n'.join(lines)+'\n')
    if inventory:
        memory_lines=['# 记忆生成、记录与后续读取','',
            '本轮按用户要求不执行 Reviewer；所有条目为本次独立运行的未审记录，不是正式译文质量结论。','',
            '| 类别 | 当前有效记录数 |','| --- | --- |']
        for kind,label in [('translation','译例（STM/LTM 共用）'),('term','软术语'),('summary','主题摘要'),('style','角色笔记')]:
            memory_lines.append(f"| {label} | {summary['inventory_counts'].get(kind,0)} |")
        memory_lines+=['',f"独立只读连接核对：{persistence['checked_refs']} 个写回引用全部落库。" if persistence else '最终落库核对待完成。',
            f"API 调用 {budget['calls']} 次；保守费用估算 ${budget['cost']:.7f}。Reviewer 调用数为 {sum(n for task,n in summary['calls_by_task'].items() if task in {'check_game_translation','review_memory','review_style'})}。",'',
            '## 按处理顺序观察记忆增长','',
            '| 序号 | 原文 | 新译例 | 新术语 | 新摘要 | 新角色 | 翻译前 STM / LTM | 实际选入的记忆项 |',
            '| --- | --- | --- | --- | --- | --- | --- | --- |']
        for c in cases:
            counts=Counter(r['kind'] for r in c.get('new_memories',[]));before=c.get('memory_before',{})
            name=c['source']['source'].replace('|','/').replace('\n',' ')[:65]
            selected=Counter(x['category'] for x in c['pack']['selected'] if x.get('memory_ref'))
            memory_lines.append(f"| {c['number']} | {name} | {int(bool(c['outcome'].get('translation')))} | {counts['term']} | {counts['summary']} | {counts['style']} | {len(before.get('stm',[]))} / {len(before.get('ltm',[]))} | {dict(selected) or '无'} |")
        memory_lines+=['','## 全部当前有效记忆','',
            '下面的 Ref 和版本可以在 run.sqlite 或 memory_inventory.json 中定位；角色/摘要未更新时通常是无明确身份/主题或未达到更新间隔，不等于写入失败。','']
        for record in inventory:
            ref=record['ref'];memory_lines +=[f"### {record['kind']} · {ref['record_id']}@{ref['version']}",'']
            for key,value in record['content'].items():memory_lines.append(f"**{key}**：{value}\n")
            ids=', '.join(e['occurrence_id']+'@'+str(e['source_version']) for e in record['evidence'])
            memory_lines +=['来源：'+ids,'']
        memory_lines +=['## 使用情况与边界','',
            '实际进入后续翻译输入的记忆项：'+json.dumps(summary['memory_selected_during_translation'],ensure_ascii=False)+'。',
            '运行结束后的五类记忆读取次数（会重复读取同一记录）：'+json.dumps(role_counts,ensure_ascii=False)+'。','',
            'STM/LTM 是同一份译例的两种读取方式，不是额外复制的记录。摘要/角色在读取层和候选池可见，不代表本轮随机动作已经选中并使用。',
            '软术语提取当前较宽，可能包含普通短语、英文原样或占位符；数量不等于高价值术语数，本轮不为这些情况新增拒绝步骤。未审译名也可能不一致，后续可由 Reviewer/人工判断。']
        (folder/'MEMORY_REPORT.md').write_text('\n'.join(memory_lines)+'\n')
    esc=lambda value:html.escape(str(value))
    cards=[]
    for c in cases:
        source=c['source'];outcome=c['outcome'];selected=c['pack']['selected']
        choices=[]
        soft=[x for x in c['pool']['candidates'] if not x['mandatory']]
        for item in selected:
            short='硬项' if item['mandatory'] else 'c%02d'%(next(i for i,x in enumerate(soft) if x['id']==item['id'])+1)
            choices.append('<section class="evidence"><b>'+esc(short+' · '+item['category'])+'</b><pre>'+esc(item['text'])+
                '</pre><small>'+esc(item['id'])+'</small><details><summary>来源与路径版本</summary><pre>'+
                esc(json.dumps({'sources':item['sources'],'graph_refs':item['graph_refs'],'relations':item['relations']},ensure_ascii=False,indent=2))+'</pre></details></section>')
        tm=('译例已写入本试跑的 STM/LTM 共用记录（未审）。' if c.get('unreviewed_record') else
            '译例已写入 STM/LTM 共用记录。' if outcome.get('translation') else
            '译例草稿已保存；待 Agent Reviewer 后写回正式 TM。' if outcome.get('draft') else '本条未写入译例。')
        memory_text=tm+'\n\n'+('\n\n'.join(r['kind']+' '+json.dumps({k:v for k,v in r['content'].items() if k in {'source','target','note'}},ensure_ascii=False)
                                 for r in c.get('new_memories',[])) or '本条没有新增术语/摘要/角色笔记。')
        qa='\n'.join(outcome.get('issues',[])) or ('未执行 Reviewer；无程序提示。' if trial else '无检查问题记录；仍待人工判断。')
        review_calls=[x for x in c['calls'] if x['task']=='check_game_translation']
        qa+='\n\n'+ '\n'.join(x['reply']['text'] for x in review_calls if x['reply'])
        remaining=[x for x in c['pool']['candidates'] if x not in selected]
        before=c.get('memory_before',{})
        before_text={k:[{'ref':{'record_id':r['record_id'],'version':r['version']},
            'content':{key:value for key,value in r['content'].items() if key in {'source','target','note'}}} for r in before.get(k,[])]
            for k in ['terms','styles','summaries','stm','ltm']}
        counts=Counter(r['kind'] for r in c.get('new_memories',[]))
        cards.append(f'''<article data-status="{esc(outcome['status'])}" id="case-{c['number']}">
<h2>{c['number']:02d} · {esc(source['metadata'].get('text_type','unknown'))} <span class="badge">{esc(LABELS.get(outcome['status'],outcome['status']))}</span></h2>
<p>{esc(c['purpose'])}</p><small>{esc(source['occurrence_id'])} @ v{source['source_version']}</small>
<div class="pair"><section><h3>原文</h3><pre>{esc(source['source'])}</pre></section><section><h3>真实模型译文</h3><pre>{esc(outcome['target'])}</pre></section></div>
<details><summary>原生 context、身份与条件</summary><pre>{esc(c['pack']['manifest']['current_source_context'])}</pre></details>
<p>软候选 {len(soft)} 项 → 随机选中 {len(c['selection']['selected_short_ids'])} 项；软 token {c['pack']['soft_tokens']} / 1024；硬项 {sum(x['mandatory'] for x in selected)} 项；调用 {len(c['calls'])} 次。</p>
<p>本条记录：译例 {int(bool(outcome.get('translation')))}，术语 {counts['term']}，摘要 {counts['summary']}，角色 {counts['style']}。翻译前读取：STM {len(before.get('stm',[]))}，LTM {len(before.get('ltm',[]))}，术语 {len(before.get('terms',[]))}，摘要 {len(before.get('summaries',[]))}，角色 {len(before.get('styles',[]))}。</p>
<details><summary>翻译前读到的记忆</summary><pre>{esc(json.dumps(before_text,ensure_ascii=False,indent=2))}</pre></details>
<details><summary>实际输入的证据（含硬项）</summary>{''.join(choices) or '无上下文'}</details>
<details><summary>未选候选与随机过程</summary><pre>{esc(json.dumps({'selection':c['selection'],'unselected':remaining},ensure_ascii=False,indent=2))}</pre></details>
<details><summary>自动检查与占位符</summary><pre>{esc(qa)}</pre><pre>{esc(json.dumps(c['placeholder_check'],ensure_ascii=False,indent=2))}</pre></details>
<details><summary>译例与其他记忆更新</summary><pre>{esc(memory_text)}</pre></details>
<div class="review"><label>人工判断 <select data-field="decision"><option value="">待核验</option><option value="pass">通过</option><option value="revise">需修改</option><option value="reject">不通过</option></select></label>
<label>建议译文<textarea data-field="edited_translation" placeholder="需要修改时填写"></textarea></label>
<label>核验备注<textarea data-field="notes" placeholder="检查原意、专名/术语、条件、数值/占位符、证据是否有用或误导"></textarea></label></div></article>''')
    payload=json.dumps([{'number':c['number'],'id':c['source']['occurrence_id'],'source':c['source']['source'],
                         'translation':c['outcome']['target']} for c in cases],ensure_ascii=False).replace('<','\\u003c')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Flare 真实翻译 · 人工核验</title>
<style>body{margin:0;background:#f3f5f8;color:#1f2937;font:15px/1.6 system-ui,sans-serif}header,main{max-width:1180px;margin:auto;padding:24px}header{padding-bottom:8px}h1{margin:0}h2{font-size:19px;margin:0}h3{font-size:14px;color:#526077}article{background:white;border:1px solid #dbe2ea;border-radius:12px;padding:24px;margin-bottom:20px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}.pair section{padding:0 16px;background:#f8fafc;border-radius:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 system-ui,sans-serif}small{color:#66758a;overflow-wrap:anywhere}.badge{font-size:12px;background:#e5eaf0;border-radius:12px;padding:4px 10px;font-weight:400}details{border-top:1px solid #e5eaf0;padding:10px 0}summary{cursor:pointer;color:#2458a6}.evidence{padding:12px;border-left:3px solid #9bb5da;margin:10px 0;background:#f8fafc}.review{background:#eef5ff;padding:16px;border-radius:8px;display:grid;gap:10px}label{display:block}textarea{box-sizing:border-box;width:100%;min-height:62px;padding:8px;border:1px solid #b7c7db;border-radius:5px;font:inherit}select,input,button{font:inherit;padding:6px 10px;border:1px solid #bac7d6;border-radius:5px}button{background:#2458a6;color:white;cursor:pointer}.toolbar{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0;align-items:center}.note{color:#526077}.stats{padding:12px;background:#e8eff8;border-radius:8px}@media(max-width:720px){.pair{grid-template-columns:1fr}header,main{padding:14px}article{padding:16px}}</style>
<header><h1>Flare 真实翻译 · 人工核验</h1><p class="note">固定随机种子选择最多 5 条软证据，闭源模型翻译与自动审校。自动通过不代表人工通过；本页不含参考译文。</p>
<div class="stats">__STATS__</div><div class="toolbar"><input id="search" placeholder="搜索原文、译文或 ID"><select id="status"><option value="">全部运行状态</option>__OPTIONS__</select><button id="export">导出人工核验 JSON</button><span id="progress"></span></div>
<p class="note">核验重点：原意、专名/术语一致性、条件和数值、占位符完整性、随机证据是否有帮助或造成误导。填写暂存于当前浏览器；请导出 JSON 保存。</p></header><main>__CARDS__</main>
<script>const rows=__ROWS__;const key=__KEY__;let saved=JSON.parse(localStorage.getItem(key)||'{}');
const cards=[...document.querySelectorAll('article')];function progress(){document.getElementById('progress').textContent=Object.values(saved).filter(x=>x.decision).length+' / '+rows.length+' 已核验'}
cards.forEach((card,i)=>card.querySelectorAll('[data-field]').forEach(el=>{el.value=(saved[rows[i].id]||{})[el.dataset.field]||'';el.addEventListener('input',()=>{saved[rows[i].id]??={};saved[rows[i].id][el.dataset.field]=el.value;localStorage.setItem(key,JSON.stringify(saved));progress()})}));
function filter(){const q=document.getElementById('search').value.toLowerCase(),s=document.getElementById('status').value;cards.forEach(c=>c.hidden=(s&&c.dataset.status!==s)||!c.textContent.toLowerCase().includes(q))}
document.getElementById('search').addEventListener('input',filter);document.getElementById('status').addEventListener('change',filter);
document.getElementById('export').onclick=()=>{const data=rows.map(r=>({...r,decision:'',edited_translation:'',notes:'',...saved[r.id]}));const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify({run:key,reviewed_at:new Date().toISOString(),cases:data},null,2)],{type:'application/json'}));a.download='flare_human_review.json';a.click();URL.revokeObjectURL(a.href)};progress();</script></html>'''
    stats=f"已处理 {len(cases)} / {state['planned']}；已记录译例 {summary['committed_translations']+summary['recorded_unreviewed_translations']}；API 调用 {budget['calls']}；保守费用估算 ${budget['cost']:.4f} / ${budget['max_cost']:.2f}；人工状态：待核验。"
    if trial:
        page=page.replace('闭源模型翻译与自动审校。自动通过不代表人工通过；本页不含参考译文。',
                          '本次跳过 Reviewer，记录为未审试跑记忆；重点观察生成、落库和后续读取。本页不含参考译文。')
        page=page.replace('<div class="stats">','<p><a href="MEMORY_REPORT.md">记忆增长与全部记录</a> · <a href="memory_inventory.json">完整记忆清单 JSON</a> · <a href="persistence.json">落库核对</a></p><div class="stats">')
    page=page.replace('__STATS__',esc(stats)).replace('__OPTIONS__',''.join('<option value="'+esc(s)+'">'+esc(LABELS.get(s,s))+'</option>' for s in statuses))
    page=page.replace('__CARDS__',''.join(cards)).replace('__ROWS__',payload).replace('__KEY__',json.dumps(str(folder)))
    (folder/'review.html').write_text(page)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder')
    report(parser.parse_args().folder)
