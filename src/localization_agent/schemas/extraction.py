"""Game graph extraction v1: a small vocabulary shared by prompts and validation."""
SCHEMA_VERSION = 'game-graph/1.1'
ENTITY_TYPES = ('character', 'skill', 'item', 'location', 'organization', 'quest')
RELATIONS = ('MENTIONS', 'DEFINES', 'SPOKEN_BY', 'ADDRESSED_TO', 'MEMBER_OF',
             'LOCATED_IN', 'COUNTERS', 'USES', 'PART_OF')

ENTITY_DESCRIPTIONS = {
    'character': 'Person, NPC, enemy or identifiable acting character; do not merge a role with a person.',
    'skill': 'Skill, spell, ability or independently named effect.',
    'item': 'Physical object, equipment, material, tool or vehicle; no game-specific top-level types.',
    'location': 'Place or spatial region at any scale.',
    'organization': 'Organization, faction or organized group.',
    'quest': 'Named mission, task or quest line.',
}
RELATION_DESCRIPTIONS = {
    'MENTIONS': 'text -> entity: names or clearly refers to the entity.',
    'DEFINES': 'text -> entity: names, describes or explains the entity; name labels also qualify.',
    'SPOKEN_BY': 'text -> character: the speaker, not a person merely mentioned.',
    'ADDRESSED_TO': 'text -> character: identifiable addressee.',
    'MEMBER_OF': 'character -> organization: current membership, subject to stated conditions.',
    'LOCATED_IN': 'entity -> location: spatial location or containment.',
    'COUNTERS': 'skill/item -> skill/item: counteracts or neutralizes, not generic association.',
    'USES': 'character/organization/item -> skill/item: uses an ability or physical object.',
    'PART_OF': 'entity -> entity: component of a larger whole, not ownership or membership.',
}


def prompt_vocabulary() -> dict[str, str]:
    """Inject the same allowed types and meanings into the extraction prompt."""
    return {
        'entity_types': ','.join(ENTITY_TYPES), 'relation_types': ','.join(RELATIONS),
        'entity_definitions': '\n'.join(f'{key}: {ENTITY_DESCRIPTIONS[key]}' for key in ENTITY_TYPES),
        'relation_definitions': '\n'.join(f'{key}: {RELATION_DESCRIPTIONS[key]}' for key in RELATIONS),
    }


def extraction_schema() -> dict:
    evidence = {'type': 'array', 'minItems': 1, 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['id', 'field', 'quote'], 'properties': {
            'id': {'type': 'string', 'minLength': 1},
            'field': {'enum': ['source', 'context']},
            'quote': {'type': 'string', 'minLength': 1}}}}
    entity = {'type': 'object', 'additionalProperties': False,
        'required': ['key', 'name', 'entity_type', 'reuse_id', 'evidence'], 'properties': {
            'key': {'type': 'string', 'minLength': 1}, 'name': {'type': 'string', 'minLength': 1},
            'entity_type': {'enum': list(ENTITY_TYPES)},
            'reuse_id': {'type': ['string', 'null']}, 'evidence': evidence}}
    relation = {'type': 'object', 'additionalProperties': False,
        'required': ['key', 'source', 'target', 'relation', 'conditions', 'evidence'], 'properties': {
            'key': {'type': 'string', 'minLength': 1},
            'source': {'type': 'string', 'pattern': '^(text|entity):.+'},
            'target': {'type': 'string', 'pattern': '^entity:.+'},
            'relation': {'enum': list(RELATIONS)},
            'conditions': {'type': 'object', 'additionalProperties': {
                'type': 'array', 'minItems': 1, 'items': {'type': 'string', 'minLength': 1}}},
            'evidence': evidence, 'basis': {'enum': ['direct', 'inferred']},
            'reason': {'type': 'string'}}, 'allOf': [{
                'if': {'required': ['basis'], 'properties': {'basis': {'const': 'inferred'}}},
                'then': {'required': ['reason'], 'properties': {'reason': {'minLength': 1}}}}]}
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
        'title': SCHEMA_VERSION, 'type': 'object', 'additionalProperties': False,
        'required': ['entities', 'relations'], 'properties': {
            'entities': {'type': 'array', 'items': entity},
            'relations': {'type': 'array', 'items': relation}}}
