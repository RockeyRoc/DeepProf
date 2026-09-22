"""存储接口：EventStore / SessionStore / MemoryStore 的协议声明。

上层只依赖接口，不绑定具体数据库（§5.3）。
"""

from __future__ import annotations

from typing import Any, Protocol

from runtime.core.events import RuntimeEvent
from runtime.core.session import Session
from runtime.memory.base import MemoryRecord


class EventStorePort(Protocol):
    def append(self, event: RuntimeEvent) -> RuntimeEvent: ...

    def replay(self, session_id: str, from_sequence: int = 0) -> list[RuntimeEvent]: ...

    def last_sequence(self, session_id: str) -> int: ...


class SessionStorePort(Protocol):
    def save(self, session: Session) -> None: ...

    def load(self, session_id: str) -> Session | None: ...

    def list(self, learner_id: str | None = None) -> list[str]: ...


class MemoryStorePort(Protocol):
    def write(self, records: list[MemoryRecord]) -> int: ...

    def read(self, query: dict[str, Any]) -> list[MemoryRecord]: ...

    def delete(self, learner_id: str, record_id: str) -> bool: ...


class ResourceStorePort(Protocol):
    """Storage seam consumed by the product-level resource library."""

    def get(self, resource_id: str) -> dict[str, Any] | None: ...

    def find_by_hash(self, content_hash: str) -> list[dict[str, Any]]: ...

    def list_resources(self, **filters: Any) -> list[dict[str, Any]]: ...

    def preview(self, resource_id: str, *, limit: int = 100) -> list[dict[str, Any]]: ...
