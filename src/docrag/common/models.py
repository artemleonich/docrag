"""Доменные модели данных (единый контракт между модулями).

Поток: RawDoc -> ParsedDocument (+SectionNode дерево) -> Chunk -> Qdrant payload
       -> RetrievedChunk -> Citation -> Answer.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
#  Метаданные документа
# --------------------------------------------------------------------------- #
class DocumentMeta(BaseModel):
    doc_id: str                              # стабильный id (slug + хэш)
    title: str                               # человекочитаемое название
    doc_type: str = "regulation"             # regulation | tariff | rules | charter | other
    source_url: Optional[str] = None
    file_name: Optional[str] = None
    sha256: Optional[str] = None
    n_pages: Optional[int] = None
    summary: Optional[str] = None            # краткая аннотация (1-2 фразы) для ответов «о чём документ»
    # Даты из преамбулы/ссылок (важно для цитат и актуальности)
    reg_date: Optional[date] = None          # дата регистрации документа (опционально)
    publish_date: Optional[date] = None      # дата размещения
    effective_date: Optional[date] = None    # дата вступления в силу
    version_note: Optional[str] = None       # напр. «ред. от 01.03.2026»


# --------------------------------------------------------------------------- #
#  Структура документа (иерархия раздел -> статья/пункт -> подпункт)
# --------------------------------------------------------------------------- #
class SectionNode(BaseModel):
    node_id: str                             # doc_id#path (напр. "устав#4.2.1")
    level: int                               # 0=раздел, 1=пункт, 2=подпункт ...
    number: Optional[str] = None             # "4", "4.2", "4.2.1"
    title: Optional[str] = None              # заголовок раздела/пункта, если есть
    text: str = ""                           # собственный текст узла
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    parent_id: Optional[str] = None
    path_titles: list[str] = Field(default_factory=list)  # ["Раздел 2. Определения", "п. 2.1 ..."]

    def citation_label(self) -> str:
        """Например: 'п. 4.2.1' или 'Раздел 4'."""
        if self.number and self.level >= 1:
            return f"п. {self.number}"
        if self.number:
            return f"Раздел {self.number}"
        return self.title or self.node_id


class ParsedDocument(BaseModel):
    meta: DocumentMeta
    full_text: str = ""
    nodes: list[SectionNode] = Field(default_factory=list)
    page_texts: list[str] = Field(default_factory=list)   # текст по страницам (для fallback-нумерации)
    parser: str = "pymupdf"                                 # pymupdf | docling
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
#  Чанки для индексации
# --------------------------------------------------------------------------- #
class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    doc_title: str
    text: str                                # текст child-чанка (то, что эмбеддим)
    parent_text: str = ""                    # родительский блок (подаётся в LLM)
    parent_id: Optional[str] = None
    section_path: str = ""                   # "Раздел 2. Определения > п. 2.1"
    clause: Optional[str] = None             # "4.2.1"
    page: Optional[int] = None
    token_count: int = 0
    # копия важных метаданных документа для фильтрации/цитат в payload
    doc_type: str = "regulation"
    source_url: Optional[str] = None
    effective_date: Optional[str] = None

    def citation_ref(self) -> str:
        """Готовая ссылка: '<Документ>, п. 4.2.1, стр. 12'."""
        parts = [self.doc_title]
        if self.clause:
            parts.append(f"п. {self.clause}")
        if self.page is not None:
            parts.append(f"стр. {self.page}")
        return ", ".join(parts)


# --------------------------------------------------------------------------- #
#  Ретрив и ответ
# --------------------------------------------------------------------------- #
class RetrievedChunk(BaseModel):
    chunk: Chunk
    dense_score: float = 0.0
    sparse_score: float = 0.0
    fused_score: float = 0.0
    rerank_score: Optional[float] = None
    rank: int = 0


class Citation(BaseModel):
    marker: str                              # "[1]"
    doc_id: str
    doc_title: str
    clause: Optional[str] = None
    page: Optional[int] = None
    section_path: str = ""
    snippet: str = ""
    source_url: Optional[str] = None
    rerank_score: Optional[float] = None

    def ref(self) -> str:
        parts = [self.doc_title]
        if self.clause:
            parts.append(f"п. {self.clause}")
        if self.page is not None:
            parts.append(f"стр. {self.page}")
        return ", ".join(parts)


class Answer(BaseModel):
    question: str
    answer: str
    grounded: bool                           # найден ли ответ в контексте
    citations: list[Citation] = Field(default_factory=list)
    contexts: list[RetrievedChunk] = Field(default_factory=list)
    trace: dict = Field(default_factory=dict)  # лог шагов ретрива для проверяемости


AnswerStatus = Literal["ok", "not_found", "error"]
