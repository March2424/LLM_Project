"""Checks the sample's identity, dialogue structure and reference isolation."""
from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest

from localization_agent.data.flare_sample import build_sample

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless((ROOT / "artifacts/flare_sample_v0.1/raw/flare-game/mods/empyrean_campaign/npcs/abasi.txt").is_file() and (ROOT / "artifacts/data_source_audit_2026-10-29/raw/flare-engine/engine.zh.po").is_file(), "Optional local Flare snapshots are not downloaded")
class FlareSampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temp.name)
        cls.manifest = build_sample(ROOT / "artifacts/flare_sample_v0.1/raw",
            ROOT / "artifacts/data_source_audit_2026-10-29/raw", cls.output)
        cls.source = cls.read("source.jsonl")
        cls.references = cls.read("references.jsonl")
        cls.blocks = cls.read("blocks.jsonl")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def read(cls, name):
        return [json.loads(line) for line in (cls.output / name).read_text().splitlines()]

    def test_occurrences_are_not_merged_by_text(self):
        ids = [o["occurrence_id"] for o in self.source]
        self.assertEqual(len(ids), len(set(ids)))
        repeated = [o for o in self.source if o["source_text"] == "Any luck finding my journal?"]
        self.assertEqual(len(repeated), 2)
        self.assertEqual(len({o["entity_id"] for o in repeated}), 2)
        item_pairs = Counter(o["native_id"] for o in self.source if o["text_type"] == "item")
        self.assertEqual(item_pairs, dict.fromkeys(["90", "91", "92", "93", "94", "95", "430"], 2))

    def test_dialogue_order_conditions_and_effects(self):
        by_id = {o["occurrence_id"]: o for o in self.source}
        reward_blocks = [b for b in self.blocks if any(t["field"] == "reward_currency" for t in b["ordered_tokens"])]
        self.assertEqual(len(reward_blocks), 2)
        for block in reward_blocks:
            rows = [by_id[i] for i in block["occurrence_ids"]]
            self.assertEqual([r["sequence_index"] for r in rows], [1, 2, 3])
            self.assertEqual([r["context"]["speaker_field"] for r in rows], ["you", "him", "him"])
            self.assertIn("requires_item", [c["field"] for c in block["conditions"]])
            self.assertEqual(len(block["declared_next_edges"]), 2)
            self.assertIsNone(block["inter_block_edges"])

    def test_exact_reference_join_and_source_isolation(self):
        self.assertEqual({o["occurrence_id"] for o in self.source}, {r["occurrence_id"] for r in self.references})
        by_id = {o["occurrence_id"]: o for o in self.source}
        visible = "\n".join((self.output / name).read_text() for name in self.manifest["source_visibility"])
        for ref in self.references:
            self.assertEqual(ref["provenance"]["msgid"], by_id[ref["occurrence_id"]]["source_text"])
            self.assertEqual(ref["alignment_status"], "exact_gettext_key")
            self.assertNotIn(ref["text"], visible)


if __name__ == "__main__":
    unittest.main()
