"""Remove case/group linkage from a paired M3 blind-rating packet."""

from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path
from typing import Any


def anonymize_pair(run_dir: Path) -> dict[str, int]:
    scoring_dir = run_dir / "scoring"
    paths = [scoring_dir / "rater-01.json", scoring_dir / "rater-02.json"]
    packets = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    identifiers = [{str(item["blind_id"]) for item in packet.get("items", [])} for packet in packets]
    if identifiers[0] != identifiers[1]:
        raise ValueError("paired_rating_packets_must_have_identical_blind_ids")
    mapping = {identifier: secrets.token_hex(8) for identifier in sorted(identifiers[0])}
    for path, packet in zip(paths, packets):
        packet["case_identifiers_hidden"] = True
        packet["group_labels_hidden"] = True
        for item in packet.get("items", []):
            old_identifier = str(item["blind_id"])
            item["blind_id"] = mapping[old_identifier]
            item.pop("case_id", None)
            item.pop("sample_type", None)
            item.setdefault("citation_evidence_sufficient", None)
        path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"paired_items": len(mapping), "packets": len(paths)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(anonymize_pair(args.run_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
