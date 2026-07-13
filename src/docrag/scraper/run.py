"""Оркестрация скрейпинга: обход страниц-семян -> отбор субкорпуса -> скачивание -> манифест."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from docrag.common.logging import get_logger
from docrag.scraper.catalog import (
    PdfCandidate,
    extract_pdf_candidates,
    rank_families,
    select_targeted_subcorpus,
)
from docrag.scraper.politeness import PoliteClient
from docrag.settings import settings

log = get_logger("scraper.run")

# Страницы-семена, где перечислены целевые PDF (замените путями своего источника).
SEED_PAGES: list[str] = [
    "/documents/",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def discover(
    seeds: list[str] | None = None,
    client: PoliteClient | None = None,
) -> list[PdfCandidate]:
    seeds = seeds or SEED_PAGES
    own_client = client is None
    client = client or PoliteClient()
    candidates: dict[str, PdfCandidate] = {}
    try:
        for seed in seeds:
            html = client.get_html(seed)
            if not html:
                continue
            for cand in extract_pdf_candidates(html, source_page=seed):
                candidates.setdefault(cand.clean_url, cand)
        log.info("Обнаружено уникальных PDF: %d", len(candidates))
    finally:
        if own_client:
            client.close()
    return list(candidates.values())


def _candidate_to_record(c: PdfCandidate, local_path: Path) -> dict:
    fam = c.family
    return {
        "doc_id": fam.key if fam else c.file_name,
        "family_key": fam.key if fam else None,
        "title": fam.title if fam else c.link_text or c.file_name,
        "doc_type": fam.doc_type if fam else "other",
        "url": c.clean_url,
        "source_page": c.source_page,
        "file_name": c.file_name,
        "local_path": str(local_path),
        "link_text": c.link_text,
        "protocol": c.protocol,
        "doc_date": c.doc_date.isoformat() if c.doc_date else None,
        "sha256": _sha256(local_path),
        "n_bytes": local_path.stat().st_size,
    }


def scrape(
    include_keys: set[str] | None = None,
    seeds: list[str] | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Полный цикл: обнаружение -> отбор субкорпуса -> скачивание -> манифест."""
    settings.ensure_dirs()
    client = PoliteClient()
    try:
        candidates = discover(seeds, client=client)
        ranked = rank_families(candidates, include_keys=include_keys)
        log.info("Отобрано семейств в субкорпус: %d", len(ranked))
        for key in sorted(ranked):
            top = ranked[key][0]
            v = f"протокол {top.protocol}" if top.protocol else (top.doc_date.isoformat() if top.doc_date else "?")
            log.info("  • %-22s %s  [%s, версий: %d]", key, top.family.title, v, len(ranked[key]))

        if dry_run:
            return [
                {
                    "doc_id": key,
                    "title": ranked[key][0].family.title,
                    "url": ranked[key][0].clean_url,
                    "file_name": ranked[key][0].file_name,
                    "protocol": ranked[key][0].protocol,
                    "doc_date": ranked[key][0].doc_date.isoformat() if ranked[key][0].doc_date else None,
                    "n_versions": len(ranked[key]),
                }
                for key in sorted(ranked)
            ]

        records: list[dict] = []
        for key in sorted(ranked):
            dest = settings.raw_dir / f"{key}.pdf"
            downloaded: PdfCandidate | None = None
            path = None
            # Пробуем версии от свежей к старой, пока одна не скачается (обход 401/битых ссылок)
            for c in ranked[key]:
                path = client.download_file(c.clean_url, dest)
                if path is not None:
                    downloaded = c
                    break
                log.warning("Версия недоступна (%s), пробую предыдущую…", c.file_name)
            if downloaded is None or path is None:
                log.error("Не удалось скачать ни одной версии семейства '%s'", key)
                continue
            records.append(_candidate_to_record(downloaded, path))

        # Сливаем с существующим манифестом: сохраняем документы, добавленные вручную
        # (через add_document) и не входящие в скрейп-набор, чтобы не потерять их.
        manifest_path = settings.raw_dir / "manifest.json"
        scraped_ids = {r["doc_id"] for r in records}
        merged = list(records)
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            merged += [r for r in existing if r.get("doc_id") not in scraped_ids]
        manifest_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Манифест сохранён: %s (%d документов)", manifest_path, len(merged))
        return records
    finally:
        client.close()
