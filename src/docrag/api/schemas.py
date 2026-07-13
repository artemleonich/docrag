"""Схемы запросов/ответов API."""

from __future__ import annotations

from pydantic import BaseModel


class AskRequest(BaseModel):
    question: str
    model: str | None = None      # id модели генерации из /models (None → дефолт)


class GenerateRequest(BaseModel):
    question: str
    doc_type: str = "spravka"     # для .docx: spravka | vypiska | proekt_otveta
    subject: str | None = None
    model: str | None = None      # id модели генерации из /models (None → дефолт)


class DocumentInfo(BaseModel):
    doc_id: str
    title: str
    doc_type: str
    pages: int | None = None
    effective_date: str | None = None
    scanned: bool = False
    file_name: str | None = None      # оригинальное имя исходного PDF (для скачивания)
