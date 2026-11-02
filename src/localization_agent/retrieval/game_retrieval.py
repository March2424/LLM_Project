"""Bounded name lookup plus one entity hop and lexical source fallback."""
from collections import deque
from dataclasses import replace

from localization_agent.schemas.graph import Candidate, CandidatePool
from localization_agent.schemas.memory import Evidence, is_mandatory
from .game_index import normalize_name


def retrieve_game(service, occurrence, view, snapshot_id):
    from .service import candidate_from_memory, canonical
    index=service.game_index_current
    if index.snapshot.project_id!=occurrence.project_id:raise ValueError('Graph snapshot belongs to another project')
    if occurrence.occurrence_id not in index.occurrences:raise ValueError('Snapshot needs a current valid occurrence node')
    current=Evidence(occurrence.occurrence_id,occurrence.source_version,occurrence.source)
    if service.allowed_source_ids is not None and occurrence.occurrence_id not in service.allowed_source_ids:
        raise ValueError('Current source is outside the declared source visibility')
    if index.sources.get(occurrence.occurrence_id)!=occurrence:raise ValueError('Current occurrence differs from registered source')
    resolved,bindings=service.bindings(occurrence,snapshot_id)
    effective=replace(occurrence,metadata={**occurrence.metadata,**resolved})
    candidates,related,unresolved={},set(),[]
    visited=set()
    start=index.occurrences[occurrence.occurrence_id]

    def allowed(obj):
        return service.reference_match(obj.scope,effective) and all(
            service._source_allowed(effective,e,True,reference=True) for e in obj.evidence)

    def put(item):
        if item.id in candidates:
            old=candidates[item.id]
            candidates[item.id]=replace(old,sources=tuple(dict.fromkeys(old.sources+item.sources)),
                graph_refs=tuple(dict.fromkeys(old.graph_refs+item.graph_refs)),
                relations=tuple(dict.fromkeys(old.relations+item.relations)))
        else:candidates[item.id]=item

    def support(refs):
        return tuple(dict.fromkeys(e for ref in refs for e in index.objects[ref].evidence))

    def source_item(ref,path=(),labels=('LEXICAL',)):
        if ref==start or (ref not in visited and len(visited)>=service.config.max_nodes):return
        node=index.nodes[ref]
        if not allowed(node):return
        visited.add(ref)
        source=index.sources[node.attributes['occurrence_id']]
        refs=tuple(dict.fromkeys(path+(ref,)))
        context=source.metadata.get('native_context','')
        text=source.source+ ('\nRaw applicability/context (not assumed true): '+context if context else '')
        put(Candidate('source:'+source.occurrence_id+'@'+str(source.source_version),'source',text,
            sources=support(refs),graph_refs=refs,relations=labels))
        related.add(source.occurrence_id)

    queue=deque()
    for key,refs in index.seed_groups(effective,service.config.max_entity_seeds):
        for ref in refs:queue.append((ref,(start,),('NAME_MATCH',),0,1))
    while queue and len(visited)<service.config.max_nodes:
        ref,path,labels,entity_hops,depth=queue.popleft()
        if ref in visited:continue
        node=index.nodes[ref]
        if not allowed(node):continue
        visited.add(ref)
        refs=tuple(dict.fromkeys(path+(ref,)))
        # Entity names route retrieval; the evidence, rather than bare names, fills the pool.
        if depth>=service.config.max_hops:continue
        incoming=[e for e in index.incoming[ref] if e.relation in {'DEFINES','MENTIONS','SPOKEN_BY'} and allowed(e)]
        incoming.sort(key=lambda e:(e.relation!='DEFINES',
            normalize_name(index.sources[index.nodes[e.source].attributes['occurrence_id']].source)==normalize_name(node.attributes.get('name','')),
            -index.lexical_score(effective.source,e.source),e.id))
        for edge in incoming[:service.config.max_neighbors]:
            source_item(edge.source,refs+(edge.ref,),labels+('REVERSE_'+edge.relation,))
        if entity_hops>=service.config.max_entity_hops:continue
        links=[e for e in index.outgoing[ref]+index.incoming[ref]
               if index.nodes[e.source].kind=='entity' and index.nodes[e.target].kind=='entity' and allowed(e)]
        for edge in sorted(links,key=lambda e:e.id)[:service.config.max_neighbors]:
            other=edge.target if edge.source==ref else edge.source
            target=index.nodes[other]
            if not allowed(target):continue
            linked=tuple(dict.fromkeys(refs+(edge.ref,target.ref)))
            left,right=index.nodes[edge.source],index.nodes[edge.target]
            text=left.attributes.get('name',left.id)+' — '+edge.relation+' → '+right.attributes.get('name',right.id)
            if edge.scope.conditions:text+='\nConditions: '+canonical(edge.scope.conditions)
            text+='\nEvidence status: '+edge.attributes.get('verification','approved')
            if edge.attributes.get('basis'):text+='; basis: '+edge.attributes['basis']
            if edge.attributes.get('reason'):text+='\nReason: '+edge.attributes['reason']
            put(Candidate('relation:'+edge.id+'@'+str(edge.version),'relation',text,
                sources=support(linked),graph_refs=linked,relations=labels+(edge.relation,)))
            direction=edge.relation if edge.source==ref else 'REVERSE_'+edge.relation
            queue.append((target.ref,refs+(edge.ref,),labels+(direction,),entity_hops+1,depth+1))
    if queue:unresolved.append('graph_truncated')
    # Lexical retrieval remains available when names/links are absent. No alias inference.
    for ref in index.lexical(effective.source,service.config.max_nodes):
        source_item(ref)
    bundle=service.memory.read(effective,view,related_ids=related)
    for kind, records in (('term',bundle.terms),('style',bundle.styles),('stm',bundle.stm),
                          ('ltm',bundle.ltm),('summary',bundle.summaries)):
        for record in records:
            if service.allowed_source_ids is not None and any(
                    e.occurrence_id not in service.allowed_source_ids for e in record.evidence):
                continue
            item=candidate_from_memory(record,mandatory=is_mandatory(record))
            put(replace(item,relations=('MEMORY_'+kind.upper(),)))
    hard=[c for c in candidates.values() if c.mandatory]
    soft=[c for c in candidates.values() if not c.mandatory]
    query=service.query_tokens(effective.source)
    def score(c):
        priority=(0 if 'REVERSE_DEFINES' in c.relations else 1 if c.category=='relation'
                  else 2 if 'NAME_MATCH' in c.relations else 3)
        bare=normalize_name(c.text.split('\n')[0]) in index.name_labels
        return (priority,bare,-len(query & service.query_tokens(c.text)),c.id)
    buckets=[[],[],[],[]]
    for item in soft:
        bucket=(1 if item.category in {'term','style'} else 2 if item.category=='translation'
                else 3 if item.category=='summary' else 0)
        buckets[bucket].append(item)
    for bucket in buckets:bucket.sort(key=score)
    selected=[]
    while any(buckets) and len(selected)<service.config.max_candidates:
        for bucket in buckets:
            if bucket and len(selected)<service.config.max_candidates:selected.append(bucket.pop(0))
    if any(buckets):unresolved.append('candidates_truncated')
    return CandidatePool(occurrence.occurrence_id,occurrence.source_version,view,snapshot_id,
        tuple(hard+selected),bundle.conflicts,tuple(unresolved),resolved,bindings)
