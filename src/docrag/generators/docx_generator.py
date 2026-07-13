"""Генерация брендированных .docx (справка / выписка / проект ответа) из RAG-ответа.

Типографика приближена к ГОСТ Р 7.0.97-2016 (Times New Roman 12pt, интервал 1.5,
поля 30/15/20/20 мм, красная строка). Шапка с логотипом и нумерация страниц — в
колонтитулах (повторяются на каждой странице). Источники — таблицей с текстом нормы
(snippet) для проверяемости. Ссылки «Документ, п. X.Y, стр. N» детерминированы из payload.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from docrag import branding as B
from docrag.common.models import Answer
from docrag.settings import settings

DOC_TYPES = {
    "spravka": "СПРАВКА",
    "vypiska": "ВЫПИСКА ИЗ НОРМАТИВНЫХ ДОКУМЕНТОВ",
    "proekt_otveta": "ПРОЕКТ ОТВЕТА",
}

_BASE_FONT = "Times New Roman"

_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
           "августа", "сентября", "октября", "ноября", "декабря"]


def _ru_date(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year} г."


def _rgb(hexcolor: str) -> RGBColor:
    r, g, b = B.hex_to_rgb(hexcolor)
    return RGBColor(r, g, b)


def _set_cell_bg(cell, hexcolor: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hexcolor.lstrip("#"))
    # порядок CT_TcPr: w:shd идёт после tcW/tcBorders, но раньше noWrap/tcMar/vAlign
    tcPr.insert_element_before(
        shd, "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign",
        "w:hideMark", "w:cellIns", "w:cellDel", "w:cellMerge", "w:tcPrChange",
    )


def _set_cell_width(cell, width: Cm) -> None:
    # python-docx сам ставит w:tcW в корректную позицию CT_TcPr; надёжно на КАЖДОЙ ячейке
    cell.width = width


def _field(paragraph, instr: str, cached: str = "1") -> None:
    """Вычисляемое поле Word (PAGE / NUMPAGES) с кэшированным результатом, чтобы не-Word
    вьюеры (macOS Preview/Quick Look, LibreOffice) показывали номер сразу, до пересчёта полей."""
    r1 = paragraph.add_run()
    begin = OxmlElement("w:fldChar"); begin.set(qn("w:fldCharType"), "begin")
    instr_el = OxmlElement("w:instrText"); instr_el.set(qn("xml:space"), "preserve"); instr_el.text = instr
    sep = OxmlElement("w:fldChar"); sep.set(qn("w:fldCharType"), "separate")
    r1._r.append(begin); r1._r.append(instr_el); r1._r.append(sep)
    paragraph.add_run(cached)  # кэшированный результат поля
    r3 = paragraph.add_run()
    end = OxmlElement("w:fldChar"); end.set(qn("w:fldCharType"), "end")
    r3._r.append(end)


def _base_font(doc: Document) -> None:
    style = doc.styles["Normal"]
    style.font.name = _BASE_FONT
    style.font.size = Pt(12)
    style.font.color.rgb = _rgb(B.BRAND["ink"])
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:cs"), _BASE_FONT)
    rfonts.set(qn("w:eastAsia"), _BASE_FONT)
    pf = style.paragraph_format
    pf.line_spacing = 1.5
    pf.space_after = Pt(0)


def _accent_border(paragraph, size: str = "18") -> None:
    pPr = paragraph._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single"); bottom.set(qn("w:sz"), size)
    bottom.set(qn("w:space"), "1"); bottom.set(qn("w:color"), B.BRAND["accent"].lstrip("#"))
    pbdr.append(bottom)
    # порядок CT_PPr: w:pBdr обязан идти раньше shd/spacing/ind/jc — вставляем в нужную позицию,
    # иначе строгие ридеры (Preview/Quick Look) считают XML невалидным
    pPr.insert_element_before(
        pbdr, "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap",
        "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi",
        "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing",
        "w:mirrorIndents", "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
        "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
    )


def _setup_header(section, subtitle: str) -> None:
    """Шапка в колонтитуле: логотип + название организации + акцентная линия.
    Повторяется на каждой странице."""
    header = section.header
    header.is_linked_to_previous = False
    table = header.add_table(rows=1, cols=2, width=Cm(16.5))
    table.autofit = False
    logo_cell, txt_cell = table.rows[0].cells
    _set_cell_width(logo_cell, Cm(3.0))
    _set_cell_width(txt_cell, Cm(13.5))
    logo_cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    if B.LOGO_PNG.exists():
        logo_cell.paragraphs[0].add_run().add_picture(str(B.LOGO_PNG), width=Cm(2.6))
    else:
        lr = logo_cell.paragraphs[0].add_run(B.ORG_NAME)
        lr.bold = True; lr.font.color.rgb = _rgb(B.BRAND["primary"])
    txt_cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    p1 = txt_cell.paragraphs[0]
    r1 = p1.add_run(B.ORG_FULL)
    r1.bold = True; r1.font.size = Pt(11); r1.font.color.rgb = _rgb(B.BRAND["primary"])
    p2 = txt_cell.add_paragraph()
    r2 = p2.add_run(subtitle)
    r2.font.size = Pt(8.5); r2.font.color.rgb = _rgb(B.BRAND["muted"])
    _accent_border(header.add_paragraph())


def _setup_footer(section) -> None:
    """Нумерация страниц «стр. N из M» + название организации."""
    footer = section.footer
    footer.is_linked_to_previous = False
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pre = p.add_run("стр. "); pre.font.size = Pt(9); pre.font.color.rgb = _rgb(B.BRAND["muted"])
    _field(p, "PAGE")
    mid = p.add_run(" из "); mid.font.size = Pt(9); mid.font.color.rgb = _rgb(B.BRAND["muted"])
    _field(p, "NUMPAGES")
    for r in p.runs:
        r.font.size = Pt(9); r.font.color.rgb = _rgb(B.BRAND["muted"])


def _title(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(10)
    r = p.add_run(text)
    r.bold = True; r.font.size = Pt(14); r.font.color.rgb = _rgb(B.BRAND["ink"])


def _label_value(doc: Document, label: str, value: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    rl = p.add_run(f"{label}: ")
    rl.bold = True; rl.font.color.rgb = _rgb(B.BRAND["slate"])
    p.add_run(value)


def _body_paragraph(doc: Document, text: str, *, indent: bool = True):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_after = Pt(6)
    pf.line_spacing = 1.5
    pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if indent:
        pf.first_line_indent = Cm(1.25)
    p.add_run(text)
    return p


def _body(doc: Document, answer: Answer, doc_type: str) -> None:
    if not answer.grounded:
        p = doc.add_paragraph()
        r = p.add_run("⚠ " + answer.answer)
        r.font.color.rgb = _rgb(B.BRAND["slate"]); r.italic = True
        return
    for para in re.split(r"\n{2,}", answer.answer.strip()):
        para = para.strip()
        if para:
            _body_paragraph(doc, para)
    # ВЫПИСКА: дословно приводим текст пунктов (snippet) — это суть выписки
    if doc_type == "vypiska" and answer.citations:
        doc.add_paragraph()
        h = doc.add_paragraph(); hr = h.add_run("Извлечения из документов")
        hr.bold = True; hr.font.size = Pt(12); hr.font.color.rgb = _rgb(B.BRAND["primary"])
        for c in answer.citations:
            lead = doc.add_paragraph()
            lr = lead.add_run(c.ref()); lr.bold = True; lr.font.size = Pt(10.5)
            lr.font.color.rgb = _rgb(B.BRAND["ink"])
            q = doc.add_paragraph()
            q.paragraph_format.left_indent = Cm(1.0); q.paragraph_format.space_after = Pt(8)
            qr = q.add_run(f"«{(c.snippet or '').strip()}»")
            qr.italic = True; qr.font.color.rgb = _rgb(B.BRAND["slate"])


def _sources_table(doc: Document, answer: Answer) -> None:
    doc.add_paragraph()
    h = doc.add_paragraph()
    hr = h.add_run("Источники")
    hr.bold = True; hr.font.size = Pt(12); hr.font.color.rgb = _rgb(B.BRAND["primary"])
    if not answer.citations:
        p = doc.add_paragraph()
        r = p.add_run("Проверяемые ссылки на нормы отсутствуют (ответ не основан на конкретных пунктах).")
        r.italic = True; r.font.size = Pt(10); r.font.color.rgb = _rgb(B.BRAND["muted"])
        return
    table = doc.add_table(rows=1, cols=3)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    widths = (Cm(1.4), Cm(6.1), Cm(9.0))
    hdr = table.rows[0].cells
    for cell, title in zip(hdr, ("№", "Ссылка на норму", "Текст (извлечение)")):
        _set_cell_bg(cell, B.BRAND["primary"])
        cp = cell.paragraphs[0]
        cr = cp.add_run(title); cr.bold = True; cr.font.size = Pt(10)
        cr.font.color.rgb = _rgb(B.BRAND["white"])
    for i, cell in enumerate(hdr):
        _set_cell_width(cell, widths[i])
    for c in answer.citations:
        row = table.add_row().cells
        for i, w in enumerate(widths):
            _set_cell_width(row[i], w)
        rm = row[0].paragraphs[0].add_run(c.marker)
        rm.bold = True; rm.font.size = Pt(10); rm.font.color.rgb = _rgb(B.BRAND["primary"])
        rr = row[1].paragraphs[0].add_run(c.ref())
        rr.font.size = Pt(9.5); rr.font.color.rgb = _rgb(B.BRAND["ink"])
        if c.section_path:
            sp = row[1].add_paragraph().add_run(c.section_path)
            sp.italic = True; sp.font.size = Pt(8.5); sp.font.color.rgb = _rgb(B.BRAND["muted"])
        snippet = (c.snippet or "").strip()
        if len(snippet) > 500:
            snippet = snippet[:500].rstrip() + "…"
        sr = row[2].paragraphs[0].add_run(snippet)
        sr.font.size = Pt(9); sr.font.color.rgb = _rgb(B.BRAND["slate"])


def _signature(doc: Document) -> None:
    doc.add_paragraph()
    doc.add_paragraph()
    table = doc.add_table(rows=1, cols=3)
    table.autofit = False
    cells = table.rows[0].cells
    widths = (Cm(6.0), Cm(4.0), Cm(6.0))
    for i, w in enumerate(widths):
        _set_cell_width(cells[i], w)
    r0 = cells[0].paragraphs[0].add_run(B.SIGN_ROLE)
    r0.font.size = Pt(11)
    sig = cells[1].paragraphs[0]; sig.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _accent_border(sig, size="6")
    cap = cells[1].add_paragraph(); cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cr = cap.add_run("подпись"); cr.font.size = Pt(8); cr.font.color.rgb = _rgb(B.BRAND["muted"])
    fio = cells[2].paragraphs[0]; fio.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _accent_border(fio, size="6")
    fcap = cells[2].add_paragraph(); fcap.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    fr = fcap.add_run("И. О. Фамилия"); fr.font.size = Pt(8); fr.font.color.rgb = _rgb(B.BRAND["muted"])


def _requisites(doc: Document) -> None:
    req = B.REQUISITES
    parts = []
    if req.get("address"): parts.append(req["address"])
    if req.get("inn"): parts.append(f"ИНН {req['inn']}")
    if req.get("ogrn"): parts.append(f"ОГРН {req['ogrn']}")
    if req.get("phone"): parts.append(f"тел. {req['phone']}")
    if req.get("email"): parts.append(req["email"])
    if req.get("site"): parts.append(req["site"])
    if not parts:
        return
    doc.add_paragraph()
    p = doc.add_paragraph(); _accent_border(p, size="4")
    r = p.add_run(" · ".join(parts))
    r.font.size = Pt(8.5); r.font.color.rgb = _rgb(B.BRAND["muted"])


def _disclaimer(doc: Document) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    r = p.add_run(
        "Документ подготовлен ИИ-ассистентом на основе действующих нормативных документов "
        "организации и носит справочный характер. Перед использованием требуется проверка "
        "уполномоченным сотрудником."
    )
    r.italic = True; r.font.size = Pt(8); r.font.color.rgb = _rgb(B.BRAND["muted"])


def build_docx(
    answer: Answer,
    doc_type: str = "spravka",
    subject: str | None = None,
    out_path: Path | None = None,
) -> Path:
    doc = Document()
    _base_font(doc)
    for section in doc.sections:
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.left_margin = Cm(3.0)     # ГОСТ: под подшивку
        section.right_margin = Cm(1.5)
        section.header_distance = Cm(1.0)
        section.footer_distance = Cm(1.0)
        _setup_header(section, B.PRODUCT_NAME)
        _setup_footer(section)

    _title(doc, DOC_TYPES.get(doc_type, "СПРАВКА"))

    reg_no = f"№ ИИ-{datetime.now():%Y%m%d-%H%M%S}"
    _label_value(doc, "Регистрационный номер", reg_no)
    _label_value(doc, "Дата", _ru_date(date.today()))
    _label_value(doc, "Тема", subject or answer.question)
    doc.add_paragraph()

    _body(doc, answer, doc_type)
    _sources_table(doc, answer)
    _signature(doc)
    _requisites(doc)
    _disclaimer(doc)

    if out_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = settings.data_dir / "generated" / f"{doc_type}_{stamp}_{uuid.uuid4().hex[:8]}.docx"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path
