"""Каталогизация PDF-документов: обнаружение, классификация, версии.

Из имени файла/URL извлекаются тип документа, номер протокола и дата,
после чего для целевого субкорпуса выбирается ПОСЛЕДНЯЯ версия каждого семейства.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup


# --------------------------------------------------------------------------- #
#  Семейства целевых документов (targeted субкорпус)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DocFamily:
    key: str
    title: str            # человекочитаемое название для цитат
    doc_type: str         # категория
    pattern: re.Pattern   # по имени файла/URL
    target: bool          # входит ли в целевой субкорпус по умолчанию


# Пример семейств документов для целевого скрейпа. Замените под свой источник PDF:
# ключ (doc_id) · человекочитаемое название для цитат · категория · паттерн имени файла/URL.
FAMILIES: list[DocFamily] = [
    DocFamily("rules", "Правила", "rules",
              re.compile(r"rules|pravila|правил", re.I), True),
    DocFamily("tariffs", "Тарифы", "tariff",
              re.compile(r"tarif|тариф", re.I), True),
    DocFamily("charter", "Устав", "charter",
              re.compile(r"ustav|устав|charter", re.I), True),
    DocFamily("regulation", "Регламент", "regulation",
              re.compile(r"reglament|регламент|regulation", re.I), False),
]


# --------------------------------------------------------------------------- #
#  Кандидат PDF
# --------------------------------------------------------------------------- #
@dataclass
class PdfCandidate:
    url: str                       # абсолютный или относительный href (сырой)
    link_text: str = ""
    family: DocFamily | None = None
    protocol: int | None = None
    doc_date: date | None = None
    source_page: str = ""

    @property
    def clean_url(self) -> str:
        return self.url

    @property
    def file_name(self) -> str:
        path = urlparse(self.clean_url).path
        return unquote(path.rsplit("/", 1)[-1])

    def version_key(self) -> tuple:
        """Ключ сортировки версий: сначала протокол, затем дата."""
        return (self.protocol or 0, self.doc_date or date.min)


# --------------------------------------------------------------------------- #
#  Парсинг метаданных из имени/URL
# --------------------------------------------------------------------------- #
_PROTOCOL_RE = re.compile(r"protokol[_\-\s]*(\d{2,4})", re.I)
_DATE_PATTERNS = [
    re.compile(r"(\d{2})[._\-](\d{2})[._\-](\d{4})"),   # dd mm yyyy
    re.compile(r"(\d{4})[._\-](\d{2})[._\-](\d{2})"),   # yyyy mm dd
    re.compile(r"(\d{2})[._\-](\d{2})[._\-](\d{2})(?!\d)"),  # dd mm yy
]


def _parse_date(text: str) -> date | None:
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            g = [int(x) for x in m.groups()]
            try:
                if pat is _DATE_PATTERNS[1]:            # yyyy mm dd
                    y, mo, d = g
                else:
                    d, mo, y = g
                    if y < 100:
                        y += 2000
                if 1 <= mo <= 12 and 1 <= d <= 31 and 2000 <= y <= 2100:
                    return date(y, mo, d)
            except ValueError:
                continue
    return None


def parse_candidate_meta(cand: PdfCandidate) -> PdfCandidate:
    text = f"{cand.file_name} {cand.link_text}"
    # тип документа
    for fam in FAMILIES:
        if fam.pattern.search(text) or fam.pattern.search(cand.clean_url):
            cand.family = fam
            break
    # протокол
    m = _PROTOCOL_RE.search(text)
    if m:
        cand.protocol = int(m.group(1))
    # дата — из имени файла, затем из пути URL (папки вида /20.04.2026/)
    cand.doc_date = _parse_date(cand.file_name) or _parse_date(unquote(cand.clean_url))
    return cand


# --------------------------------------------------------------------------- #
#  Извлечение PDF-ссылок со страницы
# --------------------------------------------------------------------------- #
def extract_pdf_candidates(html: str, source_page: str) -> list[PdfCandidate]:
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[PdfCandidate] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        if ".pdf" not in low:
            continue
        cand = PdfCandidate(url=href, link_text=a.get_text(" ", strip=True), source_page=source_page)
        key = cand.clean_url
        if key in seen:
            continue
        seen.add(key)
        out.append(parse_candidate_meta(cand))
    return out


def rank_families(
    candidates: list[PdfCandidate],
    include_keys: set[str] | None = None,
) -> dict[str, list[PdfCandidate]]:
    """Для каждого целевого семейства — список версий от свежей к старой.

    Позволяет при отказе скачивания (напр. 401 на gated-URL) откатиться
    на следующую доступную версию того же документа с другого пути.
    """
    grouped: dict[str, list[PdfCandidate]] = {}
    for c in candidates:
        if c.family is None:
            continue
        if include_keys is not None:
            if c.family.key not in include_keys:
                continue
        elif not c.family.target:
            continue
        grouped.setdefault(c.family.key, []).append(c)
    for key in grouped:
        grouped[key].sort(key=lambda c: c.version_key(), reverse=True)
    return grouped


def select_targeted_subcorpus(
    candidates: list[PdfCandidate],
    include_keys: set[str] | None = None,
) -> list[PdfCandidate]:
    """Самая свежая версия каждого целевого семейства (для dry-run/обзора)."""
    return [versions[0] for versions in rank_families(candidates, include_keys).values()]
