"""Парсинг PDF -> ParsedDocument (текст + иерархия пунктов + метаданные).

Основной путь — PyMuPDF (нативный текст). Для сканов (страницы-изображения без
текстового слоя) — опциональный OCR через RapidOCR (rapidocr-onnxruntime).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

import fitz  # PyMuPDF

from docrag.common.logging import get_logger
from docrag.common.models import DocumentMeta, ParsedDocument
from docrag.parser.clean import clean_pages
from docrag.parser.structure import parse_structure
from docrag.settings import settings

log = get_logger("parser.pdf")

_MIN_NATIVE_CHARS = 50  # меньше — считаем страницу сканом
_OCR_ENGINE = None
_OCR_KIND = None  # "vision" | "rapidocr"


def _init_ocr() -> str:
    """Выбирает OCR-движок. Приоритет — macOS Vision (ocrmac): лучший русский на Mac."""
    global _OCR_ENGINE, _OCR_KIND
    if _OCR_KIND is not None:
        return _OCR_KIND
    try:
        from ocrmac import ocrmac  # noqa: F401

        _OCR_KIND = "vision"
        log.info("OCR-движок: macOS Vision (ocrmac, ru-RU)")
        return _OCR_KIND
    except Exception:  # noqa: BLE001
        pass
    try:
        from rapidocr_onnxruntime import RapidOCR

        _OCR_ENGINE = RapidOCR()
        _OCR_KIND = "rapidocr"
        log.warning("OCR-движок: RapidOCR (кириллица распознаётся хуже, чем Vision)")
        return _OCR_KIND
    except ImportError:
        raise RuntimeError(
            "OCR недоступен. Установите: uv sync --extra ocr (даёт ocrmac для macOS Vision)"
        ) from None


def _pixmap_to_pil(pix):
    from PIL import Image

    mode = "RGBA" if pix.n == 4 else "RGB"
    img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    return img.convert("RGB")


def _ocr_page(page: "fitz.Page", dpi: int = 220) -> str:
    kind = _init_ocr()
    pix = page.get_pixmap(dpi=dpi)
    if kind == "vision":
        from ocrmac import ocrmac

        img = _pixmap_to_pil(pix)
        ann = ocrmac.OCR(img, language_preference=["ru-RU"]).recognize()
        # Vision: bbox = (x, y, w, h), origin — нижний левый угол; сортируем сверху вниз
        ann_sorted = sorted(ann, key=lambda a: (-round(a[2][1], 3), round(a[2][0], 3)))
        return "\n".join(a[0] for a in ann_sorted)
    # RapidOCR fallback
    import numpy as np

    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = arr[:, :, :3]
    result, _ = _OCR_ENGINE(arr)
    return "\n".join(line[1] for line in result) if result else ""


def _docling_extract_pages(pdf_path: Path, ocr: bool) -> tuple[list[str], list[str]]:
    """Извлечение через Docling: таблицы → markdown (сохраняет структуру колонок),
    текст — в порядке чтения, с разбивкой по страницам (для цитат «стр. N»).

    Требует `uv sync --extra docling`. Бросает исключение при недоступности —
    вызывающая сторона делает откат на PyMuPDF.
    """
    from collections import defaultdict

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import TableItem, TextItem

    opts = PdfPipelineOptions()
    opts.do_table_structure = True     # ← ради этого Docling и нужен: распознаёт таблицы
    opts.do_ocr = ocr
    conv = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    dl = conv.convert(str(pdf_path)).document
    n_pages = len(dl.pages) or 1
    per_page: dict[int, list[str]] = defaultdict(list)
    # iterate_items идёт в порядке чтения — сортировать по координатам не нужно
    for item, _lvl in dl.iterate_items():
        prov = getattr(item, "prov", None)
        page_no = prov[0].page_no if prov else 1
        if isinstance(item, TableItem):
            try:
                md = item.export_to_markdown(dl).strip()
            except Exception:  # noqa: BLE001 — сбой рендера одной таблицы не роняет документ
                md = ""
            if md:
                per_page[page_no].append(md)
        elif isinstance(item, TextItem):
            t = (item.text or "").strip()
            if t:
                per_page[page_no].append(t)
    pages = ["\n".join(per_page.get(p, [])) for p in range(1, n_pages + 1)]
    log.info("Docling: %d страниц, таблиц распознано в тексте (markdown)", n_pages)
    return pages, ["парсер: docling"]


def extract_pages(
    pdf_path: Path,
    ocr: bool = True,
    on_page: Callable[[int, int, bool], None] | None = None,
) -> tuple[list[str], list[str]]:
    """Возвращает (тексты страниц, предупреждения). OCR — только для страниц-сканов.

    Если задан ``on_page`` — после каждой страницы зовём ``on_page(номер, всего, ocr)``,
    где ``ocr`` — применялось ли распознавание (для постраничного индикатора в UI).
    """
    # Бэкенд Docling (опционально): лучше таблицы. При любом сбое — откат на PyMuPDF ниже.
    if settings.parser_backend == "docling":
        try:
            pages, warnings = _docling_extract_pages(pdf_path, ocr)
            if on_page:
                for i in range(len(pages)):
                    on_page(i + 1, len(pages), False)
            return pages, warnings
        except Exception as e:  # noqa: BLE001
            log.warning("Docling недоступен/сбой (%s) — откат на PyMuPDF", e)

    warnings: list[str] = []
    doc = fitz.open(pdf_path)
    n_pages = doc.page_count
    pages: list[str] = []
    ocr_used = 0
    try:
        for i, page in enumerate(doc):
            txt = page.get_text("text")
            page_ocr = False
            if len(txt.strip()) < _MIN_NATIVE_CHARS:
                if ocr:
                    try:
                        txt = _ocr_page(page)
                        ocr_used += 1
                        page_ocr = True
                    except RuntimeError as e:  # OCR недоступен вообще — отключаем
                        warnings.append(str(e))
                        ocr = False
                    except Exception as e:  # noqa: BLE001 — сбой на странице: пропускаем её, не роняя батч
                        warnings.append(f"стр. {i + 1}: ошибка OCR ({e})")
                        txt = ""
                else:
                    warnings.append(f"стр. {i + 1}: нет текстового слоя (скан), OCR выключен")
            pages.append(txt)
            if on_page:
                on_page(i + 1, n_pages, page_ocr)
    finally:
        doc.close()
    if ocr_used:
        log.info("OCR применён к %d страницам %s", ocr_used, pdf_path.name)
        warnings.append(f"OCR применён к {ocr_used} страницам")
    return pages, warnings


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def parse_pdf(
    record: dict,
    ocr: bool = True,
    on_page: Callable[[int, int, bool], None] | None = None,
) -> ParsedDocument:
    """record — запись из data/raw/manifest.json. ``on_page`` — постраничный прогресс (см. extract_pages)."""
    pdf_path = Path(record["local_path"])
    log.info("Парсинг %s (%s)", record["doc_id"], pdf_path.name)
    raw_pages, warnings = extract_pages(pdf_path, ocr=ocr, on_page=on_page)
    cleaned = clean_pages(raw_pages)
    pages_indexed = [(i + 1, t) for i, t in enumerate(cleaned)]
    nodes = parse_structure(record["doc_id"], pages_indexed)

    meta = DocumentMeta(
        doc_id=record["doc_id"],
        title=record.get("title") or record["doc_id"],
        doc_type=record.get("doc_type", "regulation"),
        source_url=record.get("url"),
        file_name=record.get("file_name"),
        sha256=record.get("sha256"),
        n_pages=len(cleaned),
        effective_date=_parse_date(record.get("doc_date")),
        version_note=(f"протокол {record['protocol']}" if record.get("protocol") else None),
    )
    doc = ParsedDocument(
        meta=meta,
        full_text="\n\n".join(cleaned),
        nodes=nodes,
        page_texts=cleaned,
        parser=(
            "docling" if any("docling" in w for w in warnings)
            else "pymupdf+ocr" if any("OCR" in w for w in warnings)
            else "pymupdf"
        ),
        warnings=warnings,
    )
    log.info(
        "  %s: %d страниц, %d пунктов%s",
        record["doc_id"], len(cleaned), len(nodes),
        f", предупреждений: {len(warnings)}" if warnings else "",
    )
    return doc
