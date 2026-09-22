"""Structured failures raised by the resource library."""

from __future__ import annotations

from typing import Any

from runtime.core.errors import RuntimeFailure


class LibraryError(RuntimeFailure):
    """A user-actionable resource-library failure."""

    code = "library_error"

    def __init__(self, message: str, *, kind: str, **details: Any) -> None:
        super().__init__(message, details={"kind": kind, **details})


__all__ = ["LibraryError"]
