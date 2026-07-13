"""Абстракция LLM-рантайма. Сейчас — Ollama; интерфейс позволяет позже подключить MLX.

Даёт: потоковую и обычную генерацию + надёжный structured output (JSON по схеме
через параметр Ollama `format` + защитный парсер).
"""

from __future__ import annotations

import json
import re
from typing import Iterator, Protocol

from docrag.common.logging import get_logger
from docrag.settings import settings

log = get_logger("llm")


class LLMBackend(Protocol):
    def chat(self, system: str, user: str, *, temperature: float | None = None,
             max_tokens: int | None = None) -> str: ...

    def chat_stream(self, system: str, user: str, *, temperature: float | None = None,
                    max_tokens: int | None = None) -> Iterator[str]: ...

    def chat_json(self, system: str, user: str, schema: dict, *,
                  max_tokens: int | None = None) -> dict: ...


# --------------------------------------------------------------------------- #
#  Защитный парсер JSON (JSON-mode гарантирует синтаксис не всегда)
# --------------------------------------------------------------------------- #
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _repair_truncated_json(text: str) -> str:
    """Достраивает оборванный JSON: закрывает строку/массивы/объекты по стеку."""
    stack: list[str] = []
    in_str = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
    repaired = text
    if in_str:
        repaired += '"'
    repaired = repaired.rstrip().rstrip(",")
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    return repaired


def defensive_json_parse(text: str) -> dict | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            text = m.group(1).strip()
    # вырезаем от первой { до последней }
    start = text.find("{")
    if start != -1:
        frag = text[start:]
        end = frag.rfind("}")
        for candidate in (
            frag[: end + 1] if end != -1 else frag,
            frag.rstrip().rstrip(",") + "}",
            _repair_truncated_json(frag),      # ремонт обрыва
        ):
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue
    return None


_THINK_BLOCK = re.compile(r"^\s*<think>.*?</think>\s*", re.S)


def _strip_think(text: str) -> str:
    """Страховка: срезаем ведущий блок рассуждений, если модель его всё же вернула."""
    return _THINK_BLOCK.sub("", text, count=1).lstrip("\n")


class OllamaBackend:
    def __init__(self, model: str | None = None) -> None:
        import ollama

        self.client = ollama.Client(host=settings.ollama_host)
        self.model = model or settings.llm_model
        # Думающие модели без явного think=False расходуют весь num_predict на рассуждения
        # → пустой content. Отключаем thinking для них (список в settings ПЛЮС эвристика по
        # имени — чтобы иной тег qwen3 не сломался тихо); остальным think не передаём вовсе
        # (у T-lite think=false вставляет мусорный </think>).
        _m = self.model.lower()
        is_thinking = (self.model in set(settings.llm_thinking_models)
                       or "qwen3" in _m or "thinking" in _m)
        self._think_kw: dict = {"think": False} if is_thinking else {}

    def _options(self, temperature: float | None, max_tokens: int | None) -> dict:
        return {
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "num_ctx": settings.llm_num_ctx,
            "num_predict": max_tokens or settings.llm_max_tokens,
            "seed": 0,  # детерминизм для проверяемости
            "top_p": 0.9,
        }

    def _messages(self, system: str, user: str) -> list[dict]:
        # ВАЖНО: у GGUF T-lite-it-2.1 при отдельной system-роли шаблон иногда
        # приводит к вырожденному пустому ответу (жадный EOS). Сливаем system в
        # user-сообщение — это устойчиво даёт корректную генерацию на temperature=0.
        if system:
            return [{"role": "user", "content": f"{system}\n\n{user}"}]
        return [{"role": "user", "content": user}]

    def chat(self, system: str, user: str, *, temperature: float | None = None,
             max_tokens: int | None = None) -> str:
        resp = self.client.chat(
            model=self.model,
            messages=self._messages(system, user),
            options=self._options(temperature, max_tokens),
            **self._think_kw,
        )
        return _strip_think(resp["message"]["content"])

    def chat_stream(self, system: str, user: str, *, temperature: float | None = None,
                    max_tokens: int | None = None) -> Iterator[str]:
        for part in self.client.chat(
            model=self.model,
            messages=self._messages(system, user),
            options=self._options(temperature, max_tokens),
            stream=True,
            **self._think_kw,
        ):
            piece = part.get("message", {}).get("content", "")
            if piece:
                yield piece

    def chat_json(self, system: str, user: str, schema: dict, *,
                  max_tokens: int | None = None) -> dict:
        # 1-я попытка: structured output через format=schema
        resp = self.client.chat(
            model=self.model,
            messages=self._messages(system, user),
            format=schema,
            options=self._options(0.0, max_tokens),
            **self._think_kw,
        )
        content = resp["message"]["content"]
        if not content.strip():
            log.warning("chat_json: пустой content (модель %s) — возможно thinking не отключён", self.model)
        parsed = defensive_json_parse(content)
        if isinstance(parsed, dict):
            return parsed
        # 2-я попытка: обычный JSON-режим + защитный парсер
        resp = self.client.chat(
            model=self.model,
            messages=self._messages(system, user + "\n\nВерни СТРОГО валидный JSON."),
            format="json",
            options=self._options(0.0, max_tokens),
            **self._think_kw,
        )
        parsed = defensive_json_parse(resp["message"]["content"])
        if not isinstance(parsed, dict):
            raise ValueError("LLM не вернул валидный JSON-объект после двух попыток")
        return parsed

    def health(self) -> bool:
        try:
            self.client.list()
            return True
        except Exception:  # noqa: BLE001
            return False


_BACKENDS: dict[str, LLMBackend] = {}


def get_llm(model: str | None = None) -> LLMBackend:
    """Бэкенд LLM для указанной модели (или дефолтной). Кэшируется по имени модели,
    чтобы переключение моделей в UI не пересоздавало клиента на каждый запрос."""
    key = model or settings.llm_model
    be = _BACKENDS.get(key)
    if be is None:
        if settings.llm_backend == "ollama":
            be = OllamaBackend(model=key)
        else:
            raise ValueError(f"Неизвестный LLM backend: {settings.llm_backend}")
        _BACKENDS[key] = be
    return be
