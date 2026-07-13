"""Разбор иерархии нормативного документа по точечной нумерации пунктов.

Нумерация вида 1. / 1.1. / 5.3.11.3. — уровень = число компонентов.
Верхний уровень (1., 2., ...) трактуется как раздел с заголовком.
Каждому узлу сопоставляется страница (для цитат «стр. N»).
"""

from __future__ import annotations

import re

from docrag.common.models import SectionNode

# Первый компонент 1-2 цифры (номер раздела), далее до 5 вложений; строка ДОЛЖНА
# оканчиваться точкой номера. Исключает даты (2026) и большие числа (50 000).
_CLAUSE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,5})\.\s*(.*)$")
_DOT_LEADER = re.compile(r"\.{4,}")
_APPENDIX = re.compile(r"(?i)^\s*приложение\b")
_MIN_PREAMBLE_CHARS = 40   # преамбулу короче — не превращаем в узел (титул из пары слов)


def _is_appendix_header(ln: str) -> bool:
    """Заголовок приложения — короткая строка, а не предложение, начинающееся с «Приложение...»."""
    s = ln.strip()
    if not _APPENDIX.match(s) or len(s) > 90:
        return False
    # либо «Приложение № N ...», либо заголовок без завершающей точки предложения
    return bool(re.match(r"(?i)^приложение\s*(№|n)?\s*\d", s)) or not s.rstrip().endswith(".")


