"""Offline, deterministic embedding primitives.

The default embedder intentionally has no model or network dependency.  Its
interface is compatible with a future BGE-M3 or provider-backed adapter.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Protocol, Sequence


class Embedder(Protocol):
    name: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]|[^\s]", re.IGNORECASE)


class HashingEmbedder:
    """Feature-hashing embedder with normalized cosine vectors."""

    name = "hashing-char-token-v1"
    model_version = "hashing-char-token-v1"

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


class BgeM3Embedder:
    """Optional local-snapshot BGE-M3 dense encoder, loaded on CUDA on demand.

    No package import or model download occurs until this class is instantiated.
    A pinned model revision is required so persisted vectors have traceable lineage.
    """

    name = "BAAI/bge-m3"
    dimension = 1024

    def __init__(self, model_path: str | Path, *, model_version: str, device: str = "cuda",
                 batch_size: int = 16, max_length: int = 8192, model_loader=None) -> None:
        path = Path(model_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError("bge_m3_local_model_snapshot_missing")
        if not model_version.strip():
            raise ValueError("bge_m3_model_revision_must_be_pinned")
        if device != "cuda":
            raise ValueError("bge_m3_research_embedder_requires_cuda")
        if batch_size < 1 or max_length < 1 or max_length > 8192:
            raise ValueError("invalid_bge_m3_batch_or_length")
        if model_loader is None:
            try:
                from FlagEmbedding import BGEM3FlagModel
            except ImportError as exc:  # pragma: no cover - depends on optional local install
                raise RuntimeError("FlagEmbedding_required_for_bge_m3") from exc
            model_loader = lambda: BGEM3FlagModel(str(path), use_fp16=True, devices=device)
        self.model_version = model_version.strip()
        self.dimension = 1024
        self.batch_size = batch_size
        self.max_length = max_length
        self.device = device
        self._model = model_loader()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        result = self._model.encode([str(text or "") for text in texts], batch_size=self.batch_size,
                                    max_length=self.max_length, return_dense=True,
                                    return_sparse=False, return_colbert_vecs=False)
        vectors = result.get("dense_vecs") if isinstance(result, dict) else None
        if vectors is None or len(vectors) != len(texts):
            raise ValueError("bge_m3_dense_vector_count_mismatch")
        output: list[list[float]] = []
        for vector in vectors:
            values = vector.tolist() if hasattr(vector, "tolist") else list(vector)
            values = [float(value) for value in values]
            if len(values) != self.dimension or not all(math.isfinite(value) for value in values):
                raise ValueError("bge_m3_dense_vector_dimension_or_value_mismatch")
            norm = math.sqrt(sum(value * value for value in values))
            if norm:
                values = [value / norm for value in values]
            output.append(values)
        return output


class BgeRerankerV2M3:
    """Optional local-snapshot BGE reranker on CUDA; score scale is kept explicit."""

    name = "BAAI/bge-reranker-v2-m3"

    def __init__(self, model_path: str | Path, *, model_version: str, device: str = "cuda",
                 model_loader=None) -> None:
        path = Path(model_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError("bge_reranker_local_model_snapshot_missing")
        if not model_version.strip():
            raise ValueError("bge_reranker_model_revision_must_be_pinned")
        if device != "cuda":
            raise ValueError("bge_reranker_requires_cuda")
        if model_loader is None:
            try:
                from FlagEmbedding import FlagReranker
            except ImportError as exc:  # pragma: no cover - depends on optional local install
                raise RuntimeError("FlagEmbedding_required_for_bge_reranker") from exc
            model_loader = lambda: FlagReranker(str(path), use_fp16=True, devices=device)
        self.model_version = model_version.strip()
        self.device = device
        self._model = model_loader()

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        pairs = [[str(query), str(passage)] for passage in passages]
        raw = self._model.compute_score(pairs, normalize=True)
        if isinstance(raw, (int, float)):
            raw = [raw]
        scores = [float(value) for value in raw]
        if len(scores) != len(passages) or not all(math.isfinite(value) for value in scores):
            raise ValueError("bge_reranker_score_count_or_value_mismatch")
        return scores


def retrieval_index_profile(embedder: Embedder, *, chunking_version: str,
                            reranker_version: str = "none") -> dict[str, str | int]:
    profile: dict[str, str | int] = {
        "schema_version": "deepprof-retrieval-index-profile-v1",
        "embedding_model": str(embedder.name),
        "embedding_model_version": str(getattr(embedder, "model_version", embedder.name)),
        "embedding_dimension": int(embedder.dimension),
        "chunking_version": str(chunking_version),
        "reranker_version": str(reranker_version or "none"),
    }
    canonical = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    profile["config_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return profile


def versioned_index_path(root: str | Path, profile: dict[str, str | int]) -> Path:
    """Return a distinct SQLite filename for each immutable retrieval profile."""
    config_id = str(profile.get("config_id") or "")
    if not re.fullmatch(r"[0-9a-f]{20}", config_id):
        raise ValueError("retrieval_profile_config_id_invalid")
    return Path(root) / "indexes" / f"library-{config_id}.sqlite"


__all__ = ["BgeM3Embedder", "BgeRerankerV2M3", "Embedder", "HashingEmbedder",
           "retrieval_index_profile", "versioned_index_path"]
