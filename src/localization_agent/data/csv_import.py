"""Import engine-independent localization CSVs with a separate reference side."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import tempfile
from typing import Any, Literal, Sequence

from localization_agent.schemas.game_text import (
    LocalizationReference, LocalizationText, TEXT_TYPE_LABELS,
)

COLUMN_ALIASES = {
    "text_type": ("类型", "text_type"),
    "source_text": ("英文原文", "source_text"),
    "reference_text": ("中文参考译文", "reference_text"),
    "context": ("本地化上下文", "context"),
}


class CsvImportError(ValueError):
    """An input problem with file, logical record and physical line locations."""

    def __init__(self, path: Path, record_number: int, line_number: int, message: str):
        self.path = path
        self.record_number = record_number
        self.line_number = line_number
        super().__init__(f"{path}:{line_number} (record {record_number}): {message}")


@dataclass(frozen=True)
class CsvImportResult:
    output_dir: Path
    source_records: int
    reference_records: int
    missing_reference_records: int


def import_localization_csv(
    input_files: Sequence[Path],
    output_dir: Path,
    *,
    dataset_id: str,
    context_format: Literal["text", "json"] = "text",
    source_language: str = "en",
    reference_language: str = "zh-Hans",
) -> CsvImportResult:
    """Import UTF-8 CSVs into a new/empty directory, preserving input order.

    Type and source columns are required; context and reference are optional.
    Other columns are kept only in offline provenance. IDs depend on the dataset
    namespace, input order/name and logical CSV record number, never the target.
    """
    if not input_files or not dataset_id.strip():
        raise ValueError("At least one input CSV and a nonempty dataset_id are required")
    if context_format not in ("text", "json"):
        raise ValueError("context_format must be text or json")
    output_dir = Path(output_dir)
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise FileExistsError(f"Output must be a new or empty directory: {output_dir}")
    sources: list[LocalizationText] = []
    references: list[LocalizationReference] = []
    provenance, input_inventory = [], []
    type_aliases = {**{k: k for k in TEXT_TYPE_LABELS}, **{v: k for k, v in TEXT_TYPE_LABELS.items()}}

    for input_index, file in enumerate(input_files, 1):
        path = Path(file)
        raw = path.read_bytes()
        input_info = {"input_index": input_index, "name": path.name,
                      "sha256": hashlib.sha256(raw).hexdigest()}
        input_inventory.append(input_info)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise CsvImportError(path, 0, 1, "CSV must be UTF-8 (BOM is supported)") from error
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        record_number = 0
        try:
            raw_headers = next(reader, None)
            if raw_headers is None:
                raise CsvImportError(path, 0, 1, "CSV has no header")
            headers = [h.strip() for h in raw_headers]
            if any(not h for h in headers) or len(set(headers)) != len(headers):
                raise CsvImportError(path, 0, reader.line_num, "Column names must be nonempty and unique")
            columns = {}
            for field, aliases in COLUMN_ALIASES.items():
                matches = [i for i, h in enumerate(headers) if h in aliases]
                if len(matches) > 1:
                    raise CsvImportError(path, 0, reader.line_num, f"Ambiguous columns for {field}")
                if matches:
                    columns[field] = matches[0]
            for field in ("text_type", "source_text"):
                if field not in columns:
                    raise CsvImportError(path, 0, reader.line_num, f"Missing column: {field}")
            previous_line = reader.line_num
            for record_number, row in enumerate(reader, 1):
                start_line, previous_line = previous_line + 1, reader.line_num
                if not row or not any(cell.strip() for cell in row):
                    continue
                if len(row) != len(headers):
                    raise CsvImportError(path, record_number, start_line,
                                         f"Expected {len(headers)} cells, got {len(row)}; quote commas/newlines")
                values = {field: row[index] for field, index in columns.items()}
                for field in ("text_type", "source_text"):
                    if not values[field].strip():
                        raise CsvImportError(path, record_number, start_line, f"Empty {field}")
                context_text = values.get("context", "")
                context: dict[str, Any] = {}
                if context_text.strip():
                    if context_format == "json":
                        try:
                            context = json.loads(context_text)
                        except json.JSONDecodeError as error:
                            raise CsvImportError(path, record_number, start_line, "Invalid JSON context") from error
                        if not isinstance(context, dict):
                            raise CsvImportError(path, record_number, start_line, "JSON context must be an object")
                    else:
                        context = {"note": context_text}
                identity = [dataset_id, input_index, path.name, record_number]
                record_id = "csv_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:20]
                original_type = values["text_type"].strip()
                sources.append({"record_id": record_id,
                    "text_type": type_aliases.get(original_type, original_type),
                    "source_text": values["source_text"], "context": context})
                reference = values.get("reference_text", "")
                if reference.strip():
                    references.append({"record_id": record_id, "reference_text": reference})
                provenance.append({"record_id": record_id, "input_index": input_index,
                    "input_name": path.name, "record_number": record_number,
                    "line_start": start_line, "line_end": reader.line_num,
                    "original_type": values["text_type"],
                    "extra_columns": {h: row[i] for i, h in enumerate(headers) if i not in columns.values()},
                    "reference_status": "present_unreviewed" if reference.strip() else "missing"})
        except csv.Error as error:
            raise CsvImportError(path, record_number + 1, reader.line_num, str(error)) from error

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    # Publish all files together only after every input has parsed successfully.
    with tempfile.TemporaryDirectory(prefix=".csv-import-", dir=output_dir.parent) as temp:
        stage = Path(temp) / "dataset"
        (stage / "background").mkdir(parents=True)
        for name, rows in (("source.jsonl", sources), ("references.jsonl", references),
                           ("background/provenance.jsonl", provenance)):
            (stage / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        manifest = {"schema_version": "localization-text/0.2", "adapter": "csv-import/0.1",
            "dataset_id": dataset_id, "source_language": source_language,
            "reference_language": reference_language, "context_format": context_format,
            "source_records": len(sources), "reference_records": len(references),
            "missing_reference_records": len(sources) - len(references),
            "inputs": input_inventory, "runtime_source": "source.jsonl",
            "offline_reference": "references.jsonl", "split": "unassigned",
            "reference_quality": "unreviewed", "id_policy": "dataset_id + input order/name + logical record number; no content deduplication; not an ID-preserving Flare round trip",
            "context_policy": "Caller supplies source-side context; importer does not infer or semantically audit context",
            "files": [{"path": p.relative_to(stage).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                      for p in sorted(stage.rglob("*.jsonl"))]}
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if output_dir.exists():
            output_dir.rmdir()  # Only an empty directory may be replaced.
        stage.rename(output_dir)
    return CsvImportResult(output_dir, len(sources), len(references), len(sources) - len(references))