def _looks_like_title(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 140:
        return False
    # заголовок обычно не заканчивается «обычной» пунктуацией предложения
    return not s.endswith((".", ";", ",", ":")) or s.isupper()


def _remove_toc(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Отрезает блок оглавления и строки с точками-лидерами."""
    # индекс ОГЛАВЛЕНИЕ/СОДЕРЖАНИЕ
    toc_start = None
    for i, (_p, ln) in enumerate(lines):
        if re.match(r"(?i)^\s*(ОГЛАВЛЕНИЕ|СОДЕРЖАНИЕ)\s*$", ln):
            toc_start = i
            break
    if toc_start is not None:
        # Расширяем регион оглавления ТОЛЬКО через непрерывный кластер строк-лидеров.
        # Как только после последней точки-лидера прошёл большой разрыв (реальный текст
        # начался) — останавливаемся, чтобы не срезать контент между оглавлением и телом.
        last_leader = toc_start
        gap = 0
        for j in range(toc_start + 1, len(lines)):
            if _DOT_LEADER.search(lines[j][1]):
                last_leader = j
                gap = 0
            else:
                gap += 1
                if gap > 25:  # 25 строк без лидеров подряд → оглавление кончилось
                    break
        lines = lines[:toc_start] + lines[last_leader + 1:]
    # на всякий случай выкидываем оставшиеся строки-лидеры
    return [(p, ln) for (p, ln) in lines if not _DOT_LEADER.search(ln)]


def _parent_number(number: str) -> str | None:
    parts = number.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else None


def parse_structure(doc_id: str, pages: list[tuple[int, str]]) -> list[SectionNode]:
    # разворачиваем страницы в поток строк с номером страницы
    lines: list[tuple[int, str]] = []
    for page_no, text in pages:
        for ln in text.split("\n"):
            if ln.strip():
                lines.append((page_no, ln))
    lines = _remove_toc(lines)

    nodes: list[SectionNode] = []
    stack: list[SectionNode | None] = []   # stack[i] = открытый узел уровня i
    current: SectionNode | None = None
    body_lines: list[str] = []
    top_seen = 0            # макс. принятый номер раздела (монотонная защита)
    appendix_idx = 0        # счётчик приложений (рестарт нумерации)
    seq = 0                 # глобальный счётчик — гарантирует уникальность node_id
    preamble_lines: list[str] = []          # текст до первого пункта (титул, сводка, вводные данные)
    preamble_page: int | None = None

    def flush():
        if current is not None:
            current.text = _finalize_text(current, body_lines)

    for page_no, ln in lines:
        if _is_appendix_header(ln):
            appendix_idx += 1
            top_seen = 0
            stack = []       # приложение открывает новый скоуп вложенности
        m = _CLAUSE.match(ln)
        if m:
            number, rest = m.group(1), m.group(2).strip()
            level = number.count(".")
            # Защита от ложных верхнеуровневых номеров (сноски/ячейки таблиц):
            # регрессия «N < уже виденного» вне приложения — артефакт, трактуем как текст.
            if level == 0:
                n_int = int(number)
                if n_int < top_seen and appendix_idx == 0:
                    if current is not None:
                        current.page_end = page_no
                        body_lines.append(ln.strip())
                    continue
                top_seen = max(top_seen, n_int)
            # родитель = ближайший открытый узел уровнем выше (по документу)
            parent = stack[level - 1] if level >= 1 and len(stack) >= level and stack[level - 1] else None
            flush()
            body_lines = []
            seq += 1
            node = SectionNode(
                node_id=f"{doc_id}#{seq}:{number}",   # seq делает id уникальным
                level=level,
                number=number,
                page_start=page_no,
                page_end=page_no,
                parent_id=parent.node_id if parent else None,
            )
            if appendix_idx:
                node.path_titles = [f"Приложение № {appendix_idx}"]  # дополнится в _assign
            nodes.append(node)
            current = node
            # обновляем стек: обрезаем до level, добиваем None, кладём узел на индекс level
            del stack[level:]
            while len(stack) < level:
                stack.append(None)
            stack.append(node)
            if rest:
                body_lines.append(rest)
        else:
            if current is not None:
                current.page_end = page_no
                body_lines.append(ln.strip())
            else:
                # Текст до первого пункта (титул, аннотация, сводные таблицы) раньше терялся —
                # для ненормативных/табличных документов там и есть ответ. Копим в преамбулу.
                if preamble_page is None:
                    preamble_page = page_no
                preamble_lines.append(ln.strip())
    flush()

    # Преамбулу — в отдельный узел «Вводная часть» (без номера пункта), чтобы она попала
    # в индекс и цитировалась как «<Документ>, стр. N».
    pre_text = " ".join(x for x in preamble_lines if x).strip()
    if len(pre_text) >= _MIN_PREAMBLE_CHARS:
        seq += 1
        pre = SectionNode(
            node_id=f"{doc_id}#{seq}:preamble",
            level=0,
            number=None,
            title="Вводная часть",
            text=pre_text,
            page_start=preamble_page,
            page_end=preamble_page,
        )
        nodes.insert(0, pre)

    _assign_titles_and_paths(nodes)
    return nodes


def _finalize_text(node: SectionNode, body_lines: list[str]) -> str:
    return " ".join(x for x in body_lines if x).strip()


def _clause_title(node: SectionNode) -> str | None:
    """Короткий заголовок пункта = первое «заголовкоподобное» предложение тела."""
    if not node.text:
        return None
    first = re.split(r"(?<=[.;])\s", node.text, maxsplit=1)[0]
    if _looks_like_title(first):
        return first.strip()
    return None


def _assign_titles_and_paths(nodes: list[SectionNode]) -> None:
    by_id = {n.node_id: n for n in nodes}
    for node in nodes:
        # не затираем заранее заданный заголовок (напр. «Вводная часть» у преамбулы)
        node.title = _clause_title(node) or node.title
    for node in nodes:
        # приложение-префикс (если был установлен на этапе разбора)
        appendix_prefix = node.path_titles[:1] if node.path_titles else []
        chain: list[str] = []
        cur: SectionNode | None = node
        guard = 0
        while cur is not None and guard < 12:
            if cur.number:
                label = f"п. {cur.number}" if cur.level >= 1 else f"Раздел {cur.number}"
                if cur.title:
                    label += f". {cur.title}"
            else:
                label = cur.title or cur.citation_label()   # узел без номера (преамбула)
            chain.append(label)
            cur = by_id.get(cur.parent_id) if cur.parent_id else None
            guard += 1
        node.path_titles = appendix_prefix + list(reversed(chain))
