"""Build teacher-review candidates without copying source textbook content."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
COURSE = PROJECT / "data" / "courses" / "data_structures_c"
DEFAULT_AUDIT = PROJECT.parent / "开发者测试.deepprof" / "course" / "textbook-page-audit.json"
OUTPUT = PROJECT / "docs" / "review-pack"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_question_drafts(output: Path) -> dict[str, Any]:
    coverage = _read(COURSE / "question_coverage.json")
    objectives = _read(COURSE / "learning_objectives.json")["objectives"]
    manifest = _read(COURSE / "manifest.json")
    concepts = {str(row["concept_id"]): row for row in manifest.get("concepts", [])}
    uncovered = [row for row in coverage["coverage"] if row.get("coverage_status") == "uncovered"]
    drafts = []
    for row in uncovered:
        concept_id = str(row["concept_id"])
        objective = str(objectives.get(concept_id) or "").strip()
        if not objective:
            raise ValueError(f"missing_learning_objective:{concept_id}")
        concept = concepts.get(concept_id, {})
        drafts.append({
            "draft_id": f"{concept_id}-REVIEW-DRAFT-01",
            "course_id": coverage["course_id"], "concept_id": concept_id,
            "concept_name": str(concept.get("name") or concept_id),
            "learning_objective": objective,
            "question_draft": f"请用自己的话完成以下学习目标，并通过一个具体步骤、演算或反例说明理解：{objective}",
            "answer_sketch": f"预期答案要点应覆盖：{objective}。教师需补入本课程适用的具体演算、数据或 C 语言过程，并核验边界条件。",
            "scoring_rubric_draft": {
                "scale": "0-2; open response; never auto-grade before review",
                "0": "回答缺失、答非所问，或包含关键概念/步骤错误。",
                "1": "覆盖部分目标要点，但缺少关键关系、步骤、前提或正确性解释。",
                "2": "准确覆盖全部目标要点，并给出可检查的过程、理由或边界说明。",
                "teacher_completion_required": ["核对教材内容", "加入知识点专属判分锚点", "确认常见等价答案", "填写出处页码"],
            },
            "source_ref_candidates": concept.get("source_refs", []),
            "source_status": "course_reference_requires_manual_verification",
            "deterministic_auto_grading": False,
            "teacher_approval": "pending",
            "availability": "draft_not_enabled",
        })
    payload = {"schema_version": "deepprof-question-review-drafts-v1", "course_id": coverage["course_id"],
               "bank_version": coverage["bank_version"], "draft_count": len(drafts),
               "all_drafts_unapproved": True, "items": drafts}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def build_page_candidates(audit_path: Path, output: Path) -> dict[str, Any]:
    audit = _read(audit_path)
    by_page = {int(row["pdf_page"]): row for row in audit.get("pages", [])}
    page_count = int(audit["source"]["pdf_pages"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["pdf_page", "printed_page_candidate", "candidate_method",
            "chapter_candidate_available", "extracted_character_count", "status", "manual_verified"])
        writer.writeheader()
        counts: Counter[str] = Counter()
        for pdf_page in range(1, page_count + 1):
            prior = by_page.get(pdf_page, {})
            status = str(prior.get("status") or "pending_manual_visual_check")
            counts[status] += 1
            writer.writerow({"pdf_page": pdf_page,
                "printed_page_candidate": prior.get("printed_page_candidate"),
                "candidate_method": str(prior.get("printed_page_candidate_method") or "not_available"),
                "chapter_candidate_available": bool(prior.get("chapter_heading_candidate")),
                "extracted_character_count": int(prior.get("extracted_character_count") or 0),
                "status": status,
                "manual_verified": status == "manually_verified"})
    return {"pdf_pages": page_count, "source_sha256": str(audit["source"]["sha256"]),
            "status_counts": dict(sorted(counts.items())),
            "candidate_file": output.name,
            "privacy": "page-level numbers and status only; no textbook passages, page images, or filenames exported"}


def build_pack(audit_path: Path = DEFAULT_AUDIT, output_dir: Path = OUTPUT) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    questions = build_question_drafts(output_dir / "question-drafts.json")
    pages = build_page_candidates(audit_path, output_dir / "textbook-page-candidates.csv")
    readme = f"""# 课程人工审核包

题目草案：{questions['draft_count']} 项，覆盖当前未覆盖知识点。每题含目标、开放题题干草案、参考答案要点和 0–2 分评分锚点草案。全部禁用自动评分并保持 `teacher_approval=pending`；教师补写课程专属例题与出处后再审核入库。

教材定位：{pages['pdf_pages']} 页；当前审核台账：{json.dumps(pages['status_counts'], ensure_ascii=False)}。逐页表只含 PDF 页号、已有书内页码候选、候选来源和审核状态，不含教材正文、图片或原始文件名。逐页预览仍由本机的 `scripts/render_review_pages.py` 按教师选定页码生成。

来源文件 SHA-256：`{pages['source_sha256']}`。人工核对须看本机教材页图；不得按页码偏移批量确认。教师确认记录至少填写：PDF 页、书内页、章节标题、视觉证据及审核人/日期。

M3 双盲评使用各运行目录下 `scoring/rater-01.json` 和 `rater-02.json`。评分字段及导入命令见 [评分指引](rating-guidelines.md)。公开汇总前移除逐字回答、身份与链接映射。

本包是待审核材料，不代表题目、答案、评分规则或教材映射已经通过教师审核。
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")
    return {"question_drafts": questions["draft_count"], "textbook_pages": pages["pdf_pages"],
            "textbook_status_counts": pages["status_counts"], "output_dir": str(output_dir)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--textbook-audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(json.dumps(build_pack(args.textbook_audit, args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
