"""Инкрементальная дозагрузка одного документа в базу.

Работает и для текстовых PDF, и для сканов (парсер сам определяет скан и запускает
macOS Vision OCR). Документ добавляется в существующую коллекцию Qdrant без
пересоздания. Источник — локальный путь или URL.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Callable

from docrag.common.embedder import embed_texts
from docrag.common.logging import get_logger
from docrag.indexer.chunker import chunk_document
from docrag.indexer.qdrant_store import collection_info, recreate_collection, upsert_chunks
from docrag.parser.pdf_parser import parse_pdf
from docrag.settings import settings

log = get_logger("ingest")


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text)   # транслит кириллицы -> латиница
    text = re.sub(r"[^a-z0-9\-]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")[:60] or "document"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def _resolve_source(source: str, doc_id: str) -> Path:
    """Кладёт документ в data/raw/<doc_id>.pdf (копирование или скачивание)."""
    settings.ensure_dirs()
    dest = settings.raw_dir / f"{doc_id}.pdf"
    if source.startswith(("http://", "https://")):
        from docrag.scraper.politeness import PoliteClient

        with PoliteClient() as client:
            if client.download_file(source, dest) is None:
                raise RuntimeError(f"Не удалось скачать: {source}")
    else:
        src = Path(source).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"Файл не найден: {src}")
        shutil.copyfile(src, dest)
    return dest


def _update_manifest(record: dict) -> None:
    manifest_path = settings.raw_dir / "manifest.json"
    records = []
    if manifest_path.exists():
        records = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = [r for r in records if r.get("doc_id") != record["doc_id"]]
    records.append(record)
    manifest_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def _delete_points(doc_id: str) -> None:
    """Удаляет из Qdrant все точки документа (по payload doc_id)."""
    from qdrant_client import models as qm

    from docrag.indexer.qdrant_store import get_client

    if not collection_info().get("exists"):
        return
    get_client().delete(
        settings.qdrant_collection,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
        ),
    )


def add_document(
    source: str,
    title: str,
    doc_id: str | None = None,
    doc_type: str = "regulation",
    source_url: str | None = None,
    doc_date: str | None = None,
    protocol: int | None = None,
    ocr: bool = True,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Добавляет один документ в базу (парсинг + OCR при необходимости + индексация).

    ``on_progress`` (опционально) вызывается на каждом этапе с событием вида
    ``{"stage": "parse", "status": "progress", ...}`` — чтобы UI показывал пользователю,
    что именно сейчас делается с документом (см. эндпоинт /documents/upload/stream).
    """
    def emit(stage: str, status: str, **extra) -> None:
        if on_progress:
            on_progress({"stage": stage, "status": status, **extra})

    # санитизация id: только латиница/цифры/дефис (защита от коллизий и выхода за data-каталог)
    doc_id = _slugify(doc_id) if doc_id else _slugify(title)
    log.info("Дозагрузка документа '%s' (id=%s)…", title, doc_id)

    emit("receive", "start")
    pdf_path = _resolve_source(source, doc_id)
    record = {
        "doc_id": doc_id,
        "title": title,
        "doc_type": doc_type,
        "url": source_url or (source if source.startswith("http") else None),
        "file_name": Path(source).name,
        "local_path": str(pdf_path),
        "sha256": _sha256(pdf_path),
        "doc_date": doc_date,
        "protocol": protocol,
    }
    emit("receive", "done")

    # парсинг (+OCR для сканов автоматически) — с постраничным прогрессом
    emit("parse", "start")
    parsed = parse_pdf(
        record, ocr=ocr,
        on_page=(lambda page, total, page_ocr:
                 emit("parse", "progress", page=page, total=total, ocr=page_ocr)) if on_progress else None,
    )
    scanned = "ocr" in parsed.parser
    emit("parse", "done", pages=parsed.meta.n_pages, nodes=len(parsed.nodes), scanned=scanned)

    # краткая аннотация документа (для ответов «о чём документ») — необязательна,
    # до записи JSON, чтобы аннотация сохранилась в метаданных
    emit("summarize", "start")
    from docrag.rag.summarize import summarize_document

    parsed.meta.summary = summarize_document(title, doc_type, parsed.full_text)
    emit("summarize", "done")

    (settings.parsed_dir / f"{doc_id}.json").write_text(
        parsed.model_dump_json(indent=2), encoding="utf-8"
    )
    _update_manifest(record)

    # чанкинг + эмбеддинги + upsert в существующую коллекцию
    emit("chunk", "start")
    chunks = chunk_document(parsed)
    if not chunks:
        raise RuntimeError(f"Документ '{doc_id}' не дал ни одного чанка (пустой текст?)")
    emit("chunk", "done", chunks=len(chunks))
    if not collection_info().get("exists"):
        recreate_collection()
    # КРИТИЧНО: при повторной дозагрузке (обновлённая редакция) chunk_id меняются,
    # поэтому сначала удаляем ВСЕ старые точки этого документа — иначе в индексе
    # остаются устаревшие фрагменты и цитаты на неактуальную редакцию.
    _delete_points(doc_id)
    emit("embed", "start", total=len(chunks))
    embeddings = embed_texts(
        [c.text for c in chunks],
        on_progress=(lambda done, total: emit("embed", "progress", done=done, total=total))
        if on_progress else None,
    )
    emit("embed", "done")
    emit("index", "start")
    n = upsert_chunks(chunks, embeddings)
    emit("index", "done", points=n)

    summary = {
        "doc_id": doc_id,
        "title": title,
        "pages": parsed.meta.n_pages,
        "nodes": len(parsed.nodes),
        "chunks": len(chunks),
        "points_added": n,
        "parser": parsed.parser,
        "scanned": "ocr" in parsed.parser,
        "warnings": parsed.warnings,
    }
    log.info("Документ добавлен: %s", summary)
    return summary


def remove_document(doc_id: str) -> int:
    """Удаляет документ из индекса (по doc_id), с диска и из манифеста."""
    _delete_points(doc_id)
    for p in (settings.raw_dir / f"{doc_id}.pdf", settings.parsed_dir / f"{doc_id}.json"):
        p.unlink(missing_ok=True)
    # чистим запись в манифесте, чтобы не осталось ссылки на удалённый файл
    manifest_path = settings.raw_dir / "manifest.json"
    if manifest_path.exists():
        records = json.loads(manifest_path.read_text(encoding="utf-8"))
        records = [r for r in records if r.get("doc_id") != doc_id]
        manifest_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Документ '%s' удалён из индекса, с диска и из манифеста", doc_id)
    return collection_info().get("points", 0)
