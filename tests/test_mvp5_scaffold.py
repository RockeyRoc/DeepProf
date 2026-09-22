"""MVP-5 structural and secret-boundary checks."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mvp5_cli_and_evaluation_surfaces_exist():
    for item in (
        "apps/cli/package.json",
        "apps/cli/src/index.ts",
        "apps/cli/src/session_runner.ts",
        "packages/client_sdk/provider_client.ts",
        "packages/client_sdk/node/runtime_supervisor.ts",
        "packages/client_sdk/node/dpapi_secret_store.ts",
        "packages/contracts/runtime_discovery.json",
        "evaluation/metrics.py",
        "evaluation/run_mvp5.py",
    ):
        assert (ROOT / item).is_file(), item
    assert any(item.is_file() for item in (ROOT / "docs").glob("MVP-5_*.md"))


def test_cli_never_accepts_an_api_key_command_line_option():
    source = (ROOT / "apps/cli/src/index.ts").read_text(encoding="utf-8")
    assert '"--api-key-stdin"' in source
    assert "api_key_command_line_not_supported" in source


def test_runtime_discovery_rejects_non_loopback_by_construction():
    source = (ROOT / "packages/client_sdk/node/gateway_discovery.ts").read_text(encoding="utf-8")
    assert '"127.0.0.1"' in source
    assert '"localhost"' in source
    assert '"::1"' in source
    assert "owner_token" in source


def test_cli_has_no_pi_agent_dependency():
    manifest = (ROOT / "apps/cli/package.json").read_text(encoding="utf-8").lower()
    assert "pi-agent" not in manifest
    assert "pi-tui" not in manifest
