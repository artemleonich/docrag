"""Иерархический чанкинг нормативного документа (small-to-big).

- Единица чанка = пункт (узел структуры) — он же единица цитаты.
- Эмбеддится ДОЧЕРНИЙ текст с контекст-заголовком [Документ][Раздел][п. X.Y]
  (офлайн-аналог contextual retrieval — большой прирост recall).
- В LLM подаётся РОДИТЕЛЬСКИЙ контекст (пункт + лид родителя), чтобы фрагмент
  был самодостаточным.
- Длинные пункты режутся на окна по предложениям с перекрытием.
"""

from __future__ import annotations

import hashlib
import re
import uuid

from docrag.common.models import Chunk, ParsedDocument, SectionNode
from docrag.settings import settings

# Приближение символ↔токен для русского (bge-m3 sentencepiece): ~3.5 симв/токен
_CHARS_PER_TOKEN = 3.5
_MIN_CHUNK_CHARS = 24
_SENT_SPLIT = re.compile(r"(?<=[.;:])\s+")

# фиксированный namespace для детерминированных id точек Qdrant
NAMESPACE = uuid.UUID("6a9c1e00-0000-4000-8000-000000000000")


def _child_chars() -> int:
    return int(settings.child_chunk_tokens * _CHARS_PER_TOKEN)


def _overlap_chars() -> int:
    return int(settings.child_chunk_overlap * _CHARS_PER_TOKEN)


def _parent_chars() -> int:
    return int(settings.parent_max_tokens * _CHARS_PER_TOKEN)


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(NAMESPACE, chunk_id))


def _section_top(node: SectionNode) -> str:
    return node.path_titles[0] if node.path_titles else ""


def _context_header(doc_title: str, node: SectionNode) -> str:
    parts = [doc_title]
    top = _section_top(node)
    if top and top not in doc_title:
        parts.append(top)
    parts.append(node.citation_label())
    return " ▸ ".join(p for p in parts if p)


def _split_sentences(text: str, target: int, overlap: int) -> list[str]:
    if len(text) <= target:
        return [text]
    sents = _SENT_SPLIT.split(text)
    windows: list[str] = []
    cur = ""
    for s in sents:
        if cur and len(cur) + len(s) + 1 > target:
            windows.append(cur.strip())
            # перекрытие: берём хвост предыдущего окна
            tail = cur[-overlap:] if overlap else ""
            cur = (tail + " " + s).strip()
        else:
            cur = (cur + " " + s).strip()
    if cur.strip():
        windows.append(cur.strip())
    return windows or [text]


def _parent_context(node: SectionNode, by_id: dict[str, SectionNode]) -> str:
    """Родительский контекст для подачи в LLM: лид родителя + текст пункта, с потолком."""
    pieces: list[str] = []
    parent = by_id.get(node.parent_id) if node.parent_id else None
    if parent and parent.text and len(node.text) < 800:
        lead = parent.text[:600].strip()
        pieces.append(f"{parent.citation_label()}: {lead}")
    pieces.append(f"{node.citation_label()}: {node.text}")
    ctx = "\n".join(pieces)
    cap = _parent_chars()
    return ctx[:cap]


def chunk_document(doc: ParsedDocument) -> list[Chunk]:
    by_id = {n.node_id: n for n in doc.nodes}
    chunks: list[Chunk] = []
    child_target = _child_chars()
    overlap = _overlap_chars()

    for node in doc.nodes:
        text = (node.text or "").strip()
        if len(text) < _MIN_CHUNK_CHARS:
            continue
        header = _context_header(doc.meta.title, node)
        section_path = " > ".join(node.path_titles)
        parent_text = _parent_context(node, by_id)
        windows = _split_sentences(text, child_target, overlap)
        for wi, win in enumerate(windows):
            # node_id уникален (учитывает приложения) — избегаем коллизий id точек
            base_id = f"{node.node_id}:{wi}"
            embed_text = f"{header}: {win}"
            chunks.append(
                Chunk(
                    chunk_id=base_id,
                    doc_id=doc.meta.doc_id,
                    doc_title=doc.meta.title,
                    text=embed_text,
                    parent_text=parent_text,
                    parent_id=node.parent_id,
                    section_path=section_path,
                    clause=node.number,
                    page=node.page_start,
                    token_count=int(len(win) / _CHARS_PER_TOKEN),
                    doc_type=doc.meta.doc_type,
                    source_url=doc.meta.source_url,
                    effective_date=(
                        doc.meta.effective_date.isoformat() if doc.meta.effective_date else None
                    ),
                )
            )

    # Fallback: документ без распознанной точечной нумерации (напр. загруженный
    # регламент со сплошным текстом) не должен молча выпадать из индекса —
    # режем постранично окнами, привязывая номер страницы для цитаты «стр. N».
    if not chunks and doc.page_texts:
        for page_no, ptext in enumerate(doc.page_texts, 1):
            ptext = (ptext or "").strip()
            if len(ptext) < _MIN_CHUNK_CHARS:
                continue
            for wi, win in enumerate(_split_sentences(ptext, child_target, overlap)):
                chunks.append(
                    Chunk(
                        chunk_id=f"{doc.meta.doc_id}#p{page_no}:{wi}",
                        doc_id=doc.meta.doc_id,
                        doc_title=doc.meta.title,
                        text=f"{doc.meta.title}: {win}",
                        parent_text=win,
                        section_path=f"стр. {page_no}",
                        clause=None,
                        page=page_no,
                        token_count=int(len(win) / _CHARS_PER_TOKEN),
                        doc_type=doc.meta.doc_type,
                        source_url=doc.meta.source_url,
                        effective_date=(
                            doc.meta.effective_date.isoformat() if doc.meta.effective_date else None
                        ),
                    )
                )
    return chunks


def point_id_for(chunk: Chunk) -> str:
    return _point_id(chunk.chunk_id)


def content_hash(chunk: Chunk) -> str:
    return hashlib.md5(chunk.text.encode("utf-8")).hexdigest()
