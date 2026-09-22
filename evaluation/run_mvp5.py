"""Generate an MVP-5 report from a Runtime event JSON export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.metrics import aggregate_events, load_pricing, write_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("events", type=Path, help="JSON array of RuntimeEvent objects")
    parser.add_argument("--pricing", type=Path)
    parser.add_argument("--output", type=Path, default=Path("evaluation/output"))
    args = parser.parse_args()
    events = json.loads(args.events.read_text(encoding="utf-8"))
    if not isinstance(events, list):
        raise SystemExit("events file must contain a JSON array")
    result = aggregate_events(events, load_pricing(args.pricing))
    write_report(result, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
