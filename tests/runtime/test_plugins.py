"""插件生命周期：信任名单、依赖、启停与失败事件。"""

from __future__ import annotations

from runtime.core.events import EventType
from runtime.plugins.manifest import PluginManifest
from runtime.plugins.registry import PluginRegistry
from runtime.plugins.trust import TrustList


class FakePlugin:
    def __init__(self) -> None:
        self.started = False

    def start(self, ctx):
        self.started = True

    def stop(self):
        self.started = False


def make_registry(*plugin_ids: str, trusted: bool = True) -> tuple[PluginRegistry, list[dict]]:
    emitted: list[dict] = []

    async def emit(event_type: str, payload: dict) -> None:
        emitted.append({"type": event_type, "payload": payload})

    trust = TrustList(set(plugin_ids) if trusted else set())
    registry = PluginRegistry(trust, emit=emit)
    for plugin_id in plugin_ids:
        registry.register(PluginManifest(id=plugin_id, version="1.0.0"), FakePlugin())
    return registry, emitted


async def test_trusted_plugin_starts_and_emits_event():
    registry, emitted = make_registry("official.echo")
    record = await registry.start("official.echo")
    assert record.state == "started"
    assert emitted[0]["type"] == EventType.PLUGIN_STARTED.value
    assert emitted[0]["payload"] == {"plugin_id": "official.echo", "version": "1.0.0"}


async def test_untrusted_plugin_is_refused():
    registry, emitted = make_registry("third.party", trusted=False)
    record = await registry.start("third.party")
    assert record.state == "failed"
    assert emitted[0]["type"] == EventType.PLUGIN_FAILED.value
    assert emitted[0]["payload"]["error"]["code"] == "untrusted_plugin"


async def test_missing_dependency_blocks_start():
    registry, emitted = make_registry("app")
    registry.register(PluginManifest(id="needs", version="1.0.0", dependencies=["app"]), FakePlugin())
    registry.trust.trust("needs")

    record = await registry.start("needs")
    assert record.state == "failed"
    assert emitted[-1]["payload"]["error"]["code"] == "dependency_missing"

    await registry.start("app")
    record = await registry.start("needs")
    assert record.state == "started"


async def test_plugin_start_failure_is_isolated():
    registry, emitted = make_registry("boom")

    class Broken:
        def start(self, ctx):
            raise RuntimeError("插件内部崩溃")

    registry.register(PluginManifest(id="boom2", version="1.0.0"), Broken())
    registry.trust.trust("boom2")

    record = await registry.start("boom2")
    assert record.state == "failed"
    assert "插件内部崩溃" in record.error
    assert emitted[-1]["payload"]["error"]["code"] == "plugin_start_failed"


async def test_stop_and_uninstall():
    registry, emitted = make_registry("p1")
    await registry.start("p1")
    await registry.stop("p1")
    assert emitted[-1]["type"] == EventType.PLUGIN_STOPPED.value
    await registry.uninstall("p1")
    assert registry.list() == []


async def test_unknown_plugin_raises():
    registry, _ = make_registry()
    try:
        await registry.start("ghost")
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "details", {}).get("kind") == "plugin_not_found"
    else:  # pragma: no cover
        raise AssertionError("应抛出 plugin_not_found")


def test_manifest_rejects_unknown_permission_domain():
    import pytest

    with pytest.raises(ValueError, match="未知权限域"):
        PluginManifest.from_dict({"id": "p", "permissions": {"telepathy": True}})


def test_manifest_load_from_disk(tmp_path):
    import json

    plugin_dir = tmp_path / "p"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"id": "p", "version": "0.1.0", "permissions": {"files": ["read"]}}),
        encoding="utf-8",
    )
    manifest = PluginManifest.load(plugin_dir / "plugin.json")
    assert manifest.id == "p"
    assert manifest.permission("files") == ["read"]