"""Offline, explicitly scoped Flare sample adapter. Uses only the Python standard library."""
from __future__ import annotations
import ast
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from localization_agent.schemas.game_text import Occurrence, ReferenceTranslation

REVISIONS = {
    "flare-game": "af6eee6d339ac98011864bfe89da837fe7769c28",
    "flare-engine": "4075191d576a28cbf4f27ec345ee1c7ef1482756",
}
MOD = "mods/empyrean_campaign/"


def read_po(path: Path) -> dict[tuple[str | None, str], dict[str, Any]]:
    """Read singular gettext entries, retaining flags, references and catalog lines."""
    entries = {}
    entry: dict[str, Any] = {"references": [], "flags": []}
    active = None
    for number, line in enumerate(path.read_text().splitlines() + [""], 1):
        if not line.strip():
            if entry.get("msgid"):
                if "msgid_plural" in entry:
                    raise ValueError(f"Plural catalog entry outside this sample profile: {path}:{number}")
                key = (entry.get("msgctxt"), entry["msgid"])
                if key in entries:
                    raise ValueError(f"Duplicate gettext key: {key}")
                entries[key] = entry
            entry = {"references": [], "flags": []}
            active = None
        elif line.startswith("#~"):
            active = None
        elif line.startswith("#:"):
            entry["references"].extend(line[2:].strip().split())
        elif line.startswith("#,"):
            entry["flags"].extend(flag.strip() for flag in line[2:].split(","))
        elif line.startswith("#"):
            continue
        elif line.startswith('"'):
            if active:
                entry[active] += ast.literal_eval(line)
        else:
            active, value = line.split(" ", 1)
            entry[active] = ast.literal_eval(value)
            entry[active + "_line"] = number
    return entries


