"""Оценка качества RAG на контрольном наборе (golden-set).

Метрики (автоматические):
- grounding_correct: отвечаемые → grounded=True; неотвечаемые → grounded=False (отказ).
- doc_hit@k: среди цитат есть ожидаемый документ.
- clause_hit: если в golden указан пункт — он присутствует среди цитат.
Точность формулировок и корректность пункта требуют финального sign-off юриста.
"""

from __future__ import annotations

import json
from pathlib import Path

from docrag.common.logging import get_logger
from docrag.rag.pipeline import answer
from docrag.settings import REPO_ROOT

log = get_logger("eval")

GOLDEN = REPO_ROOT / "tests" / "golden.json"


def run_eval(path: Path | None = None, verbose: bool = True) -> dict:
    data = json.loads((path or GOLDEN).read_text(encoding="utf-8"))
    items = data["questions"]
    results = []
    grounding_ok = doc_hit = clause_hit = clause_total = answerable = 0

    for it in items:
        a = answer(it["q"])
        cited_docs = {c.doc_id for c in a.citations}
        cited_clauses = {c.clause for c in a.citations}
        is_unanswerable = it["type"] == "unanswerable"

        g_ok = (a.grounded is False) if is_unanswerable else (a.grounded is True)
        grounding_ok += int(g_ok)

        d_hit = None
        if not is_unanswerable:
            answerable += 1
            d_hit = it["doc"] in cited_docs
            doc_hit += int(bool(d_hit))
            if it.get("clause"):
                clause_total += 1
                c_hit = it["clause"] in cited_clauses
                clause_hit += int(c_hit)

        results.append({
            "q": it["q"], "type": it["type"], "grounded": a.grounded,
            "grounding_ok": g_ok, "expected_doc": it.get("doc"),
            "cited_docs": sorted(cited_docs), "doc_hit": d_hit,
        })
        if verbose:
            mark = "✓" if g_ok and (is_unanswerable or d_hit) else "✗"
            log.info("%s [%s] %s → %s", mark, it["type"], it["q"][:56],
                     "отказ" if not a.grounded else ",".join(sorted(cited_docs)) or "—")

    n = len(items)
    summary = {
        "total": n,
        "grounding_accuracy": round(grounding_ok / n, 3),
        "doc_hit_rate": round(doc_hit / answerable, 3) if answerable else None,
        "clause_hit_rate": round(clause_hit / clause_total, 3) if clause_total else None,
        "answerable": answerable,
    }
    log.info("ИТОГ: %s", summary)
    return {"summary": summary, "results": results}
