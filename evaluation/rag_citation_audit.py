"""Build a traceable, local-only claim/citation audit from a frozen M3 run."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


LABELS = ("完整支持", "部分支持", "无支持", "矛盾", "无法判定")
_CITATION_RE = re.compile(r"(?:片段|证据片段|材料|来源)\s*[-#：:]?\s*(\d+)")
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])\s*|[\r\n]+")
_MARKDOWN_PREFIX_RE = re.compile(r"^\s*(?:#{1,6}\s*|[-*+]\s+|\d+[.)、]\s*)")
_QUESTION_TAILS = ("吗", "呢", "为何", "为什么", "如何", "怎样")
_STOPWORDS = set("这个那个我们你们他们因此所以如果因为而且但是以及同时其中对于关于可以能够需要进行一个一种这是不是是否的了和与或在中上对把被也都则其及等" )


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dedupe_refs(refs: Iterable[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int | None]] = set()
    for raw in refs:
        if not isinstance(raw, dict):
            continue
        document_id = str(raw.get("document_id") or "")
        chunk_id = str(raw.get("chunk_id") or "")
        page = raw.get("page")
        try:
            page = int(page) if page is not None else None
        except (ValueError, TypeError):
            page = None
        key = (document_id, chunk_id, page)
        if key in seen:
            continue
        seen.add(key)
        output.append({**raw, "document_id": document_id, "chunk_id": chunk_id, "page": page})
    return output


def _claims(text: str) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for paragraph in re.split(r"\n\s*\n", text or ""):
        paragraph_claims: list[dict[str, Any]] = []
        trailing_selectors: list[int] = []
        for clause in _CLAUSE_SPLIT_RE.split(paragraph):
            selectors = [int(value) for value in _CITATION_RE.findall(clause)]
            without_citation = _CITATION_RE.sub("", clause)
            # Sentence splitting leaves terminal punctuation attached to the
            # selector-only line (for example, ``→ 对应片段2。``). Remove
            # punctuation before matching that line, otherwise ``对应。`` is
            # mistaken for a factual claim and the citation becomes orphaned.
            citation_residual = without_citation.strip().strip(" \t→->:：，,。；;.!?！？（）()[]【】")
            citation_residual = re.sub(r"^(?:→|->)?\s*对应\s*$", "", citation_residual).strip(
                " \t→->:：，,。；;.!?！？（）()[]【】")
            if selectors and not citation_residual:
                trailing_selectors.extend(selectors)
                continue
            without_citation = citation_residual if selectors else without_citation
            cleaned = _MARKDOWN_PREFIX_RE.sub("", without_citation).strip()
            if not cleaned:
                continue
            cleaned = re.sub(r"\*{1,3}|`{1,3}|_{1,3}", "", cleaned).strip()
            # Section labels and learner-facing prompts are not factual claims.
            cleaned = re.sub(r"^(?:结论性概述|结论先说|分步展开|分层展开)\s*[:：]?\s*", "", cleaned)
            cleaned = re.sub(r"^第\s*[0-9一二三四五六七八九十]+\s*步\s*[:：]\s*", "", cleaned)
            cleaned = re.sub(r"^(?:关键条件(?:/易错点)?|易错点|自检问题)\s*[:：]\s*", "", cleaned)
            cleaned = re.sub(r"^(?:数组与线性表的关系|顺序表与数组的关系)\s*[:：]\s*", "", cleaned)
            if not cleaned or cleaned.startswith("来源："):
                continue
            if cleaned.startswith(("请", "你能", "合上材料", "如果有人说", "仅凭上述", "若有人说")):
                continue
            if re.fullmatch(r"(?:先看|先抓住|抓住|理解|结合).{0,24}", cleaned) and not re.search(
                    r"(?:是|为|有|存在|指出|说明|定义|称|导致|需要|能够|可以|应当|必须|不能)", cleaned):
                continue
            if cleaned.endswith(("：", ":", "指出", "说明", "只定义了", "例如", "包括")):
                continue
            if cleaned.endswith(("?", "？")) or any(cleaned.endswith(tail) for tail in _QUESTION_TAILS):
                continue
            # Treat prose as a fact candidate unless it is a short heading or question.
            if len(cleaned) < 8 or not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", cleaned):
                continue
            row = {"text": cleaned, "citation_selectors": selectors}
            paragraph_claims.append(row)
        # The common answer format writes a final “→ 对应片段N” line. Associate
        # it with factual clauses in that paragraph, while keeping the link auditable.
        if trailing_selectors and paragraph_claims:
            for row in paragraph_claims:
                row["citation_selectors"] = list(dict.fromkeys(
                    [*row["citation_selectors"], *trailing_selectors]))
        claims.extend(paragraph_claims)
    return claims


def _tokens(text: str) -> set[str]:
    normalized = re.sub(r"\s+", "", str(text or "")).lower()
    tokens = set(re.findall(r"[a-z][a-z0-9_-]{1,}|\d+(?:\.\d+)?", normalized))
    cjk = re.findall(r"[\u4e00-\u9fff]+", normalized)
    for run in cjk:
        tokens.update(run[index:index + 2] for index in range(len(run) - 1))
        tokens.update(char for char in run if char not in _STOPWORDS and len(run) < 4)
    return tokens


def _minimal_excerpt(claim: str, source: str, *, limit: int = 1200) -> tuple[str, str]:
    paragraphs = [part.strip() for part in re.split(r"(?<=[。！？!?；;])\s*|\r?\n+", source) if part.strip()]
    if not paragraphs:
        return "", "source_chunk_empty"
    claim_terms = _tokens(claim)
    scores = [(index, len(claim_terms & _tokens(sentence))) for index, sentence in enumerate(paragraphs)]
    best = max((score for _, score in scores), default=0)
    if best == 0:
        selected_indices = [0]
    else:
        # Keep the strongest matching sentence(s), then add one adjacent sentence
        # where needed to retain definitions and their conditions. This is an
        # evidence excerpt, never a support decision.
        selected_indices = [index for index, score in scores if score >= max(2, best * 0.55)]
        strongest = max(scores, key=lambda item: (item[1], -item[0]))[0]
        selected_indices.extend(index for index in (strongest - 1, strongest + 1)
                                if 0 <= index < len(paragraphs))
        selected_indices = sorted(set(selected_indices))
    selected: list[str] = []
    total = 0
    for index in selected_indices:
        sentence = paragraphs[index]
        extra = len(sentence) + (1 if selected else 0)
        if total + extra > limit:
            if not selected:
                selected.append(sentence[:limit].rstrip() + "…")
            break
        selected.append(sentence)
        total += extra
    return "\n".join(selected), "lexical_sentence_window; source text retained verbatim; not a semantic verdict"


def apply_ai_judgments(detailed_jsonl: Path, summary_json: Path, review_csv: Path,
                       judgments_jsonl: Path) -> dict[str, Any]:
    """Apply a complete source-grounded AI review without altering raw run evidence."""
    judgments: dict[tuple[str, str, int, str], dict[str, str]] = {}
    for line in judgments_jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        key = (str(item["cell_id"]), str(item["claim_text"]), int(item["citation_selector"]),
               str(item["chunk_id"]))
        if key in judgments:
            raise ValueError("duplicate_ai_citation_judgment")
        if item.get("judgment_status") not in LABELS or item.get("claim_status") not in LABELS:
            raise ValueError("invalid_ai_citation_judgment_label")
        if not str(item.get("judgment_reason") or "").strip() or not str(item.get("claim_reason") or "").strip():
            raise ValueError("missing_ai_citation_judgment_rationale")
        judgments[key] = item

    rows = [json.loads(line) for line in detailed_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    seen: set[tuple[str, str, int, str]] = set()
    pair_counts: Counter[str] = Counter()
    claim_labels: dict[tuple[str, str], str] = {}
    for row in rows:
        if row.get("record_type") != "claim_citation_pair":
            continue
        locator = row.get("locator") or {}
        key = (str(row["cell_id"]), str(row["claim_text"]), int(row["citation_selector"]),
               str(locator.get("chunk_id") or ""))
        item = judgments.get(key)
        if item is None:
            raise ValueError(f"missing_ai_citation_judgment:{row['cell_id']}:{row['citation_selector']}")
        seen.add(key)
        row["judgment_status"] = item["judgment_status"]
        row["judgment_reason"] = item["judgment_reason"]
        row["review_source"] = "current Codex AI; source-grounded semantic review; uncalibrated; no human score claimed"
        pair_counts[row["judgment_status"]] += 1
        claim_key = (str(row["cell_id"]), str(row["claim_id"]))
        prior = claim_labels.get(claim_key)
        if prior is not None and prior != item["claim_status"]:
            raise ValueError("inconsistent_ai_claim_judgment_for_multi_citation_claim")
        claim_labels[claim_key] = item["claim_status"]
        row["claim_judgment_reason"] = item["claim_reason"]
    if seen != set(judgments):
        raise ValueError(f"unused_ai_citation_judgments:{len(set(judgments) - seen)}")

    claim_counts: Counter[str] = Counter()
    claim_total = 0
    unreferenced = 0
    for row in rows:
        if row.get("record_type") == "claim_without_explicit_citation":
            row["judgment_status"] = "无支持"
            row["judgment_reason"] = "回答没有可映射的显式引用；按引用证据覆盖口径计为无支持，不评价该事实在外部是否正确。"
            row["review_source"] = "deterministic citation-presence check; no cited evidence to assess"
            claim_counts["无支持"] += 1
            claim_total += 1
            unreferenced += 1
        elif row.get("record_type") == "claim_citation_pair":
            claim_key = (str(row["cell_id"]), str(row["claim_id"]))
            row["claim_judgment_status"] = claim_labels[claim_key]
    claim_total += len(claim_labels)
    claim_counts.update(claim_labels.values())

    with detailed_jsonl.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = _read(summary_json)
    pair_total = sum(pair_counts.values())
    pair_determinate = pair_total - pair_counts["无法判定"]
    summary["semantic_support_rate"] = {
        "numerator": pair_counts["完整支持"], "denominator": pair_total,
        "rate": pair_counts["完整支持"] / pair_total if pair_total else None,
        "judgment_counts": {label: pair_counts[label] for label in LABELS},
        "semantically_judged_pairs": pair_determinate,
        "determinate_pair_coverage": pair_determinate / pair_total if pair_total else None,
        "unreferenced_claims_counted_as_pairs": False,
        "status": "AI-assisted, source-grounded, uncalibrated; complete support only is numerator",
    }
    summary["claim_evidence_coverage"] = {
        "fully_supported_claims": claim_counts["完整支持"], "factual_claim_candidates": claim_total,
        "rate": claim_counts["完整支持"] / claim_total if claim_total else None,
        "judgment_counts": {label: claim_counts[label] for label in LABELS},
        "unreferenced_factual_claim_candidates": unreferenced,
        "determinate_claim_coverage": (claim_total - claim_counts["无法判定"]) / claim_total if claim_total else None,
        "status": "AI-reviewed cited claims plus deterministic no-citation candidates; candidate extraction may include residual non-claims",
    }
    summary["review_protocol"]["review_source"] = (
        "current Codex AI; source-grounded semantic review of all mapped claim-citation pairs; "
        "uncalibrated; no human score claimed"
    )
    summary["review_protocol"]["AI_review_status"] = "all mapped claim-citation pairs reviewed; no ARES calibration"
    summary["review_protocol"]["AI_judgments_sha256"] = _sha256(judgments_jsonl)
    summary["claim_evidence_coverage"]["claim_support_definition"] = (
        "Full only when the cited evidence, individually or jointly, covers the claim's material content and conditions; "
        "claims without an answer-visible citation count as unsupported evidence coverage."
    )
    summary["raw_audit_jsonl_sha256"] = _sha256(detailed_jsonl)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    columns = ["record_type", "cell_id", "group", "case_id", "claim_id", "claim_text", "citation_selector",
               "locator", "source_file", "source_title", "source_page", "printed_page", "chapter", "section",
               "source_excerpt", "locator_status", "judgment_status", "judgment_reason",
               "claim_judgment_status", "claim_judgment_reason", "review_source",
               "reviewer_1_status", "reviewer_2_status", "adjudication_status"]
    with review_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(row for row in rows if row.get("record_type") != "cell_trace")
    return summary


def _index_source(connection: sqlite3.Connection, ref: dict[str, Any]) -> dict[str, Any]:
    row = connection.execute(
        "SELECT c.document_id,c.chunk_id,c.page,c.printed_page,c.chapter,c.section,c.reliable,c.text,"
        "d.filename,r.title,r.source_url,r.resource_id "
        "FROM library_chunks c JOIN library_documents d ON d.document_id=c.document_id "
        "JOIN library_resources r ON r.resource_id=c.resource_id WHERE c.chunk_id=?",
        (ref["chunk_id"],),
    ).fetchone()
    if row is None:
        return {"locatable": False, "reason": "chunk_id_not_in_frozen_index", "source_text": ""}
    (document_id, chunk_id, page, printed_page, chapter, section, reliable, text,
     filename, title, source_url, resource_id) = row
    exact = (str(document_id) == ref["document_id"] and int(page) == ref["page"] and bool(reliable))
    return {"locatable": exact, "reason": "exact_document_chunk_page_match" if exact else "locator_mismatch_or_unreliable_chunk",
            "source_text": str(text or ""), "document_id": str(document_id), "chunk_id": str(chunk_id),
            "page": int(page), "printed_page": printed_page, "chapter": str(chapter or ""),
            "section": str(section or ""), "filename": str(filename or ""), "title": str(title or ""),
            "source_url": str(source_url or ""), "resource_id": str(resource_id or "")}


def _cell_trace(cell: dict[str, Any], events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    attached: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event.get("type") == "pedagogy.decision":
            candidates.extend(payload.get("evidence_refs") or [])
        elif event.get("type") == "teaching.turn.completed":
            attached.extend(payload.get("evidence_refs") or [])
    return _dedupe_refs(candidates), _dedupe_refs(attached)


def audit_run(run_dir: Path, database_path: Path, detailed_jsonl: Path,
              summary_json: Path, review_csv: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    manifest = _read(run_dir / "manifest.json")
    if not database_path.is_file():
        raise FileNotFoundError("frozen_library_database_missing")
    connection = sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    detailed_jsonl.parent.mkdir(parents=True, exist_ok=True)
    review_csv.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)

    labels: Counter[str] = Counter()
    pair_labels: Counter[str] = Counter()
    group_counts: Counter[str] = Counter()
    field_refs = field_locatable = source_actual_total = source_actual_locatable = 0
    generated_cells = no_generation_cells = truncated_cells = no_claim_cells = no_citation_cells = 0
    attached_ref_rows = actual_ref_rows = orphan_citations = unmatched_citations = 0
    claims_total = unreferenced_claims = semantic_judged = 0
    semantic_pair_count = 0
    cell_ids: set[str] = set()
    review_rows: list[dict[str, Any]] = []

    try:
        with detailed_jsonl.open("w", encoding="utf-8", newline="\n") as detail:
            cells = sorted((run_dir / "cells").glob("*.json"), key=lambda path: path.name)
            for cell_path in cells:
                cell = _read(cell_path)
                cell_id = str(cell.get("cell_id") or cell_path.stem)
                if cell_id in cell_ids:
                    raise ValueError("duplicate_cell_id_in_citation_audit")
                cell_ids.add(cell_id)
                events_path = run_dir / "events" / f"{cell_id}.json"
                event_blob = _read(events_path) if events_path.is_file() else {"events": []}
                events = event_blob.get("events") if isinstance(event_blob.get("events"), list) else []
                candidates, attached = _cell_trace(cell, events)
                field_refs += int(cell.get("reference_count") or 0)
                field_locatable += int(cell.get("locatable_references") or 0)
                attached_ref_rows += len(attached)
                group = str(cell.get("group") or "")
                status = str(cell.get("evaluation_status") or "unknown")
                group_counts[f"{group}:{status}"] += 1
                answer = str(cell.get("response_text") or "")
                citations = [int(match.group(1)) for match in _CITATION_RE.finditer(answer)] if status == "completed" else []
                if status == "completed":
                    generated_cells += 1
                elif status == "not_applicable":
                    no_generation_cells += 1
                elif status == "failed_truncated":
                    truncated_cells += 1

                mapped: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
                for selector in citations:
                    if 1 <= selector <= len(attached):
                        ref = attached[selector - 1]
                        source = _index_source(connection, ref)
                        mapped.append((selector, ref, source))
                        source_actual_total += 1
                        source_actual_locatable += int(source["locatable"])
                        actual_ref_rows += 1
                    else:
                        unmatched_citations += 1

                claims = _claims(answer) if status == "completed" else []
                if status == "completed" and not claims:
                    no_claim_cells += 1
                if status == "completed" and not citations:
                    no_citation_cells += 1
                cell_claim_rows = 0
                used_selectors: set[int] = set()
                for claim_index, claim in enumerate(claims, 1):
                    claims_total += 1
                    claim_selectors = claim["citation_selectors"]
                    if not claim_selectors:
                        unreferenced_claims += 1
                        label = "无支持"
                        row = {"record_type": "claim_without_explicit_citation", "cell_id": cell_id,
                               "group": group, "case_id": cell.get("case_id"), "claim_id": f"{cell_id}-claim-{claim_index}",
                               "claim_text": claim["text"], "citation_selector": None, "locator": None,
                               "source_excerpt": "", "locator_status": "no_answer_citation",
                               "judgment_status": label, "judgment_reason": "该事实性回答片段未出现可映射的显式教材引用。",
                               "review_source": "deterministic structure extraction; no semantic model invoked",
                               "reviewer_1_status": "", "reviewer_2_status": "", "adjudication_status": ""}
                        detail.write(json.dumps(row, ensure_ascii=False) + "\n")
                        review_rows.append(row)
                        labels[label] += 1
                        cell_claim_rows += 1
                        continue
                    for selector in claim_selectors:
                        used_selectors.add(selector)
                        matching = next((item for item in mapped if item[0] == selector), None)
                        if matching is None:
                            ref = attached[selector - 1] if 1 <= selector <= len(attached) else None
                            source = _index_source(connection, ref) if ref else {"locatable": False,
                                "reason": "answer_selector_has_no_attached_reference", "source_text": ""}
                            label = "无法判定"
                            reason = "回答引用标号无法与该格附带引用可靠对应，需独立复核。"
                            locator = {key: ref.get(key) for key in ("document_id", "chunk_id", "page")} if ref else None
                            excerpt = ""
                        else:
                            _, ref, source = matching
                            locator = {key: ref.get(key) for key in ("document_id", "chunk_id", "page")}
                            excerpt, excerpt_reason = _minimal_excerpt(claim["text"], source.get("source_text", ""))
                            if source["locatable"]:
                                label = "无法判定"
                                reason = ("来源位置已核实；语义支持判断未由校准过的 NLI/独立评审完成。"
                                          f" 摘录规则：{excerpt_reason}。")
                            else:
                                label = "无法判定"
                                reason = f"来源不可精确定位：{source['reason']}。"
                        labels[label] += 1
                        pair_labels[label] += 1
                        semantic_pair_count += 1
                        row = {"record_type": "claim_citation_pair", "cell_id": cell_id,
                               "group": group, "case_id": cell.get("case_id"), "claim_id": f"{cell_id}-claim-{claim_index}",
                               "claim_text": claim["text"], "citation_selector": selector,
                               "locator": locator, "source_file": source.get("filename", ""),
                               "source_title": source.get("title", ""), "source_page": source.get("page"),
                               "printed_page": source.get("printed_page"), "chapter": source.get("chapter", ""),
                               "section": source.get("section", ""), "source_excerpt": excerpt,
                               "locator_status": source["reason"], "judgment_status": label,
                               "judgment_reason": reason,
                               "review_source": "deterministic structure extraction; no semantic model invoked",
                               "reviewer_1_status": "", "reviewer_2_status": "", "adjudication_status": ""}
                        detail.write(json.dumps(row, ensure_ascii=False) + "\n")
                        review_rows.append(row)
                        cell_claim_rows += 1
                if claims and not cell_claim_rows:
                    no_claim_cells += 1
                for selector, ref, source in mapped:
                    if selector not in used_selectors:
                        orphan_citations += 1
                        row = {"record_type": "answer_citation_without_extracted_claim", "cell_id": cell_id,
                               "group": group, "case_id": cell.get("case_id"), "claim_id": None,
                               "claim_text": "", "citation_selector": selector,
                               "locator": {key: ref.get(key) for key in ("document_id", "chunk_id", "page")},
                               "source_file": source.get("filename", ""), "source_title": source.get("title", ""),
                               "source_page": source.get("page"), "printed_page": source.get("printed_page"),
                               "chapter": source.get("chapter", ""), "section": source.get("section", ""),
                               "source_excerpt": "", "locator_status": source["reason"],
                               "judgment_status": "无法判定", "judgment_reason": "此引用标号未映射到抽取出的事实性主张；需复核是否属于多余引用。",
                               "review_source": "deterministic structure extraction; no semantic model invoked",
                               "reviewer_1_status": "", "reviewer_2_status": "", "adjudication_status": ""}
                        detail.write(json.dumps(row, ensure_ascii=False) + "\n")
                        review_rows.append(row)
                        labels["无法判定"] += 1
                # Emit retrieval and decision references separately, without treating them as answer citations.
                detail.write(json.dumps({"record_type": "cell_trace", "cell_id": cell_id,
                    "group": group, "case_id": cell.get("case_id"), "evaluation_status": status,
                    "retrieval_candidate_refs": candidates, "decision_attached_refs": attached,
                    "answer_citation_selectors": citations, "generated_answer": status == "completed",
                    "model_calls": int(cell.get("model_calls") or 0)}, ensure_ascii=False) + "\n")
    finally:
        connection.close()

    total_cells = len(cell_ids)
    parse_rate = field_locatable / field_refs if field_refs else None
    summary = {
        "schema_version": "deepprof-m3-citation-audit-summary-v1",
        "run_id": str(manifest.get("run_id") or ""),
        "run_manifest_sha256": _sha256(run_dir / "manifest.json"),
        "frozen_database_sha256": _sha256(database_path),
        "cells": total_cells,
        "groups": dict(sorted(group_counts.items())),
        "field_parse_rate": {"numerator": field_locatable, "denominator": field_refs,
                              "rate": parse_rate, "status": "locator-format-only; not semantic support"},
        "reference_flow": {"decision_attached_references": attached_ref_rows,
                           "answer_citation_occurrences": actual_ref_rows,
                           "unmatched_answer_citation_occurrences": unmatched_citations,
                           "citations_without_extracted_claim": orphan_citations,
                           "retrieval_candidates_are_not_counted_as_answer_citations": True},
        "source_actual_locatable_rate": {"numerator": source_actual_locatable,
             "denominator": source_actual_total,
             "rate": source_actual_locatable / source_actual_total if source_actual_total else None,
             "status": "computed against the frozen document/chunk/page index" if source_actual_total else "N/A"},
        "semantic_support_rate": {"numerator": None, "denominator": semantic_pair_count, "rate": None,
             "status": "pending_independent_semantic_review; unresolved pairs do not count as support",
             "judgment_counts": {label: pair_labels[label] for label in LABELS},
             "unreferenced_claims_counted_as_pairs": False,
             "semantically_judged_pairs": semantic_judged},
        "claim_evidence_coverage": {"fully_supported_claims": None, "factual_claims": claims_total,
             "rate": None, "unreferenced_factual_claims": unreferenced_claims,
             "status": "pending_independent_semantic_review"},
        "cell_outcomes": {"generated_complete": generated_cells, "not_generated": no_generation_cells,
                          "truncated": truncated_cells, "completed_without_extracted_fact_claims": no_claim_cells,
                          "completed_without_explicit_citations": no_citation_cells},
        "review_protocol": {"judgment_labels": list(LABELS),
             "review_source": "deterministic structure extraction; no semantic model invoked",
             "ARES_alignment": "dimensions adapted as support/completeness/contradiction checks; no ARES human calibration or human score claimed",
             "independent_review_fields": ["reviewer_1_status", "reviewer_2_status", "adjudication_status"],
             "human_blind_rating_status": "pending; existing two blind rating packets untouched",
             "locator_and_excerpt_extraction_are_not_semantic_support": True},
        "raw_audit_jsonl_sha256": _sha256(detailed_jsonl),
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    columns = ["record_type", "cell_id", "group", "case_id", "claim_id", "claim_text", "citation_selector",
               "locator", "source_file", "source_title", "source_page", "printed_page", "chapter", "section",
               "source_excerpt", "locator_status", "judgment_status", "judgment_reason",
               "claim_judgment_status", "claim_judgment_reason", "review_source",
               "reviewer_1_status", "reviewer_2_status", "adjudication_status"]
    with review_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(review_rows)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--detailed-jsonl", type=Path, required=True,
                        help="local-only output containing answer claims and source excerpts")
    parser.add_argument("--summary", type=Path, required=True,
                        help="repository-safe aggregate summary")
    parser.add_argument("--review-csv", type=Path, required=True,
                        help="local-only reviewer worksheet with independent judgement columns")
    parser.add_argument("--judgments", type=Path, default=None,
                        help="local-only JSONL of source-grounded AI pair and claim judgments")
    args = parser.parse_args()
    summary = audit_run(args.run_dir, args.database, args.detailed_jsonl, args.summary, args.review_csv)
    if args.judgments:
        summary = apply_ai_judgments(args.detailed_jsonl, args.summary, args.review_csv, args.judgments)
    print(json.dumps({"run_id": summary["run_id"], "cells": summary["cells"],
        "field_parse_rate": summary["field_parse_rate"],
        "source_actual_locatable_rate": summary["source_actual_locatable_rate"],
        "semantic_support_rate": summary["semantic_support_rate"],
        "raw_audit_jsonl_sha256": summary["raw_audit_jsonl_sha256"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
