"""Explicitly seed the built-in, project-original course resource."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from config import paths
from config.settings import Settings
from library.service import ResourceLibrary
from runtime.storage.resource_store import SqliteResourceStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed DeepProf's built-in MVP-4 course")
    parser.add_argument("--path", type=Path, default=Path(__file__).resolve().parents[1] / "data/course_manifest/mini_math.md")
    args = parser.parse_args()
    settings = Settings.from_env()
    paths.ensure_layout()
    store = SqliteResourceStore.open(str(settings.resolved_sqlite_path))
    try:
        library = ResourceLibrary(
            store,
            library_root=settings.resolved_library_dir,
            chunk_size=settings.library_chunk_size,
            chunk_overlap=settings.library_chunk_overlap,
            max_import_bytes=settings.library_max_import_bytes,
        )
        result = library.import_path(
            args.path,
            metadata={
                "course_id": "deepprof.math101",
                "title": "DeepProf 数学基础微课程",
                "type": "textbook",
                "tags": ["limits", "derivatives", "integrals", "linear-algebra", "probability", "optimization"],
                "license": "CC0-1.0 project-original",
                "visibility": "public",
            },
            actor_id="builtin-seed",
            activate=True,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()
