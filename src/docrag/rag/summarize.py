"""Краткая аннотация документа (1-2 предложения).

Генерируется один раз при индексации из начала документа и сохраняется в метаданных.
Используется для ответов на вопросы «о чём документы / про что / краткое содержание»
(маршрут corpus_about в pipeline). Необязательна: при недоступности LLM индексация
не прерывается — аннотация просто остаётся пустой.
"""

from __future__ import annotations

import re

from docrag.common.logging import get_logger

log = get_logger("rag.summarize")

# Мелкие модели любят начинать с «Документ — …» / «Этот документ …»; срезаем вводный оборот.
_LEADIN = re.compile(
    r"^\s*(?:это|этот|данный|настоящий|представленный)?\s*документ\s*"
    r"(?:—|–|-|:|представляет собой|это)?\s*",
    re.IGNORECASE,
)


def _strip_leadin(s: str) -> str:
    out = _LEADIN.sub("", s, count=1).strip()
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return out or s

_SYSTEM = (
    "Ты кратко и точно описываешь, о чём документ, на русском языке. Ответь ОДНИМ-ДВУМЯ "
    "предложениями: что это за документ и его основная тема/назначение. Пиши по делу, без "
    "вводных оборотов вроде «Этот документ» и без выдумок — только то, что видно из текста."
)

_MAX_EXCERPT = 4000   # начала документа обычно достаточно для темы; экономим контекст LLM


def summarize_document(title: str, doc_type: str, text: str) -> str | None:
    """Возвращает краткую аннотацию или None (если текста мало / LLM недоступна)."""
    excerpt = (text or "").strip()[:_MAX_EXCERPT]
    if len(excerpt) < 40:
        return None
    user = (
        f"Название: {title}\nТип: {doc_type}\nНачало документа:\n{excerpt}\n\n"
        f"О чём этот документ? Ответь 1-2 предложениями."
    )
    try:
        from docrag.rag.llm import get_llm

        s = _strip_leadin(get_llm().chat(_SYSTEM, user, max_tokens=160).strip())
        return s or None
    except Exception as e:  # noqa: BLE001 — аннотация необязательна, не роняем индексацию
        log.warning("Не удалось составить аннотацию для '%s': %s", title, e)
        return None
