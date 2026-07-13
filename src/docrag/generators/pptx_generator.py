"""Генерация брендированной .pptx из RAG-ответа.

Каскад extract→render: LLM структурирует ответ в строгий JSON слайдов (по схеме,
temperature=0), затем python-pptx рендерит презентацию в фирменном стиле.
Слайды опираются только на текст ответа; последний слайд — источники (цитаты).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR, MSO_AUTO_SIZE
from pptx.util import Cm, Pt

from docrag import branding as B
from docrag.common.logging import get_logger
from docrag.common.models import Answer
from docrag.generators.schema import DECK_JSON_SCHEMA, Deck, Slide
from docrag.rag.llm import get_llm
from docrag.settings import settings

log = get_logger("gen.pptx")

_W, _H = Cm(33.867), Cm(19.05)  # 16:9


def _rgb(hexcolor: str) -> RGBColor:
    r, g, b = B.hex_to_rgb(hexcolor)
    return RGBColor(r, g, b)


# --------------------------------------------------------------------------- #
#  Шаг 1: LLM структурирует ответ в Deck
# --------------------------------------------------------------------------- #
_KEY_ALIASES = {
    "заголовок": "title", "название": "title", "тема": "title",
    "подзаголовок": "subtitle", "подпись": "subtitle",
    "слайды": "slides", "листы": "slides",
    "тезисы": "bullets", "пункты": "bullets", "буллеты": "bullets", "содержание": "bullets",
    "макет": "layout", "тип": "layout", "таблица": "table", "строки": "table",
}


def _normalize_keys(obj):
    """Приводит русские/альтернативные ключи JSON к каноническим английским."""
    if isinstance(obj, dict):
        return {_KEY_ALIASES.get(k.lower(), k): _normalize_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_keys(x) for x in obj]
    return obj


def _clean_bullet(text: str) -> str:
    # убрать маркеры цитат: [1], [1,2], [1, 2], [1-3], [1][2]
    text = re.sub(r"\[\s*\d+(?:\s*[,\-–]\s*\d+)*\s*\]", "", text)
    text = re.sub(r"^[\s•\-–—▸]+", "", text.strip())
    return re.sub(r"\s+", " ", text).strip()


def _clean_cell(text: str) -> str:
    # для ячеек таблицы убираем ТОЛЬКО маркеры цитат (не трогаем ведущий «-», это может быть данными)
    return re.sub(r"\[\s*\d+(?:\s*[,\-–]\s*\d+)*\s*\]", "", str(text)).strip()


def _build_material(answer: Answer) -> str:
    """Материал для слайдов = ответ + выдержки из документов (богаче, но грундинг сохранён)."""
    parts = [answer.answer.strip()]
    snippets = []
    for c in answer.citations[:4]:
        s = (c.snippet or "").strip()
        if s:
            snippets.append(f"— {s[:400]}")
    if snippets:
        parts.append("Выдержки из документов:\n" + "\n".join(snippets))
    material = "\n\n".join(parts)
    return material[:2400]


def answer_to_deck(answer: Answer, topic: str | None = None, model: str | None = None) -> Deck:
    llm = get_llm(model)
    system = (
        "Ты — эксперт по деловым презентациям. Преврати материал в структуру презентации.\n"
        "ПРАВИЛА:\n"
        "- 3–5 содержательных слайдов.\n"
        "- Каждый слайд: короткий заголовок (2–5 слов) и 2–4 тезиса.\n"
        "- Каждый тезис — короткая ёмкая фраза (до 12 слов), ОДНА мысль. Длинные предложения дроби на отдельные тезисы.\n"
        "- Используй ТОЛЬКО факты из материала, ничего не выдумывай. Без ссылок вида [1]. Без воды и повторов.\n"
        "- МАКЕТ (layout): 'bullets' по умолчанию; 'two_col' если 4+ параллельных пункта; "
        "'table' для числовых/тарифных данных — тогда заполни table массивом строк (первая строка — заголовки столбцов), bullets оставь пустым [].\n"
        "- Значения по-русски; КЛЮЧИ JSON строго английские: title, subtitle, slides (в каждом title, bullets; опц. layout, table).\n"
        "Пример: {\"title\":\"Заголовок\",\"subtitle\":\"Подзаголовок\",\"slides\":["
        "{\"title\":\"Раздел\",\"bullets\":[\"тезис\",\"тезис\"]},"
        "{\"title\":\"Показатели\",\"layout\":\"table\",\"bullets\":[],\"table\":[[\"Показатель\",\"Значение\"],[\"Метрика A\",\"10\"]]}]}"
    )
    user = (
        f"ТЕМА: {topic or answer.question}\n\n"
        f"МАТЕРИАЛ (источник фактов):\n{_build_material(answer)}\n\n"
        "Сформируй ёмкую, структурированную презентацию строго по этим фактам."
    )
    try:
        data = llm.chat_json(system, user, DECK_JSON_SCHEMA, max_tokens=1600)
        deck = Deck.model_validate(_normalize_keys(data))
        # чистим тезисы/ячейки от маркеров цитат, валидируем таблицы, отбрасываем пустые слайды
        for sl in deck.slides:
            sl.bullets = [b for b in (_clean_bullet(x) for x in sl.bullets) if b]
            if sl.table:
                sl.table = [[_clean_cell(c) for c in row] for row in sl.table
                            if any(str(c).strip() for c in row)]
                # некорректная таблица (<2 строк или рваные ряды) → откат в буллеты
                if len(sl.table) < 2 or len({len(r) for r in sl.table}) != 1:
                    sl.layout, sl.table = "bullets", []
            if sl.layout == "table" and not sl.table:
                sl.layout = "bullets"
            if sl.layout not in ("bullets", "two_col", "table"):
                sl.layout = "bullets"
        deck.slides = [sl for sl in deck.slides if sl.bullets or sl.table][:6]
        if deck.slides:
            return deck
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось получить JSON-схему слайдов (%s) — fallback", e)
    # Fallback: абзац → слайд; заголовок из первых слов, тело дробим по предложениям на тезисы
    slides: list[Slide] = []
    for para in re.split(r"\n{2,}", answer.answer):
        clean = _clean_bullet(para)
        if not clean:
            continue
        words = clean.split()
        title = " ".join(words[:5]) + ("…" if len(words) > 5 else "")
        sents = [s.strip() for s in re.split(r"(?<=[.;])\s+", clean) if s.strip()]
        bullets = [s[:160] for s in (sents[:4] if sents else [clean[:200]])]
        slides.append(Slide(title=title[:60], bullets=bullets))
        if len(slides) >= 5:
            break
    return Deck(title=topic or answer.question, subtitle=B.ORG_FULL, slides=slides or
               [Slide(title="Ответ", bullets=[_clean_bullet(answer.answer)[:200] or "—"])])


# --------------------------------------------------------------------------- #
#  Шаг 2: рендер брендированной .pptx
# --------------------------------------------------------------------------- #
def _blank(prs: Presentation):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _rect(slide, x, y, w, h, hexfill):
    from pptx.enum.shapes import MSO_SHAPE

    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    shp.fill.solid()
    shp.fill.fore_color.rgb = _rgb(hexfill)
    shp.line.fill.background()
    shp.shadow.inherit = False
    return shp


def _text(slide, x, y, w, h, text, size, color, *, bold=False, align=PP_ALIGN.LEFT,
          anchor=MSO_ANCHOR.TOP, font="Calibri"):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    p = tf.paragraphs[0]
    p.alignment = align
    r = p.add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.name = font
    r.font.color.rgb = _rgb(color)
    return box


def _logo_dark(slide, x, y, width):
    logo = B.LOGO_PNG_WHITE if B.LOGO_PNG_WHITE.exists() else B.LOGO_PNG
    if logo.exists():
        slide.shapes.add_picture(str(logo), x, y, width=width)


def _footer(slide, idx: int | None = None, total: int | None = None):
    _rect(slide, Cm(1.6), _H - Cm(1.35), Cm(30.6), Pt(1), B.BRAND["line"])
    _text(slide, Cm(1.6), _H - Cm(1.2), Cm(22), Cm(0.8),
          f"{B.ORG_NAME} · ИИ-ассистент", 9, B.BRAND["muted"])
    if idx is not None:
        _text(slide, _W - Cm(4), _H - Cm(1.2), Cm(2.4), Cm(0.8),
              f"{idx} / {total}", 9, B.BRAND["muted"], align=PP_ALIGN.RIGHT)


def _title_slide(prs, deck: Deck):
    s = _blank(prs)
    _rect(s, 0, 0, _W, _H, B.BRAND["primary"])
    # декоративные акценты
    _rect(s, 0, _H - Cm(0.55), _W, Cm(0.55), B.BRAND["accent"])
    _rect(s, Cm(2), Cm(6.2), Cm(1.4), Cm(0.16), B.BRAND["accent"])
    _logo_dark(s, Cm(2), Cm(1.8), Cm(6.2))
    # кегль титула — по длине, чтобы длинный заголовок не наезжал на подзаголовок
    tlen = len(deck.title)
    tsize = 34 if tlen <= 48 else (28 if tlen <= 80 else 22)
    _text(s, Cm(2), Cm(6.8), Cm(29.5), Cm(4.6), deck.title, tsize, B.BRAND["white"],
          bold=True, anchor=MSO_ANCHOR.TOP)
    if deck.subtitle:
        _text(s, Cm(2), Cm(11.7), Cm(29), Cm(2.2), deck.subtitle, 18, B.BRAND["subtitle"])
    _text(s, Cm(2), _H - Cm(2.4), Cm(29), Cm(1),
          f"{B.ORG_FULL} · {date.today().strftime('%d.%m.%Y')}", 11, B.BRAND["subtitle"])


def _hang(p, marL_cm: float = 0.85) -> None:
    """Висячий отступ: перенос строки тезиса встаёт под текст, а не под маркер «▸»."""
    pPr = p._p.get_or_add_pPr()
    pPr.set("marL", str(int(Cm(marL_cm))))
    pPr.set("indent", str(int(-Cm(marL_cm))))


def _bullet_size(bullets: list[str]) -> tuple[int, int]:
    """Кегль и отступ по СУММАРНОЙ длине текста (не только по числу буллетов) —
    защита от переполнения бокса длинными тезисами."""
    n = max(1, len(bullets))
    total = sum(len(b) for b in bullets)
    if total <= 160 and n <= 3:
        return 20, 14
    if total <= 320 and n <= 4:
        return 18, 12
    if total <= 520:
        return 16, 9
    return 14, 7


def _bullets_body(tf, bullets: list[str]):
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE  # PowerPoint дожмёт кегль при переполнении
    size, gap = _bullet_size(bullets)
    for i, b in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(gap)
        p.line_spacing = 1.15
        _hang(p)
        rb = p.add_run()
        rb.text = "▸  "; rb.font.size = Pt(size); rb.font.bold = True
        rb.font.color.rgb = _rgb(B.BRAND["accent"])
        rt = p.add_run()
        rt.text = b; rt.font.size = Pt(size); rt.font.name = "Calibri"
        rt.font.color.rgb = _rgb(B.BRAND["ink"])


def _table_body(s, rows: list[list[str]]):
    n_rows = len(rows)
    n_cols = max(len(r) for r in rows)
    x, y, w = Cm(2.0), Cm(3.8), Cm(29.8)
    h = Cm(min(13.0, 1.3 * n_rows))
    table = s.shapes.add_table(n_rows, n_cols, x, y, w, h).table
    for ci in range(n_cols):
        table.columns[ci].width = int(w / n_cols)
    for ri, row in enumerate(rows):
        for ci in range(n_cols):
            cell = table.cell(ri, ci)
            cell.text = row[ci] if ci < len(row) else ""
            cell.fill.solid()
            cell.fill.fore_color.rgb = _rgb(
                B.BRAND["primary"] if ri == 0 else (B.BRAND["bg"] if ri % 2 else B.BRAND["surface"]))
            for run in cell.text_frame.paragraphs[0].runs:
                run.font.size = Pt(14 if ri == 0 else 13)
                run.font.name = "Calibri"
                run.font.bold = ri == 0
                run.font.color.rgb = _rgb(B.BRAND["white"] if ri == 0 else B.BRAND["ink"])


def _content_slide(prs, slide: Slide, idx: int, total: int):
    s = _blank(prs)
    _rect(s, 0, 0, _W, Cm(2.6), B.BRAND["primary"])
    _rect(s, 0, Cm(2.6), _W, Cm(0.1), B.BRAND["accent"])
    tsize = 24 if len(slide.title) <= 42 else (20 if len(slide.title) <= 60 else 17)
    _text(s, Cm(1.6), Cm(0.35), Cm(26), Cm(1.9), slide.title, tsize, B.BRAND["white"],
          bold=True, anchor=MSO_ANCHOR.MIDDLE)
    _logo_dark(s, _W - Cm(5.2), Cm(0.85), Cm(3.4))
    if slide.table:  # валидная таблица приоритетнее layout — не теряем её и не рисуем пустой слайд
        _table_body(s, slide.table)
    elif slide.layout == "two_col" and len(slide.bullets) >= 2:
        mid = (len(slide.bullets) + 1) // 2
        _bullets_body(s.shapes.add_textbox(Cm(2.0), Cm(3.6), Cm(14.6), Cm(13.5)).text_frame, slide.bullets[:mid])
        _bullets_body(s.shapes.add_textbox(Cm(17.3), Cm(3.6), Cm(14.6), Cm(13.5)).text_frame, slide.bullets[mid:])
    else:
        _bullets_body(s.shapes.add_textbox(Cm(2.0), Cm(3.6), Cm(29.8), Cm(13.5)).text_frame, slide.bullets)
    _footer(s, idx, total)


_SRC_PER_SLIDE = 7


def _sources_slides(prs, answer: Answer):
    """Слайд(ы) источников с пагинацией — длинный список не обрезается."""
    if not answer.citations:
        return
    pages = [answer.citations[i:i + _SRC_PER_SLIDE]
             for i in range(0, len(answer.citations), _SRC_PER_SLIDE)]
    for pi, page in enumerate(pages, 1):
        s = _blank(prs)
        _rect(s, 0, 0, _W, Cm(2.6), B.BRAND["slate"])
        _rect(s, 0, Cm(2.6), _W, Cm(0.1), B.BRAND["accent"])
        title = "Источники" if len(pages) == 1 else f"Источники ({pi}/{len(pages)})"
        _text(s, Cm(1.6), Cm(0.35), Cm(26), Cm(1.9), title, 24, B.BRAND["white"],
              bold=True, anchor=MSO_ANCHOR.MIDDLE)
        _logo_dark(s, _W - Cm(5.2), Cm(0.85), Cm(3.4))
        box = s.shapes.add_textbox(Cm(2.0), Cm(3.6), Cm(29.8), Cm(12))
        tf = box.text_frame
        tf.word_wrap = True
        for i, c in enumerate(page):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.space_after = Pt(10)
            _hang(p, marL_cm=1.0)
            rm = p.add_run()
            rm.text = f"{c.marker}  "
            rm.font.size = Pt(15); rm.font.bold = True
            rm.font.color.rgb = _rgb(B.BRAND["primary"])
            r = p.add_run()
            r.text = c.ref()
            r.font.size = Pt(15); r.font.color.rgb = _rgb(B.BRAND["ink"])
        _text(s, Cm(2.0), _H - Cm(1.9), Cm(30), Cm(1),
              "Подготовлено ИИ-ассистентом организации на основе действующих нормативных документов · "
              "требует проверки уполномоченным сотрудником", 9, B.BRAND["muted"])


def build_pptx(answer: Answer, topic: str | None = None, out_path: Path | None = None,
               model: str | None = None) -> Path:
    deck = answer_to_deck(answer, topic, model)
    prs = Presentation()
    prs.slide_width = _W
    prs.slide_height = _H
    _title_slide(prs, deck)
    total = len(deck.slides)
    for i, sl in enumerate(deck.slides, 1):
        _content_slide(prs, sl, i, total)
    _sources_slides(prs, answer)

    # метаданные файла
    cp = prs.core_properties
    cp.title = deck.title
    cp.author = B.ORG_FULL
    cp.subject = topic or answer.question
    cp.category = "Презентация · ИИ-ассистент организации"

    if out_path is None:
        from datetime import datetime
        import uuid
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = settings.data_dir / "generated" / f"presentation_{stamp}_{uuid.uuid4().hex[:8]}.pptx"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))
    log.info("Презентация сохранена: %s (слайдов: %d)", out_path, len(prs.slides._sldIdLst))
    return out_path
