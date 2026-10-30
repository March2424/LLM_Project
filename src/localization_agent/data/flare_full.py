"""Extract all pinned Flare gettext domains with static localization context.

Raw IDs, script predicates and file locations are kept in a separate provenance
stream. This is a text adapter, not a Flare game interpreter.
"""
from __future__ import annotations
import ast
from collections import Counter, defaultdict
import csv
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from localization_agent.data.flare_sample import REVISIONS, read_po, write_json
from localization_agent.schemas.game_text import LocalizationText, LocalizationReference, TEXT_TYPE_LABELS

TRIGGERS = set("msg him her you name title tooltip power_desc quest_text description tab_title currency_name flavor topic option caption text name_short text_base text_base_desc text_regen text_regen_desc text_steal text_steal_desc text_resist_steal text_resist_steal_desc text_combat_heal text_log_restore text_log_low text_tooltip_heal text_tooltip_cost tooltip_text_shield tooltip_text_heal".split())
PLAIN = set("msg him her you name title tooltip quest_text description topic flavor caption text".split())
CONTENT_DIRS = set("items powers enemies maps quests npcs cutscenes books scripts engine soundfx".split())
MODULE_NAMES = {"empyrean_campaign": "Empyrean 主战役", "fantasycore": "公共奇幻资源", "default": "引擎默认资源", "engine": "引擎界面与消息", "alpha_demo": "Alpha Demo（旧版）", "devlab": "开发教学模块", "minicore_alpha": "旧版缩放资源", "minicore": "缩放资源", "centered_statbars": "居中状态条模块", "gcw0_defaults": "GCW-Zero 配置"}
TYPE_NAMES = TEXT_TYPE_LABELS
PLACEHOLDERS = re.compile(r'\$\{[^}]+\}|%(?:\d+\$)?[-+#0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[hlLjzt]*[diuoxXfFeEgGaAcspn]|%%')
CSTRING = r'"(?:\\.|[^"\\])*"'
CGET = re.compile(r'\bmsg->getv?\s*\(\s*((?:' + CSTRING + r'\s*)+)')


def display_context(c: dict[str, Any]) -> str:
    labels = {"module": "资源范围", "speaker": "说话人", "character": "角色", "topic": "话题", "locations": "可能出现地点", "item_name": "物品", "item_description": "物品说明", "item_category": "物品类别", "skill_name": "技能", "skill_description": "技能说明", "quest_name": "任务", "related_quests": "相关任务", "location": "地点", "screen": "界面", "ui_section": "界面区域", "usage": "用途", "requirements": "条件", "conditional_variant": "条件文本", "previous": "前文（英文）", "next": "后文（英文）", "placeholders": "需保留占位符"}
    parts = []
    for key, value in c.items():
        if value is None or value == [] or value == "":
            continue
        if key in ("previous", "next"):
            value = " / ".join((r.get("speaker", "") + ": " if r.get("speaker") else "") + r["text"] for r in value)
        elif isinstance(value, list):
            value = "；".join(value)
        elif value is True:
            value = "是，具体触发条件留在后台"
        parts.append(labels.get(key, key) + "：" + str(value))
    return "\n".join(parts)


