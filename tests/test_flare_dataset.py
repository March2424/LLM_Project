"""Consistency check for the published processed snapshot; no raw inputs needed."""
import csv
import hashlib
import json
from pathlib import Path
import unittest

from localization_agent.data.flare_full import display_context
from localization_agent.schemas.game_text import TEXT_TYPE_LABELS

DATA = Path(__file__).resolve().parents[1] / "data/flare/v0.2"


def read_jsonl(name):
    return [json.loads(line) for line in (DATA / name).read_text(encoding="utf-8").splitlines()]


class FlarePublishedDatasetTest(unittest.TestCase):
    def test_processed_snapshot_is_complete_and_aligned(self):
        manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
        listed = {entry["path"] for entry in manifest["files"]}
        actual = {p.relative_to(DATA).as_posix() for p in DATA.rglob("*") if p.is_file()}
        self.assertEqual(actual, listed | {"manifest.json"})
        for entry in manifest["files"]:
            path = DATA / entry["path"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"], entry["path"])
        self.assertFalse(any(Path(name).suffix in {".po", ".pot", ".cpp", ".zip", ".gz"} for name in actual))
        self.assertFalse(any("raw" in Path(name).parts for name in actual))
        source_rows, ref_rows = read_jsonl("source.jsonl"), read_jsonl("references.jsonl")
        sources = {row["record_id"]: row for row in source_rows}
        refs = {row["record_id"]: row["reference_text"] for row in ref_rows}
        self.assertEqual(len(sources), len(source_rows))
        self.assertEqual(len(refs), len(ref_rows))
        self.assertEqual(len(sources), manifest["source_records"])
        self.assertEqual(len(refs), manifest["reference_records"])
        self.assertEqual(len(sources) - len(refs), manifest["missing_reference_records"])
        self.assertTrue(refs.keys() <= sources.keys())
        self.assertTrue(all(set(row) == {"record_id", "text_type", "source_text", "context"} for row in source_rows))
        self.assertTrue(all(set(row) == {"record_id", "reference_text"} for row in ref_rows))
        self.assertEqual({row["record_id"] for row in read_jsonl("background/provenance.jsonl")}, sources.keys())
        lookup = read_jsonl("background/view_row_lookup.jsonl")
        for view, filename in (("bilingual", "flare_bilingual.csv"), ("untranslated", "flare_untranslated.csv")):
            positions = [row for row in lookup if row["view"] == view]
            with (DATA / filename).open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[0], ["类型", "英文原文", "中文参考译文", "本地化上下文"])
            self.assertEqual(len(rows) - 1, len(positions))
            for position, row in zip(positions, rows[1:]):
                source = sources[position["record_id"]]
                self.assertEqual(row, [TEXT_TYPE_LABELS[source["text_type"]], source["source_text"],
                    refs.get(position["record_id"], ""), display_context(source["context"])])
        self.assertEqual(manifest["split"], "unassigned")
        self.assertEqual(manifest["translation_quality"], "upstream_unreviewed")


if __name__ == "__main__":
    unittest.main()
