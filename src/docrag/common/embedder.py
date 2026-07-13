"""Обёртка эмбеддера bge-m3 (dense + learned-sparse) поверх FlagEmbedding.

Одна модель на dense (1024-dim, Cosine) и sparse (лексические веса для гибрида).
Загружается лениво один раз (singleton), устройство — MPS/CPU из настроек.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from docrag.common.logging import get_logger
from docrag.settings import settings

log = get_logger("embedder")

_MODEL = None


@dataclass
class SparseVector:
    indices: list[int]
    values: list[float]


@dataclass
class Embedding:
    dense: list[float]
    sparse: SparseVector


def _resolve_device() -> str:
    dev = settings.embed_device
    if dev == "mps":
        try:
            import torch

            if not torch.backends.mps.is_available():
                log.warning("MPS недоступен — переключаюсь на CPU")
                return "cpu"
        except Exception:  # noqa: BLE001
            return "cpu"
    return dev


def _get_model():
    global _MODEL
    if _MODEL is None:
        from FlagEmbedding import BGEM3FlagModel

        device = _resolve_device()
        log.info("Загрузка эмбеддера %s на %s…", settings.embed_model, device)
        _MODEL = BGEM3FlagModel(
            settings.embed_model,
            use_fp16=settings.embed_use_fp16 and device != "cpu",
            devices=device,
        )
    return _MODEL


def _to_sparse(lexical_weights: dict) -> SparseVector:
    indices: list[int] = []
    values: list[float] = []
    for tok, w in lexical_weights.items():
        try:
            idx = int(tok)
        except (TypeError, ValueError):
            continue
        val = float(w)
        if val > 0:
            indices.append(idx)
            values.append(val)
    return SparseVector(indices=indices, values=values)


def _pack(out: dict, n: int) -> list[Embedding]:
    """Упаковывает выход model.encode в список Embedding для первых n текстов."""
    dense = out["dense_vecs"]
    sparse = out["lexical_weights"]
    return [
        Embedding(dense=[float(x) for x in dense[i]], sparse=_to_sparse(sparse[i]))
        for i in range(n)
    ]


def embed_texts(
    texts: list[str],
    batch_size: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[Embedding]:
    """Эмбеддинг списка текстов (для инжеста): dense + sparse.

    Если задан ``on_progress`` — считаем по батчам и после каждого зовём
    ``on_progress(готово, всего)`` (для индикатора прогресса в UI). Без него —
    один вызов encode (быстрее, поведение как раньше).
    """
    if not texts:
        return []
    model = _get_model()
    bs = batch_size or settings.embed_batch_size
    kw = dict(
        max_length=settings.embed_max_length,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    if on_progress is None:
        return _pack(model.encode(texts, batch_size=bs, **kw), len(texts))
    result: list[Embedding] = []
    for start in range(0, len(texts), bs):
        batch = texts[start:start + bs]
        result.extend(_pack(model.encode(batch, batch_size=bs, **kw), len(batch)))
        on_progress(min(start + bs, len(texts)), len(texts))
    return result


def embed_query(text: str) -> Embedding:
    """Эмбеддинг одного запроса."""
    return embed_texts([text], batch_size=1)[0]
