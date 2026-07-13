"""Пакетный парсинг корпуса: data/raw/manifest.json -> data/parsed/<doc_id>.json."""

from __future__ import annotations

import json
from pathlib import Path

from docrag.common.logging import get_logger
from docrag.parser.pdf_parser import parse_pdf
from docrag.settings import settings

log = get_logger("parser.run")


def parse_corpus(ocr: bool = True, only: list[str] | None = None) -> list[dict]:
    settings.ensure_dirs()
    manifest_path = settings.raw_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Нет манифеста {manifest_path}. Сначала выполните скрейпинг.")
    records = json.loads(manifest_path.read_text(encoding="utf-8"))

    summary: list[dict] = []
    for rec in records:
        if not rec.get("doc_id"):
            log.warning("Пропуск записи манифеста без doc_id: %s", rec.get("file_name", "?"))
            continue
        if only and rec["doc_id"] not in only:
            continue
        doc = parse_pdf(rec, ocr=ocr)
        out = settings.parsed_dir / f"{rec['doc_id']}.json"
        out.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        summary.append(
            {
                "doc_id": doc.meta.doc_id,
                "title": doc.meta.title,
                "pages": doc.meta.n_pages,
                "nodes": len(doc.nodes),
                "parser": doc.parser,
                "warnings": len(doc.warnings),
            }
        )
    parsed_index = settings.parsed_dir / "index.json"
    parsed_index.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Парсинг завершён: %d документов", len(summary))
    return summary
