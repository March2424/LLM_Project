"""Small offline checks for CSV identity, alignment and reference isolation."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from localization_agent.data.csv_import import CsvImportError, import_localization_csv

ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


class CsvImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def write_csv(self, name, rows):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            csv.writer(file).writerows(rows)
        return path

    def test_current_four_columns_and_reference_isolation(self):
        result = import_localization_csv([ROOT / "examples/localization.csv"], self.root / "out", dataset_id="demo")
        source = read_jsonl(result.output_dir / "source.jsonl")
        refs = read_jsonl(result.output_dir / "references.jsonl")
        self.assertEqual((result.source_records, result.reference_records, result.missing_reference_records), (5, 4, 1))
        self.assertEqual(source[0]["source_text"], source[1]["source_text"])
        self.assertNotEqual(source[0]["record_id"], source[1]["record_id"])
        self.assertIn("\n", source[0]["context"]["note"])
        self.assertEqual(source[-1]["source_text"], "Opens the gate, {player}.")
        self.assertEqual(source[2]["text_type"], "ui")
        self.assertNotIn(source[2]["record_id"], {r["record_id"] for r in refs})
        for row in source:
            self.assertEqual(set(row), {"record_id", "text_type", "source_text", "context"})
        self.assertNotIn("跟紧我。", (result.output_dir / "source.jsonl").read_text())

    def test_english_columns_structured_context_and_background_ids(self):
        path = self.write_csv("english.csv", [
            ["text_type", "source_text", "reference_text", "context", "native_id"],
            ["achievement_name", "First Step", "第一步", '{"speaker":"Alice","locations":["Bridge"]}', "game:37"],
        ])
        result = import_localization_csv([path], self.root / "out", dataset_id="game", context_format="json")
        row = read_jsonl(result.output_dir / "source.jsonl")[0]
        self.assertEqual(row["text_type"], "achievement_name")
        self.assertEqual(row["context"], {"speaker": "Alice", "locations": ["Bridge"]})
        self.assertNotIn("game:37", (result.output_dir / "source.jsonl").read_text())
        provenance = read_jsonl(result.output_dir / "background/provenance.jsonl")[0]
        self.assertEqual(provenance["extra_columns"], {"native_id": "game:37"})

    def test_reference_updates_and_same_named_inputs(self):
        header = ["text_type", "source_text", "reference_text"]
        a = self.write_csv("a/strings.csv", [header, ["ui", "Open", "打开"]])
        b = self.write_csv("b/strings.csv", [header, ["ui", "Open", "开启"]])
        first = import_localization_csv([a,b], self.root / "first", dataset_id="same")
        source = read_jsonl(first.output_dir / "source.jsonl")
        self.assertNotEqual(source[0]["record_id"], source[1]["record_id"])
        self.write_csv("a/strings.csv", [header, ["ui", "Open", "开放"]])
        second = import_localization_csv([a,b], self.root / "second", dataset_id="same")
        self.assertEqual((first.output_dir / "source.jsonl").read_bytes(), (second.output_dir / "source.jsonl").read_bytes())
        self.assertNotEqual((first.output_dir / "references.jsonl").read_bytes(), (second.output_dir / "references.jsonl").read_bytes())
        source_only = self.write_csv("source.csv", [["类型", "英文原文"], ["UI", "Close"]])
        result = import_localization_csv([source_only], self.root / "source_only", dataset_id="source")
        self.assertEqual(result.missing_reference_records, 1)
        self.assertEqual((result.output_dir / "references.jsonl").read_text(), "")

    def test_invalid_input_does_not_publish_partial_output(self):
        cases = [
            ([["text_type", "source_text"], ["ui", "Good"], ["ui", "Bad", "extra"]], "text"),
            ([["类型", "source_text", "英文原文"], ["UI", "A", "B"]], "text"),
            ([["text_type", "source_text", "context"], ["ui", "Good", '{"speaker":']], "json"),
            ([["text_type", "source_text"], ["ui", " "]], "text"),
        ]
        for index, (rows, context_format) in enumerate(cases):
            with self.subTest(case=index):
                path = self.write_csv(f"bad{index}.csv", rows)
                out = self.root / f"bad{index}"
                with self.assertRaises(CsvImportError) as raised:
                    import_localization_csv([path], out, dataset_id="bad", context_format=context_format)
                self.assertEqual(raised.exception.path, path)
                self.assertGreaterEqual(raised.exception.line_number, 1)
                self.assertFalse(out.exists())

    def test_existing_dataset_is_protected(self):
        out = self.root / "out"
        out.mkdir()
        protected = out / "source.jsonl"
        protected.write_text("existing record\n")
        with self.assertRaises(FileExistsError):
            import_localization_csv([ROOT / "examples/localization.csv"], out, dataset_id="demo")
        self.assertEqual(protected.read_text(), "existing record\n")
        empty = self.root / "empty"
        empty.mkdir()
        result = import_localization_csv([ROOT / "examples/localization.csv"], empty, dataset_id="demo")
        self.assertEqual(result.source_records, 5)


if __name__ == "__main__":
    unittest.main()
