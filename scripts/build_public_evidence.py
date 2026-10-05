"""Export reviewed experiment aggregates and verify/bundle the public release payload.

Original experiment files stay local and byte-for-byte frozen. CI bundles only
the committed public export; it never needs textbook pages or private requests.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "docs/experiments/v0.6.4"
LOCAL_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|/(?:Users|home|tmp)/")
PRIVATE_KEYS = {"sources", "artifacts", "prior_source_lineage_sha256", "raw_requests", "messages", "response_text", "prompt", "source_text"}
SUMMARY_KEYS = (
    "schema_version", "batch_id", "batch_status", "formal_observed_cells", "formal_planned_cells",
    "formal_eligible_cells", "configuration_eligible", "matrix_complete", "ocr_coverage", "ocr_sha256", "budget",
    "conditions_by_split", "retrieval_by_pool_and_split", "citation_by_pool_condition_split", "annotation_counts",
    "answerability_counts", "retrieval_protocol_exceptions", "human_agreement", "acceptance_definition",
    "raw_prompts_in_report", "cleanup",
)

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def public_value(value):
    if isinstance(value, dict):
        return {k: public_value(v) for k, v in value.items()
                if k not in PRIVATE_KEYS and not LOCAL_PATH.search(k)
                and not k.endswith(("_path", "_directory")) and k != "working_directory"
                and not (k == "path" and isinstance(v, str) and LOCAL_PATH.search(v))}
    if isinstance(value, list): return [public_value(v) for v in value]
    if isinstance(value, str) and LOCAL_PATH.search(value): return "local-only source; see retained SHA-256 lineage"
    return value

def export(source: Path) -> None:
    if not source.is_dir(): raise FileNotFoundError(source)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    for name in ["M3-BKT-RAG-改进实验报告.md", "M3-BKT-RAG-改进实验报告.pdf", "NoMIRACL-复现说明.md"]:
        shutil.copyfile(source / name, PUBLIC / name)
    (PUBLIC / "tools").mkdir(exist_ok=True)
    shutil.copyfile(source / "tools/rag-requirements.lock", PUBLIC / "tools/rag-requirements.lock")
    model_snapshots = json.loads((source / "tools/models/model-snapshots.json").read_text(encoding="utf-8"))
    (PUBLIC / "tools/model-snapshots.json").write_text(
        json.dumps(public_value(model_snapshots), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for directory in ["data", "figures"]:
        (PUBLIC / directory).mkdir(exist_ok=True)
        for path in sorted((source / directory).iterdir()):
            allowed = path.suffix == ".csv" if directory == "data" else path.suffix in {".png", ".svg", ".pdf", ".R"}
            if allowed:
                shutil.copyfile(path, PUBLIC / directory / path.name)
    for path in sorted((source / "data").glob("*.json")):
        if path.name.startswith("assistments-"): continue  # keep fit details in the private reproducibility archive
        value = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "fulltext-rag-summary.json": value = {k: value[k] for k in SUMMARY_KEYS if k in value}
        if path.name == "figure-data-manifest.json":
            value = {"schema_version": "deepprof-public-figure-data-v1", "active_fulltext_batch": value["active_fulltext_batch"],
                     "original_manifest_sha256": digest(path), "note": "Public aggregate files and hashes are listed in ../delivery-manifest.json; private source lineage is retained locally."}
        (PUBLIC / "data" / path.name).write_text(json.dumps(public_value(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for path in (PUBLIC / "figures").glob("*.R"):
        text = path.read_text(encoding="utf-8").replace("docs/experiments/bkt-rag-improvement-20261001", "docs/experiments/v0.6.4")
        path.write_text(text, encoding="utf-8")
    (PUBLIC / "README.md").write_text(
        "# DeepProf v0.6.4 public experiment evidence\n\n"
        "Latest batch: `m3-fulltext-rag-20261004-v4`, 120 questions × four conditions = 480 valid cells. "
        "The 347-page OCR source and 629-chunk index remain private. Retrieval labels: 13,710; citation labels: 1,595; answerability labels: 120. "
        "These are AI annotations; genuine personnel review is pending.\n\n"
        "NoMIRACL Chinese: 3,770 fixed-candidate queries and 37,599 scored pairs; test FAR 4.51%, FRR 70.11%, AUC 0.8007. "
        "This evaluates ranking and evidence acceptance, not whole-corpus recall or generated-answer correctness.\n\n"
        "BKT experiments use ASSISTments student-grouped nested development OOF. Candidate calibration/item parameters remain exploratory; "
        "the runtime defaults are unchanged and no student learning improvement is established.\n\n"
        "The Chinese report, figures, numeric/label aggregate CSVs, plotting sources, frozen dependency lock and model revision/hash metadata are included. Public NoMIRACL query/document IDs are dataset identifiers. "
        "Raw prompts, provider messages, textbook excerpts, private databases, credentials, downloaded models and local absolute paths are excluded. "
        "Original experiment bytes, private lineage and historical failed/stopped batches are retained in the local archive.\n\n"
        "Run `python scripts/build_public_evidence.py --check --bundle` to verify and rebuild the release evidence ZIP. "
        "For figure regeneration, install ggplot2/svglite/ragg/jsonlite and run the four `figures/*.R` scripts from the repository root; "
        "their default input directory is this public export. Historical M1–M3 evidence remains under the existing experiment report links.\n",
        encoding="utf-8")
    files = {}
    for path in sorted(PUBLIC.rglob("*")):
        if not path.is_file() or path.name == "delivery-manifest.json": continue
        if path.suffix in {".json", ".csv", ".md", ".R", ".svg"} and LOCAL_PATH.search(path.read_text(encoding="utf-8-sig")):
            raise ValueError(f"Local path in public payload: {path.relative_to(PUBLIC)}")
        files[path.relative_to(PUBLIC).as_posix()] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    manifest = {"schema_version": "deepprof-public-delivery-v1", "release_version": "0.6.4",
                "source_batch": "m3-fulltext-rag-20261004-v4", "original_local_delivery_manifest_sha256": digest(source / "delivery-manifest.json"),
                "human_review_status": "pending genuine personnel review", "files": files}
    (PUBLIC / "delivery-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def verify_and_bundle(bundle: bool) -> dict:
    manifest = json.loads((PUBLIC / "delivery-manifest.json").read_text(encoding="utf-8"))
    paths = []
    for name, item in manifest["files"].items():
        path = PUBLIC / name
        if not path.is_file() or path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            raise ValueError(f"Public evidence mismatch: {name}")
        paths.append(path)
    result = {"status": "verified", "files": len(paths), "version": manifest["release_version"]}
    if bundle:
        release = ROOT / ".release"; release.mkdir(exist_ok=True)
        target = release / f"DEEPPROF-v{manifest['release_version']}-evidence.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for path in sorted([*paths, PUBLIC / "delivery-manifest.json"]):
                info = zipfile.ZipInfo(path.relative_to(PUBLIC).as_posix(), date_time=(2026, 10, 5, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, path.read_bytes())
        result.update(archive=target.name, sha256=digest(target))
    return result

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path); p.add_argument("--check", action="store_true"); p.add_argument("--bundle", action="store_true")
    a = p.parse_args()
    if a.source: export(a.source)
    if not (a.source or a.check or a.bundle): p.error("choose --source, --check or --bundle")
    print(json.dumps(verify_and_bundle(a.bundle), ensure_ascii=False))

if __name__ == "__main__": main()
