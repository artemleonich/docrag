"""Автотесты RAG на контрольном наборе. Требуют поднятого Qdrant и Ollama с индексом."""

from __future__ import annotations

import pytest

from docrag.eval import run_eval
from docrag.parser.clean import fix_hyphenation, normalize_whitespace
from docrag.parser.structure import parse_structure


def test_cyrillic_cleaning_preserves_text():
    # кириллице-безопасная очистка не должна терять русский текст
    text = "доку-\nмент   организации"
    out = normalize_whitespace(fix_hyphenation(text))
    assert "документ" in out        # перенос по дефису склеен
    assert "организации" in out     # русский текст не потерян


def test_structure_parses_dotted_clauses():
    pages = [(1, "1. Общие положения\n1.1. Термин определяется так.\n1.2. Другой пункт.\n2. Второй раздел")]
    nodes = parse_structure("doc", pages)
    numbers = {n.number for n in nodes}
    assert {"1", "1.1", "1.2", "2"}.issubset(numbers)
    # уровни вложенности
    lvl = {n.number: n.level for n in nodes}
    assert lvl["1"] == 0 and lvl["1.1"] == 1


@pytest.mark.slow
def test_golden_set_quality():
    """Сквозная оценка на golden-set (медленно: гоняет весь пайплайн)."""
    res = run_eval(verbose=False)
    s = res["summary"]
    # грундинг: отвечаемые отвечаются, неотвечаемые отклоняются
    assert s["grounding_accuracy"] >= 0.85, f"grounding={s['grounding_accuracy']}"
    # ретрив: нужный документ попадает в цитаты
    assert s["doc_hit_rate"] >= 0.75, f"doc_hit={s['doc_hit_rate']}"
