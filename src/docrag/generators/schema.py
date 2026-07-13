"""Строгая схема презентации для structured output LLM (генерация .pptx)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Slide(BaseModel):
    title: str = Field(description="Краткий заголовок слайда")
    bullets: list[str] = Field(default_factory=list, description="Тезисы, каждый — короткая фраза")
    layout: str = Field(default="bullets", description="bullets | two_col | table")
    table: list[list[str]] = Field(
        default_factory=list,
        description="Строки таблицы (первая — заголовок столбцов), только для layout=table",
    )


class Deck(BaseModel):
    title: str = Field(description="Заголовок презентации")
    subtitle: str = Field(default="", description="Подзаголовок (тема/контекст)")
    slides: list[Slide] = Field(description="3–6 содержательных слайдов")


# Плоская JSON-схема для параметра Ollama `format` (без $defs/$ref — так llama.cpp
# может скомпилировать грамматику и гарантировать валидный JSON).
DECK_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                    "layout": {"type": "string", "enum": ["bullets", "two_col", "table"]},
                    "table": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "required": ["title", "bullets"],
            },
        },
    },
    "required": ["title", "slides"],
}
