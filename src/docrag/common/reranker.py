"""Cross-encoder реранкер bge-reranker-v2-m3 напрямую через transformers.

Реализован без FlagReranker (тот несовместим со свежим transformers).
Даёт полный контроль над устройством (MPS) и батчингом. Скор = logit (можно sigmoid).
"""

from __future__ import annotations

from dataclasses import dataclass

from docrag.common.logging import get_logger
from docrag.settings import settings

log = get_logger("reranker")

_TOKENIZER = None
_MODEL = None
_DEVICE = None


@dataclass
class RerankResult:
    index: int          # индекс во входном списке
    score: float        # logit релевантности (больше = релевантнее)


def _resolve_device() -> str:
    dev = settings.embed_device
    if dev == "mps":
        import torch

        if not torch.backends.mps.is_available():
            return "cpu"
    return dev


def _load():
    global _TOKENIZER, _MODEL, _DEVICE
    if _MODEL is None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        _DEVICE = _resolve_device()
        log.info("Загрузка реранкера %s на %s…", settings.rerank_model, _DEVICE)
        _TOKENIZER = AutoTokenizer.from_pretrained(settings.rerank_model)
        _MODEL = AutoModelForSequenceClassification.from_pretrained(settings.rerank_model)
        _MODEL = _MODEL.to(_DEVICE).eval()
        if settings.embed_use_fp16 and _DEVICE != "cpu":
            _MODEL = _MODEL.half()
    return _TOKENIZER, _MODEL, _DEVICE


def rerank(
    query: str,
    passages: list[str],
    top_n: int | None = None,
    max_length: int = 512,
    batch_size: int = 16,
) -> list[RerankResult]:
    """Возвращает результаты, отсортированные по убыванию релевантности."""
    if not passages:
        return []
    import torch

    tok, model, device = _load()
    scores: list[float] = []
    for start in range(0, len(passages), batch_size):
        batch = passages[start : start + batch_size]
        inp = tok(
            [query] * len(batch),
            batch,
            padding=True,
            truncation=True,
            max_length=max_length,
            return_tensors="pt",
        ).to(device)
        with torch.no_grad():
            logits = model(**inp).logits.view(-1).float().cpu()
        scores.extend(logits.tolist())
    ranked = sorted(
        (RerankResult(index=i, score=s) for i, s in enumerate(scores)),
        key=lambda r: r.score,
        reverse=True,
    )
    return ranked[:top_n] if top_n else ranked
