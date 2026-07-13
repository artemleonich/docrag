"""Кириллице-безопасная очистка текста нормативных PDF.

КРИТИЧНО: никогда не применяем «remove non-ASCII» — это стёрло бы всю кириллицу.
Убираем артефакты вёрстки, повторяющиеся колонтитулы, оглавление; чиним переносы.
"""

from __future__ import annotations

import re
from collections import Counter

# Мягкий перенос и «висячие» переносы слов на границе строки
_SOFT_HYPHEN = "­"
_WORD_HYPHEN_BREAK = re.compile(r"([а-яёa-z])[\-­]\s*\n\s*([а-яё])")
_MULTISPACE = re.compile(r"[ \t ]+")
_DOT_LEADER = re.compile(r"\.{4,}")          # «........» в оглавлении
_TRAILING_DOTS_PAGENO = re.compile(r"\.{2,}\s*\d{1,4}\s*$")


def normalize_whitespace(text: str) -> str:
    text = text.replace(" ", " ").replace("\r", "\n")
    # схлопываем пробелы, но сохраняем переводы строк
    lines = [_MULTISPACE.sub(" ", ln).strip() for ln in text.split("\n")]
    return "\n".join(lines)


def fix_hyphenation(text: str) -> str:
    text = text.replace(_SOFT_HYPHEN, "")
    # склейка «слово-\nпродолжение» -> «словопродолжение» (только строчные-строчные)
    prev = None
    while prev != text:
        prev = text
        text = _WORD_HYPHEN_BREAK.sub(r"\1\2", text)
    return text


def find_repeated_lines(page_texts: list[str], min_fraction: float = 0.5) -> set[str]:
    """Строки, повторяющиеся на ≥ min_fraction страниц — колонтитулы/водяные знаки."""
    counter: Counter[str] = Counter()
    for pt in page_texts:
        seen = set()
        for ln in pt.split("\n"):
            s = ln.strip()
            if len(s) >= 8 and not s.isdigit():
                seen.add(s)
        counter.update(seen)
    threshold = max(2, int(len(page_texts) * min_fraction))
    return {line for line, cnt in counter.items() if cnt >= threshold}


def strip_headers_footers(page_text: str, repeated: set[str]) -> str:
    lines = page_text.split("\n")
    # Номер страницы стоит в колонтитуле — т.е. это ПЕРВАЯ или ПОСЛЕДНЯЯ непустая строка.
    # Голое число в середине контента — это данные (значение таблицы, напр. «Всего сделок: 11»,
    # число из пункта), его удалять нельзя.
    non_empty = [i for i, ln in enumerate(lines) if ln.strip()]
    edge = {non_empty[0], non_empty[-1]} if non_empty else set()
    out = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s in repeated:
            continue
        if s.isdigit() and len(s) <= 4 and i in edge:   # голый номер страницы (только с краю)
            continue
        out.append(ln)
    return "\n".join(out)


def strip_toc(text: str) -> str:
    """Удаляет блок оглавления (ОГЛАВЛЕНИЕ/СОДЕРЖАНИЕ ... последняя строка с точками-лидерами)."""
    m = re.search(r"(?im)^\s*(ОГЛАВЛЕНИЕ|СОДЕРЖАНИЕ)\s*$", text)
    if not m:
        return text
    start = m.start()
    # ищем последнюю строку с точками-лидерами в разумном окне после ОГЛАВЛЕНИЯ
    tail = text[m.end():]
    last_leader_end = m.end()
    for lm in _DOT_LEADER.finditer(tail):
        # конец этой строки
        nl = tail.find("\n", lm.end())
        last_leader_end = m.end() + (nl if nl != -1 else lm.end())
    return text[:start] + "\n" + text[last_leader_end:]


def drop_dot_leader_lines(text: str) -> str:
    out = []
    for ln in text.split("\n"):
        if _DOT_LEADER.search(ln):
            continue
        out.append(ln)
    return "\n".join(out)


def clean_pages(page_texts: list[str]) -> list[str]:
    """Постранично: убрать колонтитулы, починить переносы, нормализовать пробелы."""
    # На коротких документах (2–3 страницы) «повторяющаяся» строка может оказаться
    # содержательным текстом, а не колонтитулом — удаление колонтитулов пропускаем.
    repeated = find_repeated_lines(page_texts) if len(page_texts) >= 4 else set()
    cleaned = []
    for pt in page_texts:
        t = strip_headers_footers(pt, repeated)  # с пустым repeated убирает лишь номера страниц
        t = fix_hyphenation(t)
        t = normalize_whitespace(t)
        cleaned.append(t)
    return cleaned
