import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from localization_agent.memory import MemoryConfig, MemoryService, MemoryStore, VersionConflict
from localization_agent.models.protocols import ModelReply
from localization_agent.schemas.memory import Evidence, Occurrence, Scope, TranslationCheck, scope_match, text_hash


class ScriptedModel:
    """Offline contract fixture; not a translation or quality evaluator."""
    def __init__(self):
        self.calls = []

    def __call__(self, task, prompt):
        self.calls.append((task, prompt))
        if task == "extract_terms":
            text = '[{"source":"Aurora","target":"曙光"}]'
        elif task == "retrieve":
            candidates = json.loads(prompt.split("Candidates:\n")[1].split("\nQuery:")[0])
            text = json.dumps([candidates[0]["id"]])
        else:
            text = f"fixture-{task}"
        return ModelReply(text, "offline-fixture", 10, 5)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "memory.sqlite"
        self.store = MemoryStore(self.path)
        self.store.create_snapshot("k0", "game")
        self.store.start_run("r1", "game", "d1", "en", "zh", "k0")
        self.model = ScriptedModel()
        self.service = MemoryService(self.store, MemoryConfig(stm_size=1, ltm_top_k=1, summary_step=2), chat=self.model)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def occurrence(self, i, **changes):
        return replace(Occurrence("game", "d1", f"s{i}", i, f"Aurora opens gate {i}.",
                                  metadata={"branch_id": "main", "speaker_id": "hero"}), **changes)

    def commit(self, i, *, occurrence=None, run="r1", target=None, expected=0, used=()):
        occurrence = occurrence or self.occurrence(i)
        self.store.put_source(occurrence)
        target = target or f"曙光打开第{i}道门。"
        check = TranslationCheck(occurrence.occurrence_id, occurrence.source_version, text_hash(target),
                                 text_hash("offline-context"), "fixture-review", True)
        return self.store.commit_translation(occurrence, run, target, check, expected_version=expected, used=used)

    def test_persistence_run_watermark_and_no_future(self):
        first = self.commit(0)
        old_view = self.store.view("r1")
        self.commit(1)
        self.commit(9)  # Out-of-order commit must not expose a future target.
        self.store.close()
        self.store = MemoryStore(self.path)
        service = MemoryService(self.store)
        self.assertEqual([r.ref for r in service.read(self.occurrence(2), old_view).stm], [first.ref])
        self.assertEqual(len(service.read(self.occurrence(2), self.store.view("r1")).stm), 2)
        self.store.start_run("r2", "game", "d1", "en", "zh", "k0")
        self.assertEqual(service.read(self.occurrence(2), self.store.view("r2")).refs, ())
        with self.assertRaises(ValueError):
            service.read(self.occurrence(2, document_id="another"), old_view)
        with self.assertRaises(ValueError):
            service.read(self.occurrence(2, source_lang="zh", target_lang="en"), old_view)

    def test_scope_unknown_and_identical_source_occurrences(self):
        self.commit(0)
        branch = self.occurrence(1, source=self.occurrence(0).source, metadata={"branch_id": "other", "speaker_id": "hero"})
        other = self.commit(1, occurrence=branch)
        bundle = self.service.read(self.occurrence(2), self.store.view("r1"))
        self.assertNotIn(other.ref, bundle.refs)
        self.assertEqual(scope_match(Scope("game", conditions={"speaker_id": ("hero",)}),
                                     self.occurrence(2, metadata={})), "unknown")
        self.assertEqual(scope_match(Scope("game", conditions={"addressee_id": ("B",), "speaker_id": ("villain",)}),
                                     self.occurrence(2)), "no_match")
        self.assertEqual(scope_match(Scope("game", conditions={"speaker_id": "antihero"}),
                                     self.occurrence(2)), "no_match")
        unknown = self.occurrence(3, metadata={"speaker_id": "hero"})
        self.assertEqual(self.service.read(unknown, self.store.view("r1")).refs, ())

    def test_translation_validation_expected_version_and_dependencies(self):
        first = self.commit(0)
        with self.assertRaises(VersionConflict):
            self.commit(0)
        occurrence = self.occurrence(1)
        self.store.put_source(occurrence)
        check = TranslationCheck("s1", 1, text_hash("different"), "pack", "review", True)
        with self.assertRaises(ValueError):
            self.store.commit_translation(occurrence, "r1", "候选", check)
        self.store.start_run("r2", "game", "d1", "en", "zh", "k0")
        with self.assertRaises(ValueError):
            self.commit(1, run="r2", used=(first.ref,))
        check = replace(check, target_hash=text_hash("候选"), passed=False)
        with self.assertRaises(ValueError):
            self.store.commit_translation(occurrence, "r1", "候选", check)

    def test_knowledge_approval_snapshot_and_conflict(self):
        source = self.occurrence(0)
        self.store.put_source(source)
        evidence = (Evidence("s0", 1, "Aurora"),)
        draft = self.store.draft("term", Scope("game"), {"source": "Aurora", "target": "曙光"}, evidence)
        self.store.create_snapshot("draft-only", "game")
        approved = self.store.approve(draft.ref, "human-A")
        self.store.create_snapshot("k1", "game")
        new = self.store.draft("term", Scope("game"), {"source": "Aurora", "target": "极光"}, evidence,
                               record_id=approved.record_id, expected_version=approved.version)
        self.store.approve(new.ref, "human-A")
        self.store.start_run("pinned", "game", "d1", "en", "zh", "k1")
        bundle = self.service.read(self.occurrence(2), self.store.view("pinned"))
        self.assertEqual(bundle.terms[0].content["target"], "曙光")
        self.store.start_run("unapproved", "game", "d1", "en", "zh", "draft-only")
        self.assertEqual(self.service.read(self.occurrence(2), self.store.view("unapproved")).terms, ())
        alt = self.store.draft("term", Scope("game"), {"source": "Aurora", "target": "晨光"}, evidence)
        self.store.approve(alt.ref, "human-A")
        self.store.create_snapshot("conflicting", "game")
        self.store.start_run("conflict", "game", "d1", "en", "zh", "conflicting")
        self.assertEqual(self.service.read(self.occurrence(2), self.store.view("conflict")).conflicts, ("Aurora",))

    def test_summary_includes_boundary_separates_languages_and_merges(self):
        self.commit(0)
        self.assertIsNone(self.service.summarize(self.occurrence(1), self.store.view("r1")))
        second = self.commit(1)
        draft = self.service.summarize(self.occurrence(2), self.store.view("r1"))
        self.assertIn(second.ref, draft.dependencies)
        self.assertEqual(self.service.read(self.occurrence(2), self.store.view("r1")).summaries, ())
        first_summary = self.store.approve(draft.ref, "human-A")
        source_prompt = self.model.calls[0][1]
        target_prompt = self.model.calls[1][1]
        self.assertIn("Aurora opens gate 1.", source_prompt)
        self.assertNotIn("曙光打开", source_prompt)
        self.assertIn("曙光打开第1道门", target_prompt)
        self.assertNotIn("Aurora opens gate", target_prompt)
        self.commit(2)
        self.commit(3)
        merged = self.service.summarize(self.occurrence(4), self.store.view("r1"))
        self.assertIn(first_summary.ref, merged.dependencies)
        self.assertEqual([task for task, _ in self.model.calls[-4:]],
                         ["summarize_source", "merge_source", "summarize_target", "merge_target"])
        self.store.approve(merged.ref, "human-A")
        self.store.invalidate(second.ref, "QA correction")
        self.assertEqual(self.service.read(self.occurrence(4), self.store.view("r1")).summaries, ())

    def test_term_drafts_evidence_and_failure_preserve_tm(self):
        first = self.commit(0)
        drafts = self.service.extract_terms(first.ref)
        self.assertEqual(self.service.read(self.occurrence(1), self.store.view("r1")).terms, ())
        self.store.approve(drafts[0].ref, "human-A")
        self.assertEqual(len(self.service.read(self.occurrence(1), self.store.view("r1")).terms), 1)
        self.assertEqual(self.service.extract_terms(first.ref), ())
        bad = MemoryService(self.store, chat=lambda task, prompt: ModelReply('[{"source":"Fake","target":"虚构"}]', "fixture"))
        with self.assertRaises(ValueError):
            bad.extract_terms(first.ref)
        with self.assertRaises(RuntimeError):
            MemoryService(self.store).summarize(self.occurrence(1), self.store.view("r1"), flush=True)
        self.assertEqual(self.service.read(self.occurrence(1), self.store.view("r1")).stm[0].ref, first.ref)
        with self.assertRaises(ValueError):
            self.store.draft("style", Scope("game", conditions={"speaker_id": ("hero",)}),
                             {"note": "brief"}, (Evidence("s0", 1, "not in source"),))

    def test_ltm_agent_embedding_and_no_stm_duplicates(self):
        records = [self.commit(i) for i in range(4)]
        current = self.occurrence(5)
        view = self.store.view("r1")
        agent = MemoryService(self.store, MemoryConfig(stm_size=1, ltm_top_k=1, retrieval="agent"), chat=self.model)
        bundle = agent.read(current, view)
        self.assertEqual(bundle.ltm[0].ref, records[0].ref)
        self.assertEqual(bundle.stm[0].ref, records[-1].ref)
        invalid = MemoryService(self.store, agent.config, chat=lambda task, prompt: ModelReply('["foreign-id"]', "fixture"))
        with self.assertRaises(ValueError):
            invalid.read(current, view)
        embed = MemoryService(self.store, replace(agent.config, retrieval="embedding"),
                              embedder=lambda text: [1.0, 0.0] if "0." in text or "5." in text else [0.0, 1.0])
        self.assertEqual(embed.read(current, view).ltm[0].ref, records[0].ref)
        self.assertEqual(len(bundle.refs), len(set(bundle.refs)))

    def test_source_update_invalidates_memory_and_blocks_old_commit(self):
        first = self.commit(0)
        summary = self.service.summarize(self.occurrence(1), self.store.view("r1"), flush=True)
        self.store.approve(summary.ref, "human-A")
        self.store.put_source(self.occurrence(0, source_version=2, source="A changed instruction."))
        self.assertFalse(self.store.is_valid(first.ref))
        self.assertEqual(self.service.read(self.occurrence(1), self.store.view("r1")).refs, ())
        with self.assertRaises(VersionConflict):
            self.commit(0, expected=first.version)
        with self.assertRaises(VersionConflict):
            self.store.draft("term", Scope("game"), {"source": "Aurora", "target": "曙光"}, (Evidence("s0", 1, "Aurora"),))

    def test_late_commit_invalidates_incomplete_summary(self):
        self.commit(0)
        self.commit(2)
        draft = self.service.summarize(self.occurrence(3), self.store.view("r1"))
        approved = self.store.approve(draft.ref, "human-A")
        self.commit(1)
        self.assertFalse(self.store.is_valid(approved.ref))
        self.assertEqual(self.service.read(self.occurrence(3), self.store.view("r1")).summaries, ())


if __name__ == "__main__":
    unittest.main()
