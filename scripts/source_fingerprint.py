"""Stable source-tree fingerprint that excludes generated experiment outputs."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

EXCLUDED_EXACT = {
    "docs/experiments/source-data/experiment-snapshot.json",
    "docs/experiments/source-data/figure-data.csv",
    "docs/experiments/source-data/F09-m3-offline-summary.csv",
    "docs/experiments/source-data/plot-runtime.txt",
    "docs/experiments/source-data/R-session-info.txt",
    "docs/experiments/source-data/figure-validation.json",
    "docs/experiments/\u56fe\u8868\u5e93.md",
    "docs/experiments/\u56fe\u8868\u5e93.docx",
    "docs/M2-offline-acceptance.md",
    "docs/M2-offline-acceptance.json",
    "docs/experiments/source-data/M1-M3-evidence-manifest.json",
    "docs/experiments/source-data/M3-offline-summary-redacted.json",
    "docs/experiments/source-data/v0.6.2-release-validation.json",
    "docs/experiments/source-data/v0.6.2-m2-revalidation-summary.json",
    "docs/experiments/source-data/v0.6.2-m3-offline-summary.json",
    "docs/experiments/source-data/F06-qa.json",
    "docs/experiments/source-data/F09-qa.json",
    "docs/experiments/source-data/F09-collision-audit.json",
    "docs/experiments/source-data/F06-collision-audit.json",
    "docs/experiments/M1-M3-\u5b9e\u9a8c\u62a5\u544a.md",
    "docs/experiments/M1-M3-experimental-report.pdf",
    "docs/experiments/releases/M1-M3-evidence.zip",
    "docs/experiments/releases/M1-M3-SHA256SUMS.txt",
}
EXCLUDED_PREFIXES = ("docs/experiments/figures/", "docs/experiments/m3-offline/")
GIT_EXCLUDES = (
    ":(exclude)docs/experiments/source-data/experiment-snapshot.json",
    ":(exclude)docs/experiments/source-data/figure-data.csv",
    ":(exclude)docs/experiments/source-data/F09-m3-offline-summary.csv",
    ":(exclude)docs/experiments/source-data/plot-runtime.txt",
    ":(exclude)docs/experiments/source-data/R-session-info.txt",
    ":(exclude)docs/experiments/source-data/figure-validation.json",
    ":(exclude)docs/experiments/\u56fe\u8868\u5e93.md",
    ":(exclude)docs/experiments/\u56fe\u8868\u5e93.docx",
    ":(exclude)docs/M2-offline-acceptance.md",
    ":(exclude)docs/M2-offline-acceptance.json",
    ":(exclude)docs/experiments/source-data/M1-M3-evidence-manifest.json",
    ":(exclude)docs/experiments/source-data/M3-offline-summary-redacted.json",
    ":(exclude)docs/experiments/source-data/v0.6.2-release-validation.json",
    ":(exclude)docs/experiments/source-data/v0.6.2-m2-revalidation-summary.json",
    ":(exclude)docs/experiments/source-data/v0.6.2-m3-offline-summary.json",
    ":(exclude)docs/experiments/source-data/F06-qa.json",
    ":(exclude)docs/experiments/source-data/F09-qa.json",
    ":(exclude)docs/experiments/source-data/F09-collision-audit.json",
    ":(exclude)docs/experiments/source-data/F06-collision-audit.json",
    ":(exclude)docs/experiments/M1-M3-\u5b9e\u9a8c\u62a5\u544a.md",
    ":(exclude)docs/experiments/M1-M3-experimental-report.pdf",
    ":(exclude)docs/experiments/releases/M1-M3-evidence.zip",
    ":(exclude)docs/experiments/releases/M1-M3-SHA256SUMS.txt",
    ":(exclude)docs/experiments/figures/**",
    ":(exclude)docs/experiments/m3-offline/**",
)


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    return result.stdout if result.returncode == 0 else b""


def _included(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized not in EXCLUDED_EXACT and not any(normalized.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def repository_fingerprint(root: Path) -> dict[str, object]:
    status_records = _git(root, "status", "--porcelain=v1", "--untracked-files=all", "--no-renames", "-z").split(b"\0")
    included: list[tuple[bytes, str]] = []
    for record in status_records:
        if len(record) < 4:
            continue
        state, raw_path = record[:3], record[3:]
        path = os.fsdecode(raw_path)
        if _included(path):
            included.append((state, path.replace("\\", "/")))

    status_payload = b"\0".join(state + b" " + path.encode("utf-8", errors="surrogateescape") for state, path in included)
    diff_args = ["diff", "HEAD", "--binary", "--no-ext-diff", "--", ".", *GIT_EXCLUDES]
    diff_payload = _git(root, *diff_args)
    source_hash = hashlib.sha256()
    source_hash.update(b"STATUS\0")
    source_hash.update(status_payload)
    source_hash.update(b"\0DIFF\0")
    source_hash.update(diff_payload)

    for state, path in included:
        if state == b"??":
            file_path = root / Path(path)
            if file_path.is_file():
                source_hash.update(b"\0UNTRACKED\0")
                source_hash.update(path.encode("utf-8", errors="surrogateescape"))
                try:
                    with file_path.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            source_hash.update(block)
                except OSError:
                    source_hash.update(b"<unreadable>")

    commit = _git(root, "rev-parse", "HEAD").decode("ascii", errors="replace").strip() or "unavailable"
    return {
        "commit": commit,
        "working_tree_dirty": bool(included),
        "working_tree_status_sha256": hashlib.sha256(status_payload).hexdigest(),
        "working_tree_diff_sha256": source_hash.hexdigest(),
        "fingerprint_excludes": sorted(EXCLUDED_EXACT | {prefix + "*" for prefix in EXCLUDED_PREFIXES}),
    }
