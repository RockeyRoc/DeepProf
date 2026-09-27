from __future__ import annotations

import json
import os

import pytest

from runtime.providers.windows_secrets import WindowsDpapiSecretStore


@pytest.mark.skipif(os.name != "nt", reason="CurrentUser DPAPI is available only on Windows")
def test_gateway_dpapi_store_round_trips_and_removes_secret(tmp_path):
    path = tmp_path / "credentials" / "dpapi.json"
    store = WindowsDpapiSecretStore(path)
    store.set("provider:fixture", "secret-value")
    assert store.get("provider:fixture") == "secret-value"
    assert "secret-value" not in path.read_text(encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["provider:fixture"]
    store.delete("provider:fixture")
    assert store.get("provider:fixture") is None
