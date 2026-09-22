"""MVP-3 的轻量结构守卫；不依赖 Node/npm，先锁住安全边界与目录接缝。"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mvp3_directories_and_entrypoints_exist() -> None:
    expected = (
        "apps/desktop/main/app.ts",
        "apps/desktop/main/runtime_supervisor.ts",
        "apps/desktop/main/backend_broker.ts",
        "apps/desktop/preload/index.ts",
        "apps/desktop/renderer/index.html",
        "apps/pet/director/pet_director.ts",
        "apps/pet/package/pet.json",
        "apps/pet/package/spritesheet.webp",
        "packages/client_sdk/event_client.ts",
        "packages/design_system/styles.css",
    )
    assert all((ROOT / item).is_file() for item in expected)


def test_pet_manifest_matches_v1_contract() -> None:
    manifest = json.loads((ROOT / "apps/pet/package/pet.json").read_text(encoding="utf-8"))
    assert manifest["spritesheetPath"] == "spritesheet.webp"
    assert manifest["atlas"] == {"columns": 8, "rows": 9, "width": 1536, "height": 1872}
    assert manifest["actions"] == [
        "idle", "running-right", "running-left", "waving", "jumping",
        "failed", "waiting", "running", "review",
    ]


def test_desktop_renderer_has_no_provider_secret_field() -> None:
    renderer = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "apps/desktop/renderer").rglob("*")
        if path.is_file() and path.suffix in {".ts", ".tsx", ".html"}
    )
    assert "api_key" not in renderer
    assert "nodeIntegration: true" not in renderer


def test_desktop_runtime_and_browser_are_loopback_only() -> None:
    runtime = (ROOT / "apps/desktop/main/runtime_supervisor.ts").read_text(encoding="utf-8")
    broker = (ROOT / "apps/desktop/main/backend_broker.ts").read_text(encoding="utf-8")
    windows = (ROOT / "apps/desktop/main/windows.ts").read_text(encoding="utf-8")
    assert '"127.0.0.1"' in runtime
    assert '"127.0.0.1"' in broker
    assert "contextIsolation: true" in windows
    assert "nodeIntegration: false" in windows
    assert "sandbox: true" in windows


def test_mvp3_settings_and_pet_interactions_are_wired() -> None:
    renderer = (ROOT / "apps/desktop/renderer/src/App.tsx").read_text(encoding="utf-8")
    pet_renderer = (ROOT / "apps/desktop/renderer/src/PetApp.tsx").read_text(encoding="utf-8")
    main = (ROOT / "apps/desktop/main/app.ts").read_text(encoding="utf-8")
    assert "/probe" in renderer
    assert "/models" in renderer
    assert "provider.configure" in renderer
    assert "pet.set-ignore-mouse-events" in main
    assert "setIgnoreMouseEvents" in pet_renderer
    assert "-webkit-app-region: drag" in (ROOT / "apps/desktop/renderer/src/pet.css").read_text(encoding="utf-8")


def test_pet_package_has_build_time_validator() -> None:
    validator = ROOT / "apps/pet/package/validate.mjs"
    prepare = (ROOT / "apps/desktop/scripts/prepare-assets.mjs").read_text(encoding="utf-8")
    assert validator.is_file()
    assert "validatePetPackage" in prepare
