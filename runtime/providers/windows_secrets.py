"""Windows CurrentUser DPAPI credential store shared with the Node CLI."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
from pathlib import Path

from config.paths import deepprof_home


class WindowsDpapiSecretStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (deepprof_home() / "credentials" / "dpapi.json")
        self._lock = threading.RLock()

    def get(self, ref: str) -> str | None:
        if os.name != "nt" or not ref:
            return None
        with self._lock:
            encoded = self._read().get(ref)
            if not encoded:
                return None
            try:
                ciphertext = base64.b64decode(str(encoded), validate=True)
                raw = _powershell_protect(ciphertext, decrypt=True)
                return raw.decode("utf-8")
            except (ValueError, UnicodeError, OSError, subprocess.SubprocessError):
                return None

    def set(self, ref: str, value: str) -> None:
        if os.name != "nt":
            raise RuntimeError("dpapi_unavailable")
        if not ref or not value:
            raise ValueError("invalid_secret")
        with self._lock:
            values = self._read()
            protected = _powershell_protect(value.encode("utf-8"), decrypt=False)
            values[ref] = base64.b64encode(protected).decode("ascii")
            self._write(values)

    def delete(self, ref: str) -> None:
        with self._lock:
            values = self._read()
            if ref not in values:
                return
            del values[ref]
            self._write(values)

    def _read(self) -> dict[str, str]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {str(key): str(value) for key, value in raw.items()} if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, values: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(self.path)


def _powershell_protect(value: bytes, *, decrypt: bool) -> bytes:
    if decrypt:
        script = (
            "Add-Type -AssemblyName System.Security;$raw=[Console]::In.ReadToEnd();"
            "$cipher=[Convert]::FromBase64String($raw);"
            "$plain=[Security.Cryptography.ProtectedData]::Unprotect($cipher,$null,"
            "[Security.Cryptography.DataProtectionScope]::CurrentUser);"
            "[Console]::Out.Write([Convert]::ToBase64String($plain))"
        )
        payload = base64.b64encode(value).decode("ascii")
    else:
        script = (
            "Add-Type -AssemblyName System.Security;$raw=[Console]::In.ReadToEnd();"
            "$plain=[Convert]::FromBase64String($raw);"
            "$cipher=[Security.Cryptography.ProtectedData]::Protect($plain,$null,"
            "[Security.Cryptography.DataProtectionScope]::CurrentUser);"
            "[Console]::Out.Write([Convert]::ToBase64String($cipher))"
        )
        payload = base64.b64encode(value).decode("ascii")
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-Command", script],
        input=payload,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise OSError("dpapi_operation_failed")
    return base64.b64decode(completed.stdout.strip(), validate=True)
