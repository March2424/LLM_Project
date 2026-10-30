"""Shared legacy game-text and simplified localization-text contracts."""
from __future__ import annotations
from typing import Any, Literal, TypedDict


class SourceLocation(TypedDict):
    repository: str
    revision: str
    path: str
    line: int
    field: str
    url: str


class Occurrence(TypedDict):
    schema_version: str
    occurrence_id: str
    game_id: str
    resource_namespace: str
    native_id: str | None
    localization_key: str
    text_type: Literal["dialogue", "ui", "item"]
    text_role: Literal["utterance", "label", "name", "description"]
    source_language: str
    source_text: str
    entity_id: str | None
    speaker_id: str | None
    scene_id: str | None
    block_id: str | None
    sequence_index: int | None
    context: dict[str, Any]
    source: SourceLocation


class ReferenceTranslation(TypedDict):
    occurrence_id: str
    language: str
    text: str
    alignment_status: str
    quality_status: str
    provenance: dict[str, Any]


class LocalizationText(TypedDict):
    """localization-text/0.2: engine identities live in separate provenance."""
    record_id: str
    text_type: str
    source_text: str
    context: dict[str, Any]


class LocalizationReference(TypedDict):
    record_id: str
    reference_text: str


TEXT_TYPE_LABELS = dict(dialogue="对话", dialogue_option="对话选项", character_name="角色名称", enemy_name="敌人名称", item_name="物品名称", item_description="物品描述", item_property="物品属性与类别", skill_name="技能名称", skill_description="技能描述", quest_name="任务名称", quest_description="任务说明", location_name="地点名称", world_tooltip="场景提示", book_text="书籍文本", narrative="剧情旁白", credits="制作人员表标签", ui="UI", system_message="系统消息", other="其他本地化文本")
