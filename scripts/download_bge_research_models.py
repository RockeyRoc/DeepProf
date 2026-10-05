"""Download version-pinned official BGE-M3 and BGE reranker snapshots."""
from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download

MODELS = [
    ("BAAI/bge-m3", "bge-m3", ["*.json", "*.safetensors", "pytorch_model.bin", "*.pt", "*.model", "tokenizer.json"]),
    ("BAAI/bge-reranker-v2-m3", "bge-reranker-v2-m3", ["*.json", "*.safetensors", "pytorch_model.bin", "*.model", "tokenizer.json"]),
]

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, required=True)
    args = parser.parse_args()
    models_dir = args.models_dir.resolve()
    models_dir.mkdir(parents=True, exist_ok=True)
    api = HfApi()
    manifest = {"schema_version": "deepprof-bge-model-snapshot-v1", "models": []}
    for repo_id, folder, allow_patterns in MODELS:
        info = api.model_info(repo_id, files_metadata=True)
        revision = str(info.sha)
        target = models_dir / f"{folder}-{revision[:12]}"
        snapshot_download(repo_id=repo_id, revision=revision, local_dir=target,
                          allow_patterns=allow_patterns, max_workers=4)
        files = []
        for path in sorted(target.rglob("*")):
            if path.is_file() and ".cache" not in path.parts:
                files.append({"path": str(path.relative_to(target)), "bytes": path.stat().st_size,
                              "sha256": sha(path)})
        manifest["models"].append({"repo_id": repo_id, "revision": revision,
            "revision_resolved_from": "Hugging Face HfApi.model_info", "license": getattr(info.card_data, "license", None),
            "local_path": str(target), "allow_patterns": allow_patterns, "files": files})
        print(json.dumps(manifest["models"][-1], ensure_ascii=False))
    output = models_dir / "model-snapshots.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())