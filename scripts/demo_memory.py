"""Offline plumbing demo. Scripted model output is NOT experimental evidence."""
import argparse
import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from localization_agent.memory import MemoryConfig, MemoryService, MemoryStore
from localization_agent.models.protocols import ModelReply
from localization_agent.schemas.memory import Evidence, Occurrence, Scope, TranslationCheck, text_hash


def demo_model(task: str, prompt: str) -> ModelReply:
    if task == "extract_terms":
        reply = '[{"source":"Aurora","target":"曙光"}]'
    else:
        reply = f"[离线模拟 {task}] 仅用于验证记忆读写流程。"
    return ModelReply(reply, "offline-demo")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="artifacts/memory-demo.sqlite")
    args = parser.parse_args()
    path = Path(args.db)
    path.parent.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(path)
    try:
        project = "demo-" + uuid4().hex[:12]
        run, snapshot = project + "-run", project + "-knowledge"
        occurrences = [Occurrence(project, "scene-1", f"line-{i}", i, f"Aurora opens gate {i}.",
                                   metadata={"branch_id": "main", "speaker_id": "captain"}) for i in range(5)]
        for occurrence in occurrences:
            store.put_source(occurrence)
        style = store.draft("style", Scope(project, conditions={"speaker_id": ("captain",)}),
                            {"note": "示例台词使用简短陈述句。"},
                            (Evidence("line-0", 1, "Aurora opens gate 0."),))
        store.approve(style.ref, "demo-human")
        store.create_snapshot(snapshot, project)
        store.start_run(run, project, "scene-1", "en", "zh", snapshot)
        config = MemoryConfig(stm_size=2, ltm_top_k=1, summary_step=2)
        service = MemoryService(store, config, chat=demo_model)
        for i, occurrence in enumerate(occurrences[:-1]):
            view = store.view(run)
            target = f"曙光打开第{i}道门。"  # Fabricated demonstration output, not a dataset reference.
            check = TranslationCheck(occurrence.occurrence_id, 1, text_hash(target),
                                     text_hash("demo-empty-pack"), "demo-check", True)
            saved = store.commit_translation(occurrence, run, target, check, view=view)
            if i == 0:
                for draft in service.extract_terms(saved.ref):
                    store.approve(draft.ref, "demo-human")
            summary = service.summarize(occurrences[i+1], store.view(run))
            if summary:
                store.approve(summary.ref, "demo-human")
        bundle = service.read(occurrences[-1], store.view(run))
        print(json.dumps({"mode": "offline-demo; no real LLM evaluation", "database": str(path),
                          "run_id": run, "counts": {key: len(getattr(bundle, key))
                                                    for key in ("terms", "summaries", "stm", "ltm", "styles")},
                          "candidate_refs": [asdict(ref) for ref in bundle.refs]}, ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()
