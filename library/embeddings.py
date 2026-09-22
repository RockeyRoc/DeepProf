"""Offline, deterministic embedding primitives.

The default embedder intentionally has no model or network dependency.  Its
interface is compatible with a future BGE-M3 or provider-backed adapter.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, Sequence


class Embedder(Protocol):
    name: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]|[^\s]", re.IGNORECASE)


class HashingEmbedder:
    """Feature-hashing embedder with normalized cosine vectors."""

    name = "hashing-char-token-v1"

    def __init__(self, dimension: int = 384) -> None:
        if dimension < 32:
            raise ValueError("dimension must be at least 32")
        self.dimension = dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(str(text or "")) for text in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        normalized = " ".join(text.lower().split())
        tokens = _TOKEN_RE.findall(normalized)
        features: list[tuple[str, float]] = [(token, 1.0) for token in tokens]
        for start in range(max(0, len(normalized) - 2)):
            gram = normalized[start : start + 3]
            if not gram.isspace():
                features.append((f"c3:{gram}", 0.5))
        for feature, weight in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector

    @staticmethod
    def similarity(left: Sequence[float], right: Sequence[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        return float(sum(a * b for a, b in zip(left, right)))


__all__ = ["Embedder", "HashingEmbedder"]
