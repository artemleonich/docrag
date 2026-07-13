"""Оркестрация индексации: data/parsed/*.json -> чанки -> bge-m3 -> Qdrant."""

from __future__ import annotations

import json

from docrag.common.embedder import embed_texts
from docrag.common.logging import get_logger
from docrag.common.models import Chunk, ParsedDocument
from docrag.indexer.chunker import chunk_document
from docrag.indexer.qdrant_store import recreate_collection, upsert_chunks
from docrag.settings import settings

log = get_logger("indexer.run")


def _load_parsed(only: list[str] | None = None) -> list[ParsedDocument]:
    docs: list[ParsedDocument] = []
    for path in sorted(settings.parsed_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        if only and path.stem not in only:
            continue
        docs.append(ParsedDocument.model_validate_json(path.read_text(encoding="utf-8")))
    return docs


def build_index(only: list[str] | None = None, recreate: bool | None = None) -> dict:
    # По умолчанию пересоздаём коллекцию только при полной переиндексации.
    # Для подмножества (only=[...]) — дозагрузка без стирания всего индекса.
    if recreate is None:
        recreate = only is None
    docs = _load_parsed(only)
    if not docs:
        raise FileNotFoundError("Нет распарсенных документов. Сначала выполните парсинг.")

    all_chunks: list[Chunk] = []
    for doc in docs:
        ch = chunk_document(doc)
        log.info("  %s: %d чанков", doc.meta.doc_id, len(ch))
        all_chunks.extend(ch)

    # сохраняем чанки на диск (для отладки/оффлайн-инспекции)
    settings.ensure_dirs()
    (settings.chunks_dir / "chunks.jsonl").write_text(
        "\n".join(c.model_dump_json() for c in all_chunks), encoding="utf-8"
    )

    log.info("Всего чанков: %d. Считаю эмбеддинги (bge-m3 dense+sparse)…", len(all_chunks))
    embeddings = embed_texts([c.text for c in all_chunks])

    if recreate:
        recreate_collection()
    else:
        # дозагрузка: удаляем старые точки переиндексируемых документов,
        # иначе при смене chunk_id останутся устаревшие фрагменты
        from docrag.ingest import _delete_points

        for doc in docs:
            _delete_points(doc.meta.doc_id)
    n = upsert_chunks(all_chunks, embeddings)

    summary = {
        "documents": len(docs),
        "chunks": len(all_chunks),
        "points": n,
        "collection": settings.qdrant_collection,
    }
    (settings.chunks_dir / "index_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Индексация завершена: %s", summary)
    return summary
