"""Fetch pinned Flare text resources only; no game assets or executables."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "artifacts/flare_full_v0.2"
REVS = {"flare-game": "af6eee6d339ac98011864bfe89da837fe7769c28", "flare-engine": "4075191d576a28cbf4f27ec345ee1c7ef1482756"}


def blob_hash(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def main():
    TARGET.mkdir(parents=True,exist_ok=True)
    jobs = []
    for repo, rev in REVS.items():
        tree_file=TARGET / (repo + "-tree.json")
        if not tree_file.exists():
            with urlopen(f"https://api.github.com/repos/flareteam/{repo}/git/trees/{rev}?recursive=1",timeout=45) as response:
                tree_file.write_bytes(response.read())
        tree = json.loads(tree_file.read_text())
        if tree.get('truncated'):
            raise ValueError('Incomplete upstream tree: '+repo)
        for f in tree["tree"]:
            p = f["path"]
            needed = (p.startswith("mods/") and p.endswith(".txt")) or (p.startswith("src/") and p.endswith((".cpp", ".h")))
            needed |= p in ("LICENSE.txt", "COPYING", "README.md")
            needed |= p.endswith(("/data.pot", "/data.zh.po", "/engine.pot", "/engine.zh.po"))
            if f["type"] == "blob" and needed:
                jobs.append((repo, rev, f))
    def fetch(job):
        repo, rev, f = job
        dest = TARGET / "raw" / repo / f["path"]
        url = f"https://raw.githubusercontent.com/flareteam/{repo}/{rev}/{f['path']}"
        data = dest.read_bytes() if dest.exists() else None
        if data is None or blob_hash(data) != f["sha"]:
            for attempt in range(3):
                try:
                    with urlopen(url, timeout=45) as response:
                        data = response.read()
                    if blob_hash(data) != f["sha"]:
                        raise ValueError("Downloaded Git blob hash mismatch: " + url)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(attempt + 1)
        return {"repository": repo, "revision": rev, "path": f["path"], "bytes": len(data), "git_blob_sha": f["sha"], "sha256": hashlib.sha256(data).hexdigest(), "url": url}
    files, failures = [], []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(fetch, job): job for job in jobs}
        for index, future in enumerate(as_completed(futures), 1):
            try:
                files.append(future.result())
            except Exception as error:
                repo, _, f = futures[future]
                failures.append({"repository": repo, "path": f["path"], "error": str(error)})
            if index % 100 == 0:
                print(f"{index}/{len(jobs)} files; failures={len(failures)}", flush=True)
    (TARGET / "download_manifest.json").write_text(json.dumps({"files": sorted(files, key=lambda f:(f["repository"],f["path"])), "failures": failures}, indent=2))
    print(json.dumps({"files": len(files), "bytes": sum(f["bytes"] for f in files), "failures": failures[:5]}), flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