def build_full(raw: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    backend = output / "background"
    backend.mkdir(exist_ok=True)
    module_roots = {p.name: p for repo in ("flare-game", "flare-engine") for p in sorted((raw / repo / "mods").iterdir()) if p.is_dir()}
    module_repo = {m: "flare-engine" if p.is_relative_to(raw / "flare-engine") else "flare-game" for m, p in module_roots.items()}
    catalogs, templates, catalog_paths = {}, {}, {}
    for mod, base in module_roots.items():
        for stem in ("data", "engine"):
            po, pot = base / "languages" / (stem + ".zh.po"), base / "languages" / (stem + ".pot")
            if po.exists():
                domain = mod if stem == "data" else "engine"
                catalogs[domain], templates[domain] = read_po(po), read_po(pot)
                catalog_paths[domain] = po
    issues, excluded = [], Counter()

    def source_info(p: Path, line: int, field: str) -> dict[str, Any]:
        rel = p.relative_to(raw)
        repo, path = rel.parts[0], Path(*rel.parts[1:]).as_posix()
        return {"repository": "flareteam/" + repo, "revision": REVISIONS[repo], "path": path, "line": line, "field": field,
                "url": f"https://github.com/flareteam/{repo}/blob/{REVISIONS[repo]}/{path}#L{line}"}

    @lru_cache(None)
    def tokens(p: Path) -> list[dict[str, Any]]:
        result = []
        for n, raw_line in enumerate(p.read_text(encoding="utf-8-sig").splitlines(), 1):
            s = raw_line.strip()
            if not s or s.startswith("#") or s == "APPEND":
                continue
            if s.startswith("[") and s.endswith("]"):
                result.append({"kind": "section", "value": s[1:-1], "path": p, "line": n})
            elif s.startswith("INCLUDE "):
                result.append({"kind": "include", "value": s[8:].strip(), "path": p, "line": n})
            elif "=" in s:
                k, v = s.split("=", 1)
                result.append({"kind": "field", "field": k.strip(), "value": v.strip(), "path": p, "line": n})
            # Tile data rows contain no '=' and carry no localization text.
        return result

    @lru_cache(None)
    def fallback(mod: str) -> list[str]:
        order = []
        def visit(m):
            if m in order or m == "default":
                return
            order.append(m)
            if m in module_roots:
                settings = module_roots[m] / "settings.txt"
                if settings.exists():
                    for t in tokens(settings):
                        if t.get("field") == "requires":
                            for dependency in t["value"].split(","):
                                visit(dependency.split(":")[0].strip())
        visit(mod)
        return order + ["default"]

    def resolve(mod: str, resource: str) -> list[Path]:
        selected = []
        for m in fallback(mod):
            p = module_roots[m] / resource
            if p.is_file():
                selected.append(p)
                first = next((s.strip() for s in p.read_text().splitlines() if s.strip() and not s.lstrip().startswith("#")), "")
                if first != "APPEND":
                    break
        return list(reversed(selected))

    def expand(p: Path, mod: str, stack=(), chain=()):
        if p in stack:
            issues.append({"kind": "include_cycle", "path": str(p)})
            return
        for t in tokens(p):
            if t["kind"] == "include":
                targets = resolve(mod, t["value"])
                if not targets:
                    issues.append({"kind": "missing_include", "source": source_info(p, t["line"], "INCLUDE"), "target": t["value"]})
                for child in targets:
                    yield from expand(child, mod, stack + (p,), chain + (source_info(p, t["line"], "INCLUDE"),))
            else:
                yield {**t, "include_chain": list(chain)}

    @lru_cache(None)
    def blocks_for(p: Path, mod: str):
        blocks, section, values = [], None, []
        for t in expand(p, mod):
            if t["kind"] == "section":
                if values:
                    blocks.append((section, values))
                section, values = t["value"], []
            else:
                values.append(t)
        if values:
            blocks.append((section, values))
        return blocks

    def value(ts, key):
        return next((t["value"] for t in reversed(ts) if t["field"] == key), None)

    def text_value(t):
        v, key = t["value"], t["field"]
        if key in PLAIN:
            return v
        parts = v.split(",")
        if key == "option" and len(parts) == 5:
            return parts[-1]
        if len(parts) == 3:
            return parts[1]
        return parts[0]

    def eligible(p):
        rel = p.relative_to(module_roots[next(m for m, base in module_roots.items() if p.is_relative_to(base))]).as_posix()
        return rel.split("/")[0] in CONTENT_DIRS or rel in ("menus/powers.txt", "settings.txt")

    roots = {}
    for mod, base in module_roots.items():
        files = [p for p in sorted(base.rglob("*.txt")) if eligible(p)]
        consumed = {child for p in files for t in tokens(p) if t["kind"] == "include" for child in resolve(mod, t["value"])}
        roots[mod] = [p for p in files if p not in consumed]

    # Source-only indices provide names and candidate map locations, never Chinese.
    item_names, item_types, quest_status_names = {}, {}, defaultdict(set)
    npc_locations = defaultdict(set)
    for mod, files in roots.items():
        for p in files:
            rel = p.relative_to(module_roots[mod]).as_posix()
            if not rel.startswith(("items/", "quests/", "maps/")):
                continue
            bs = blocks_for(p, mod)
            all_fields = [t for _, ts in bs for t in ts]
            title = next((t["value"] for t in all_fields if t["field"] in (("title",) if rel.startswith("maps/") else ("name",))), None)
            if rel.startswith("maps/"):
                for section, ts in bs:
                    for t in ts:
                        if t["field"] == "npc" or (section == "npc" and t["field"] == "filename"):
                            npc_locations[(mod, t["value"])].add(title or "unknown")
            elif rel.startswith("quests/") and title:
                for t in all_fields:
                    if t["field"].endswith("status"):
                        quest_status_names[(mod, t["value"])].add(title)
            elif rel.startswith("items/"):
                for section, ts in bs:
                    if value(ts, "id") and value(ts, "name"):
                        (item_names if section == "item" else item_types)[(mod, value(ts, "id"))] = value(ts, "name")

    credits_settings = tokens(module_roots["default"] / "languages/credits_settings.txt")
    credit_files = {t["value"] for t in credits_settings if t.get("field") == "file"}
    allowed_credits = {t["value"] for t in credits_settings if t.get("field") == "s"}
    sources: list[LocalizationText] = []
    references: list[LocalizationReference] = []
    provenance, dialogue_blocks, reviews, uncovered_sources = [], [], [], []
    covered = defaultdict(set)

    def classify(resource, section, key):
        folder = resource.split("/")[0]
        if key in ("him", "her", "you"):
            return "dialogue"
        if key == "topic":
            return "dialogue_option"
        if folder == "items":
            return "item_name" if key == "name" and section == "item" else "item_description" if key in ("flavor", "power_desc", "description") else "item_property"
        if folder == "powers" or (folder == "menus" and resource == "menus/powers.txt"):
            if key in ("title", "tab_title"):
                return "ui"
            return "skill_name" if key == "name" else "skill_description"
        if folder == "quests":
            return "quest_name" if key == "name" else "quest_description"
        if folder == "npcs" and key == "name":
            return "character_name"
        if folder == "enemies" and key == "name":
            return "enemy_name"
        if folder == "maps" and key == "title":
            return "location_name"
        if key == "msg":
            return "system_message"
        if folder == "maps":
            return "world_tooltip"
        if folder == "books":
            return "book_text"
        if folder == "cutscenes":
            return "credits" if resource in credit_files else "narrative"
        if folder in ("engine", "menus", "soundfx") or resource == "settings.txt":
            return "ui"
        return "other"

    def choose_entry(mod, text):
        domains = list(dict.fromkeys(["engine"] + fallback(mod))) if mod == "engine" else fallback(mod) + ["engine"]
        for domain in domains:
            key = (None, text)
            if key in templates.get(domain, {}) or key in catalogs.get(domain, {}):
                return domain, key, catalogs.get(domain, {}).get(key)
        return None, (None, text), None

    def emit(mod, p, t, text, kind, context, block_no=None, native_id=None, raw_conditions=None):
        domain, key, entry = choose_entry(mod, text)
        # Mark each independent catalog key represented, even if also present in another domain.
        for d in set([mod, domain]):
            if key in templates.get(d, {}):
                covered[d].add(key)
        identity = [module_repo.get(mod, "flare-engine"), mod, str(p.relative_to(raw)), block_no,
                    str(t["path"].relative_to(raw)), t["line"], t.get("column"), t.get("field"), t.get("include_chain", [])]
        rid = "flr_" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
        placeholders = PLACEHOLDERS.findall(text)
        if placeholders:
            context["placeholders"] = placeholders
        source = {"record_id": rid, "text_type": kind, "source_text": text, "context": context}
        sources.append(source)
        status = "missing_catalog" if entry is None else "fuzzy" if "fuzzy" in entry["flags"] else "blank" if not entry.get("msgstr", "").strip() else "aligned"
        zh = entry.get("msgstr") if status == "aligned" else None
        if zh is not None:
            references.append({"record_id": rid, "reference_text": zh})
        catalog_info = None if entry is None else {"source": source_info(catalog_paths[domain], entry["msgstr_line"], "msgstr"), "msgid": entry["msgid"], "msgctxt": entry.get("msgctxt"), "flags": entry["flags"], "references": entry["references"], "raw_translation": entry.get("msgstr", "")}
        provenance.append({"record_id": rid, "module": mod, "catalog_domain": domain, "native_id": native_id,
            "resource": str(p.relative_to(raw)), "source": source_info(t["path"], t["line"], t.get("field", "msg->get")),
            "source_column": t.get("column"),
            "include_chain": t.get("include_chain", []), "block_index": block_no, "conditions": raw_conditions or [],
            "alignment_status": status, "quality_status": "upstream_unreviewed", "catalog": catalog_info})
        reviews.append({**source, "reference_text": zh, "alignment_status": status})
        if entry is None:
            uncovered_sources.append({"record_id": rid, "source_text": text, "source": provenance[-1]["source"]})
        return rid

    for mod, files in roots.items():
        for p in files:
            resource = p.relative_to(module_roots[mod]).as_posix()
            bs = blocks_for(p, mod)
            all_ts = [t for _, ts in bs for t in ts]
            header = bs[0][1] if bs and bs[0][0] is None else []
            root_name = value(header, "name")
            map_title = next((t["value"] for t in all_ts if t["field"] == "title"), None)
            book_groups = defaultdict(list)
            if resource.startswith("books/"):
                for _, paragraphs in bs:
                    scope = tuple((q["field"],q["value"]) for q in paragraphs if q["field"].startswith("requires_"))
                    book_groups[scope].extend(q for q in paragraphs if q["field"] == "text")
            # NPC-wide identity comes from its header (which may itself be INCLUDEd).
            for block_no, (section, ts) in enumerate(bs, 1):
                local_name = value(ts, "name") or root_name
                conditions = [t for t in ts if t["field"].startswith("requires_")]
                raw_conditions = [{"field": t["field"], "value": t["value"], "source": source_info(t["path"], t["line"], t["field"])} for t in conditions]
                dialogue = [t for t in ts if t["field"] in ("him", "her", "you")]
                captions = [t for t in ts if t["field"] in ("caption", "text")]
                if resource.startswith("books/"):
                    captions = book_groups[tuple((q["field"],q["value"]) for q in conditions)]
                def nearby(seq, index):
                    return [{"speaker": "Player" if q["field"] == "you" else root_name if q["field"] in ("him", "her") else None, "text": q["value"]} for q in seq[index]]
                ids = []
                for t in ts:
                    key = t["field"]
                    if key not in TRIGGERS or not t["value"]:
                        continue
                    text = text_value(t)
                    if not text:
                        continue
                    source_mod = next(m for m, base in module_roots.items() if t["path"].is_relative_to(base))
                    source_resource = t["path"].relative_to(module_roots[source_mod]).as_posix()
                    if source_resource in credit_files and text not in allowed_credits:
                        excluded["credits_person_names_or_links"] += 1
                        continue
                    # Numeric menu geometry is not display text.
                    if key == "name" and re.fullmatch(r'[\d, .+\-]+', text):
                        excluded["numeric_layout"] += 1
                        continue
                    kind = "credits" if source_resource in credit_files else classify(resource, section, key)
                    context = {"module": MODULE_NAMES.get(mod, mod)}
                    if resource.startswith("npcs/"):
                        context["character"] = root_name
                        context["locations"] = sorted(npc_locations[(mod, resource)])
                        context["topic"] = value(ts, "topic")
                    if key in ("him", "her", "you"):
                        i = dialogue.index(t)
                        context.update(speaker="Player" if key == "you" else root_name or "unknown",
                            previous=nearby(dialogue, slice(max(0, i-2), i)), next=nearby(dialogue, slice(i+1, i+3)))
                    if resource.startswith("items/") and section == "item":
                        context["item_name"] = local_name
                        if key == "name":
                            context["item_description"] = value(ts, "flavor")
                        context["item_category"] = next((item_types[(m, value(ts, "item_type"))] for m in fallback(mod) if (m, value(ts, "item_type")) in item_types), None)
                        if key == "power_desc":
                            context["usage"] = "Item use/equipped power description"
                    if resource.startswith("powers/"):
                        context["skill_name"] = local_name
                        if key == "name":
                            context["skill_description"] = value(ts, "description")
                        if key in ("title", "tab_title"):
                            context["screen"] = "Skills"
                            context["usage"] = "Skill tree/category title"
                    if resource.startswith("quests/"):
                        context["quest_name"] = root_name
                    if resource.startswith("maps/"):
                        context["location"] = map_title
                        context["usage"] = "Map location title" if key == "title" else "Map interaction message" if key == "msg" else "Map interaction tooltip"
                    if kind in ("book_text", "narrative"):
                        i = captions.index(t)
                        context["previous"] = nearby(captions, slice(max(0, i-2), i))
                        context["next"] = nearby(captions, slice(i+1, i+3))
                        context["usage"] = "Book text" if kind == "book_text" else "Cutscene caption"
                    if resource.startswith("engine/"):
                        context["usage"] = {"hero_options":"Default character name", "equip_flags":"Equipment requirement label", "primary_stats":"Character attribute label", "resource_stats":"Resource/status label", "damage_types":"Damage type label", "elements":"Element label", "titles":"Character title", "classes":"Character class", "loot":"Currency label"}.get(p.stem, p.stem.replace("_", " "))
                    if conditions:
                        context["conditional_variant"] = True
                        context["related_quests"] = sorted({q for c in conditions if c["field"].endswith("status") for m in fallback(mod) for q in quest_status_names[(m, c["value"])]})
                        reqs = []
                        for c in conditions:
                            if c["field"] in ("requires_item", "requires_not_item"):
                                for item in c["value"].split(","):
                                    ident, _, quantity = item.partition(":")
                                    name = next((item_names[(m, ident)] for m in fallback(mod) if (m, ident) in item_names), None)
                                    if name:
                                        reqs.append(("Must carry " if c["field"] == "requires_item" else "Must not carry ") + name + (" ×" + quantity if quantity else ""))
                        if reqs:
                            context["requirements"] = reqs
                    context = {k:v for k,v in context.items() if v is not None and v != []}
                    rid = emit(mod, p, t, text, kind, context, block_no, value(ts, "id"), raw_conditions)
                    if key in ("him", "her", "you"):
                        ids.append(rid)
                if ids:
                    dialogue_blocks.append({"module": mod, "resource": str(p.relative_to(raw)), "block_index": block_no,
                        "record_ids_in_declared_order": ids, "conditions": raw_conditions,
                        "events": [{"field": t["field"], "value": t["value"], "source": source_info(t["path"], t["line"], t["field"])} for t in ts], "runtime_path_status": "not_verified"})

    # Only literal gettext calls count as confirmed engine occurrences. Dynamic
    # source labels are handled above through their data files.
    engine_root = raw / "flare-engine"
    for p in sorted((engine_root / "src").glob("*")):
        if p.suffix not in (".cpp", ".h"):
            continue
        code = p.read_text()
        # Preserve offsets/lines while excluding comments, including quoted comment text.
        mask = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/')
        clean = mask.sub(lambda m: re.sub(r'[^\n]', ' ', m[0]) if m[0].startswith(('//','/*')) else m[0], code)
        tabs = {m[1]:m[2] for m in re.finditer(r'setupTab\((\w+),\s*msg->get\("([^"\\]+)"\)', clean)}
        for match in CGET.finditer(clean):
            text = ''.join(ast.literal_eval(q) for q in re.findall(CSTRING, match[1]))
            if not text:
                continue
            n = clean.count("\n", 0, match.start()) + 1
            line = code.splitlines()[n-1]
            screen = {"MenuConfig":"Settings", "MenuInventory":"Inventory", "MenuPowers":"Skills", "MenuCharacter":"Character", "MenuQuestLog":"Quest log", "MenuTalker":"Dialogue", "MenuVendor":"Merchant", "GameStateTitle":"Title screen", "GameStateLoad":"Load game", "GameStateNew":"New game"}.get(p.stem)
            context = {"module": MODULE_NAMES["engine"]}
            if screen:
                context["screen"] = screen
            tab = re.search(r'cfg_tabs\[(\w+)\]', line)
            if tab and tab[1] in tabs:
                context["ui_section"] = tabs[tab[1]]
            context["usage"] = "Interface label or tooltip" if p.stem.startswith(("Menu", "Widget", "GameState")) else "Item tooltip or item feedback" if p.stem == "ItemManager" else "Skill tooltip or feedback" if "Power" in p.stem else "Engine feedback or display text"
            kind = "ui" if p.stem.startswith(("Menu", "Widget", "GameState")) else "system_message"
            emit("engine", p, {"path":p,"line":n,"column":match.start()-clean.rfind("\n",0,match.start()),"field":"msg->getv" if "getv" in match[0] else "msg->get"}, text, kind, context)

    catalog_report = []
    for domain, template in templates.items():
        missing = template.keys() - covered[domain]
        catalog_report.append({"domain": domain, "template_keys":len(template), "represented_keys":len(template)-len(missing),
            "unrepresented_keys": [{"msgid":template[k]["msgid"],"references":template[k]["references"]} for k in sorted(missing, key=lambda k:k[1])],
            "translated_keys":sum(bool(e.get("msgstr", "").strip()) and "fuzzy" not in e["flags"] for e in catalogs[domain].values())})
    paired = [r for r in reviews if r["reference_text"] is not None]
    pending = [r for r in reviews if r["reference_text"] is None]
    module_order = {MODULE_NAMES[m]:i for i,m in enumerate(("empyrean_campaign","engine","fantasycore","default","alpha_demo","devlab","minicore_alpha","minicore","centered_statbars","gcw0_defaults"))}
    type_order = {k:i for i,k in enumerate(TYPE_NAMES)}
    for view in (paired, pending):
        view.sort(key=lambda r:(module_order[r["context"]["module"]],type_order[r["text_type"]]))
    def jsonl(name, rows):
        (output / name).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding="utf-8")
    for name, rows in (("source.jsonl",sources),("references.jsonl",references),("flare_bilingual.jsonl",paired),("flare_untranslated.jsonl",pending),("background/provenance.jsonl",provenance),("background/dialogue_blocks.jsonl",dialogue_blocks),("background/source_without_catalog.jsonl",uncovered_sources)):
        jsonl(name,rows)
    for name, rows in (("flare_bilingual.csv",paired),("flare_untranslated.csv",pending)):
        with (output/name).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f);w.writerow(["类型","英文原文","中文参考译文","本地化上下文"])
            w.writerows([TYPE_NAMES[r["text_type"]],r["source_text"],r["reference_text"],display_context(r["context"])] for r in rows)
    # Backend row lookup keeps primary CSV/XLSX free of IDs.
    jsonl("background/view_row_lookup.jsonl",[{"view":"bilingual","row":i+2,"record_id":r["record_id"]} for i,r in enumerate(paired)] + [{"view":"untranslated","row":i+2,"record_id":r["record_id"]} for i,r in enumerate(pending)])
    report={"schema_version":"localization-text/0.2","revisions":REVISIONS,"text_type_labels":TYPE_NAMES,"source_records":len(sources),"bilingual_records":len(paired),"untranslated_records":len(pending),
        "unique_bilingual_source_texts":len({r["source_text"] for r in paired}),
        "unique_bilingual_pairs":len({(r["source_text"],r["reference_text"]) for r in paired}),
        "reference_diagnostics":{"unchanged_from_english":sum(r["source_text"]==r["reference_text"] for r in paired),"without_han_characters":sum(not re.search('[\u3400-\u9fff]',r["reference_text"]) for r in paired)},
        "by_type":{TYPE_NAMES[k]:{"all":sum(r['text_type']==k for r in reviews),"bilingual":sum(r['text_type']==k for r in paired),"untranslated":sum(r['text_type']==k for r in pending)} for k in TYPE_NAMES if any(r['text_type']==k for r in reviews)},
        "by_module":{m:{"name":MODULE_NAMES.get(m,m),"all":sum(p['module']==m for p in provenance),"bilingual":sum(p['module']==m and p['alignment_status']=='aligned' for p in provenance),"untranslated":sum(p['module']==m and p['alignment_status']!='aligned' for p in provenance)} for m in sorted({p['module'] for p in provenance})},
        "alignment_status":dict(Counter(p['alignment_status'] for p in provenance)),"catalogs":catalog_report,"issues":issues,"excluded":dict(excluded),"dialogue_blocks":len(dialogue_blocks),
        "translation_quality":"upstream_unreviewed","split":"unassigned","reference_visibility":"references and bilingual views are offline only; runtime source is source.jsonl",
        "scope":"All five upstream Simplified Chinese gettext domains, static eligible text in every bundled mod and all literal engine gettext calls. Credits' nonlocalized people names/URLs and media excluded.",
        "limitations":["Static resource occurrences, not executed playthrough paths or one record per spawned NPC", "INCLUDE and APPEND are expanded against mod dependency fallback; optional module combinations are not executed", "Status predicates and native IDs are in background only; semantic quest/item links are source-derived", "No translation quality rating, model calls, train/dev/test split or complete dynamic tooltip reconstruction"]}
    write_json(output/'coverage.json',report)
    input_files = [{"path":p.relative_to(raw).as_posix(), "bytes":p.stat().st_size,
                    "sha256":hashlib.sha256(p.read_bytes()).hexdigest()}
                   for p in sorted(raw.rglob('*')) if p.is_file()]
    write_json(backend/'input_files.json',input_files)
    manifest = {"schema_version":"localization-text/0.2", "profile":"flare-full-v0.2",
        "source_language":"en", "reference_language":"zh-Hans", "original_catalog_language":"zh",
        "language_note":"zh-Hans describes the sampled writing system; no regional locale certification",
        "revisions":REVISIONS, "adapter_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_records":len(sources), "bilingual_records":len(paired), "untranslated_records":len(pending),
        "input_inventory":"background/input_files.json", "runtime_source":"source.jsonl",
        "offline_reference":"references.jsonl", "review_fields":["text_type","source_text","reference_text","context"],
        "identity_policy":"opaque record_id in JSONL; all native IDs and source locations in background; CSV/XLSX are review views, not identity-preserving reimport formats",
        "source_visibility":"Source-side context only. Reference translation and provenance catalogs are offline-only.",
        "split":"unassigned", "translation_quality":"upstream_unreviewed",
        "files":[{"path":p.relative_to(output).as_posix(),"sha256":hashlib.sha256(p.read_bytes()).hexdigest()}
                 for p in sorted(output.rglob('*')) if p.is_file() and p.name!='manifest.json']}
    write_json(output/'manifest.json',manifest)
    return report
