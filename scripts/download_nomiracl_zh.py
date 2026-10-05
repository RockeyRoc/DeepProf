"""Fetch the official NoMIRACL Chinese fixed-candidate benchmark snapshot."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    repo = "miracl/nomiracl"
    info = HfApi().dataset_info(repo, files_metadata=True)
    revision = str(info.sha)
    path = root / f"nomiracl-zh-{revision[:12]}"
    snapshot_download(repo_id=repo, repo_type="dataset", revision=revision, local_dir=path,
        allow_patterns=["README.md", "data/chinese/**"], max_workers=4)
    files = []
    for item in sorted(path.rglob("*")):
        if item.is_file() and ".cache" not in item.parts:
            files.append({"path": str(item.relative_to(path)), "bytes": item.stat().st_size, "sha256": sha(item)})
    manifest = {"dataset_id": repo, "revision": revision, "revision_resolved_from": "HfApi.dataset_info",
        "language": "Chinese", "split": "official dev/test; relevant + non_relevant",
        "license": "Apache-2.0 per official dataset card", "local_path": str(path),
        "files": files, "status": "downloaded; candidate-only assessment, not corpus-wide retrieval"}
    (root / "nomiracl-zh-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())