def fields(path: Path) -> list[dict[str, Any]]:
    result = []
    section = None
    section_index = 0
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section, section_index = line[1:-1], section_index + 1
            continue
        if line.startswith("INCLUDE "):
            key, value = "INCLUDE", line[8:]
        else:
            key, value = line.split("=", 1)
        result.append({"field": key, "value": value, "line": number,
                       "section": section, "section_index": section_index})
    return result


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def build_sample(raw_root: Path, catalog_root: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    catalogs = {
        "flare-game": read_po(catalog_root / "flare-game/empyrean_campaign/data.zh.po"),
        "flare-engine": read_po(catalog_root / "flare-engine/engine.zh.po"),
    }
    catalog_paths = {
        "flare-game": "mods/empyrean_campaign/languages/data.zh.po",
        "flare-engine": "mods/default/languages/engine.zh.po",
    }
    occurrences: list[Occurrence] = []
    references: list[ReferenceTranslation] = []
    entities, blocks = [], []

    def game_fields(resource: str) -> list[dict[str, Any]]:
        return [{**f, "source_path": resource} for f in fields(raw_root / "flare-game" / resource)]

    def location(repo: str, path: str, line: int, field: str) -> dict[str, Any]:
        return {"repository": f"flareteam/{repo}", "revision": REVISIONS[repo],
                "path": path, "line": line, "field": field,
                "url": f"https://github.com/flareteam/{repo}/blob/{REVISIONS[repo]}/{path}#L{line}"}

    def emit(oid: str, repo: str, path: str, field: dict[str, Any], kind: str,
             role: str, context: dict[str, Any], native_id: str | None = None,
             entity_id: str | None = None, speaker_id: str | None = None,
             block_id: str | None = None, sequence_index: int | None = None) -> str:
        text = field["value"]
        entry = catalogs[repo][(None, text)]  # exact lookup; never fuzzy-match wording
        if not entry.get("msgstr") or "fuzzy" in entry["flags"]:
            raise ValueError(f"Selected sample has no usable catalog alignment: {oid}")
        occurrences.append({"schema_version": "game-text/0.1", "occurrence_id": oid,
            "game_id": "flare", "resource_namespace": "empyrean_campaign" if repo == "flare-game" else "engine",
            "native_id": native_id, "localization_key": text, "text_type": kind,
            "text_role": role, "source_language": "en", "source_text": text,
            "entity_id": entity_id, "speaker_id": speaker_id, "scene_id": None,
            "block_id": block_id, "sequence_index": sequence_index, "context": context,
            "source": location(repo, path, field["line"], field["field"])})
        references.append({"occurrence_id": oid, "language": "zh-Hans", "text": entry["msgstr"],
            "alignment_status": "exact_gettext_key", "quality_status": "upstream_unreviewed",
            "provenance": {"catalog": location(repo, catalog_paths[repo], entry["msgstr_line"], "msgstr"),
                "catalog_language": "zh", "msgctxt": entry.get("msgctxt"),
                "msgid": text, "flags": entry["flags"], "catalog_source_references": entry["references"],
                "reference_line_policy": "catalog references retained verbatim; actual source location takes precedence"}})
        return oid

    common_path = MOD + "npcs/base/abasi/common.txt"
    common = game_fields(common_path)
    name = next(f for f in common if f["field"] == "name")
    entities.append({"entity_id": "flare:player", "native_id": None, "name": None,
                     "identity_basis": "you script field; player role only"})
    for owner in ("abasi", "abasi_2"):
        owner_path = MOD + f"npcs/{owner}.txt"
        entity_id = f"flare:empyrean_campaign:npc:{owner}"
        owner_fields = game_fields(owner_path)
        entities.append({"entity_id": entity_id, "native_id": None, "name": name["value"],
                         "source": location("flare-game", common_path, name["line"], "name"),
                         "owner_path": owner_path, "fields": owner_fields + common})
        for include in owner_fields:
            if include["field"] != "INCLUDE" or "quest_" not in include["value"]:
                continue
            path = MOD + include["value"]
            script = game_fields(path)
            for index in sorted({f["section_index"] for f in script if f["section"] == "dialog"}):
                tokens = [f for f in script if f["section_index"] == index]
                bid = f"{entity_id}:{include['value']}:dialog:{index}"
                conditions = [f for f in tokens if f["field"].startswith("requires_")]
                topic = next((f["value"] for f in tokens if f["field"] == "topic"), None)
                ids, counts = [], Counter()
                for token in tokens:
                    if token["field"] not in ("him", "you"):
                        continue
                    counts[token["field"]] += 1
                    oid = f"{bid}:{token['field']}:{counts[token['field']]}"
                    ids.append(emit(oid, "flare-game", path, token, "dialogue", "utterance",
                        {"owner_path": owner_path, "include_site": location("flare-game", owner_path, include["line"], "INCLUDE"),
                         "topic": topic, "conditions": conditions, "speaker_field": token["field"],
                         "order_semantics": "declared_utterance_order_within_block"}, entity_id=entity_id,
                        speaker_id=entity_id if token["field"] == "him" else "flare:player",
                        block_id=bid, sequence_index=len(ids) + 1))
                blocks.append({"block_id": bid, "entity_id": entity_id, "topic": topic,
                    "conditions": conditions, "ordered_tokens": tokens, "occurrence_ids": ids,
                    "declared_next_edges": [{"from": a, "to": b} for a, b in zip(ids, ids[1:])],
                    "runtime_path_status": "not_verified", "inter_block_edges": None,
                    "source_path": path, "owner_path": owner_path})

    path = MOD + "items/categories/level_4_unique.txt"
    item_fields = game_fields(path)
    for index in sorted({f["section_index"] for f in item_fields}):
        tokens = [f for f in item_fields if f["section_index"] == index]
        native_id = next(f["value"] for f in tokens if f["field"] == "id")
        eid = f"flare:empyrean_campaign:item:{native_id}"
        entities.append({"entity_id": eid, "native_id": native_id, "source_path": path,
                         "fields": tokens, "base_include_status": "not_expanded"})
        for token in tokens:
            if token["field"] in ("name", "flavor"):
                emit(f"{eid}:{token['field']}", "flare-game", path, token, "item",
                     "name" if token["field"] == "name" else "description",
                     {"item_fields": tokens, "base_include_status": "not_expanded",
                      "description_kind": "flavor_text"}, native_id=native_id, entity_id=eid)

    path = "src/MenuConfig.cpp"
    labels = {"Video", "Audio", "Game", "Interface", "Input", "Keybindings", "Full Screen Mode",
              "V-Sync", "Frame Limit", "Sound Volume", "Music Volume", "Language"}
    for line_number, raw in enumerate((raw_root / "flare-engine" / path).read_text().splitlines(), 1):
        match = re.search(r'msg->get\("([^"\\]*)"\)', raw)
        if not match or match[1] not in labels:
            continue
        tab = re.search(r'setupTab\((\w+),', raw)
        option = re.search(r'cfg_tabs\[(\w+)\]\.setOptionWidgets\(([^,]+),', raw)
        if not tab and not option:
            continue
        symbols = [tab[1]] if tab else [option[1], option[2]]
        control = "/".join(symbols)
        emit(f"flare:engine:MenuConfig:{control}:label", "flare-engine", path,
             {"value": match[1], "line": line_number, "field": "msg->get"}, "ui", "label",
             {"panel": "MenuConfig", "tab_symbol": symbols[0], "native_control_symbols": symbols,
              "source_statement": raw.strip(), "display_order": None}, native_id=None)

    for filename, rows in (("source.jsonl", occurrences), ("references.jsonl", references),
                           ("entities.jsonl", entities), ("blocks.jsonl", blocks)):
        (output / filename).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    ref_by_id = {r["occurrence_id"]: r for r in references}
    preview = [{**o, "reference_text": ref_by_id[o["occurrence_id"]]["text"]} for o in occurrences]
    write_json(output / "review_bilingual.json", preview)
    with (output / "review_bilingual.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["occurrence_id", "text_type", "text_role", "native_id", "source_text", "reference_text", "speaker_id", "block_id", "sequence_index", "source_path", "source_line", "context"])
        for o in preview:
            writer.writerow([o[k] for k in ("occurrence_id", "text_type", "text_role", "native_id", "source_text", "reference_text", "speaker_id", "block_id", "sequence_index")] + [o["source"]["path"], o["source"]["line"], json.dumps(o["context"], ensure_ascii=False)])
    inputs = []
    for root in (raw_root, catalog_root):
        selected = list(root.rglob("*")) if root == raw_root else [catalog_root / "flare-game/empyrean_campaign/data.zh.po", catalog_root / "flare-engine/engine.zh.po"]
        for file in sorted(f for f in selected if f.is_file()):
            relative = file.relative_to(root)
            repo = relative.parts[0]
            upstream = relative.as_posix().split("/", 1)[1] if root == raw_root else catalog_paths[repo]
            inputs.append({"local_path": str(file), "bundle_path": ("raw/" if root == raw_root else "catalogs/") + relative.as_posix(),
                "upstream_url": f"https://github.com/flareteam/{repo}/blob/{REVISIONS[repo]}/{upstream}",
                "sha256_saved_bytes": hashlib.sha256(file.read_bytes()).hexdigest()})
    manifest = {"schema_version": "game-text/0.1", "profile": "flare-sample-v0.1", "revisions": REVISIONS,
        "counts": {"occurrences": len(occurrences), "categories": dict(Counter(o["text_type"] + "/" + o["text_role"] for o in occurrences)),
                   "blocks": len(blocks), "entities": len(entities), "references": len(references)},
        "inputs": inputs, "split": "unassigned_preview", "translation_quality": "upstream Chinese; not manually quality-rated",
        "language_mapping": "catalog zh displayed as zh-Hans based on sampled text; not a regional locale certification",
        "id_policy": "Native item IDs preserved. Other IDs derive from owner/resource/block ordinal/field ordinal or UI code symbols; fixed-snapshot stable, reconcile on script insertion/reordering.",
        "source_visibility": ["source.jsonl", "entities.jsonl", "blocks.jsonl"],
        "reference_only": ["references.jsonl", "review_bilingual.json", "review_bilingual.csv", "flare_sample.xlsx"],
        "licenses": {"game_content": "CC-BY-SA-3.0", "engine_source": "GPL-3.0-or-later"},
        "limitations": ["No executed playthrough or resolved inter-block branch graph", "No maps/scene attribution or base item include expansion", "Game and engine snapshots individually pinned; runtime compatibility not tested", "Flavor descriptions are not complete dynamic item tooltips"]}
    write_json(output / "manifest.json", manifest)
    return manifest
