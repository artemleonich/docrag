"""Ядро RAG: ретрив -> реранк -> retrieval-rail -> грундинг-генерация -> цитаты.

Гарантии качества:
- Гибридный dense+sparse поиск (sparse спасает номера пунктов).
- Cross-encoder реранк + порог отсечения (retrieval-rail).
- Дедупликация по (документ, пункт) — распределяем слоты узкого контекста.
- Small-to-big: в LLM подаётся родительский контекст пункта.
- alternating repack: сильнейшие фрагменты в начало и конец (анти lost-in-the-middle).
- Цитаты строятся ДЕТЕРМИНИРОВАННО из метаданных по маркерам [N] в ответе.
- Полный трейс каждого шага — для проверяемости.
"""

from __future__ import annotations

import json
import re
import time
from typing import Iterator

from docrag.common.embedder import embed_query
from docrag.common.logging import get_logger
from docrag.common.models import Answer, Citation, RetrievedChunk
from docrag.common.models import Chunk
from docrag.common.reranker import rerank
from docrag.indexer.qdrant_store import hybrid_search
from docrag.rag.llm import get_llm
from docrag.rag.prompts import REFUSAL, SYSTEM_QA, build_qa_user
from docrag.settings import settings

log = get_logger("rag.pipeline")

_MARKER = re.compile(r"\[(\d+)\]")

# --------------------------------------------------------------------------- #
#  Мета-вопросы о самом корпусе («какие документы есть», «что в базе»)
#  — их нельзя ответить поиском по СОДЕРЖАНИЮ, поэтому отвечаем из списка корпуса.
# --------------------------------------------------------------------------- #
_TYPE_LABEL = {"rules": "Правила", "tariff": "Тарифы", "charter": "Устав", "regulation": "Регламент"}


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        y, m, d = iso.split("-")
        return f"{d}.{m}.{y}"
    except ValueError:
        return iso


def _load_corpus_meta() -> list[dict]:
    out: list[dict] = []
    for path in sorted(settings.parsed_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        m = data.get("meta", {})
        out.append({
            "title": m.get("title", path.stem),
            "doc_type": m.get("doc_type", "regulation"),
            "n_pages": m.get("n_pages"),
            "effective_date": m.get("effective_date"),
            "scanned": "ocr" in data.get("parser", ""),
            "summary": m.get("summary"),
        })
    return out


def _is_corpus_meta(q: str) -> bool:
    """Вопрос про перечень документов в системе, а не про их содержание."""
    ql = " " + q.lower().replace("ё", "е") + " "
    # безусловные формулировки про перечень корпуса
    for p in ("список документ", "перечень документ", "сколько документ", "список нормативн",
              "перечень нормативн", "какие нормативные документ", "что за документ",
              "список файл", "какие файл"):
        if p in ql:
            return True
    # «контейнерная» ссылка на саму систему/базу + упоминание документов
    container = any(w in ql for w in (
        "в базе", "базе знаний", "в системе", "загружен", "доступн", "у тебя", "у вас",
        "ты знаешь", "ты работаешь", "с какими документ", "содержит", "проиндексирован",
        "в тебе", "по каким документ", "твоя база", "твоей базе"))
    doc_word = any(w in ql for w in ("документ", "файл", "нормативн"))
    if doc_word and container:
        return True
    # вопросы о возможностях/охвате
    for p in ("что в базе", "что в системе", "что ты умеешь", "чем ты можешь помочь",
              "о чем ты можешь", "что ты знаешь", "что вы знаете", "на какие вопросы",
              "о чем я могу", "что я могу спросить", "что можно спросить", "что можно узнать",
              "чем ты можешь быть полезен", "что ты можешь", "какие вопросы можно"):
        if p in ql:
            return True
    return False


def _corpus_answer(question: str) -> Answer:
    docs = _load_corpus_meta()
    if not docs:
        return Answer(question=question, answer=REFUSAL, grounded=False,
                      trace={"question": question, "route": "corpus_meta_empty"})
    lines = [f"В базе знаний — {len(docs)} нормативных документов организации (последние редакции):", ""]
    for d in sorted(docs, key=lambda x: x["title"]):
        parts = [_TYPE_LABEL.get(d["doc_type"], d["doc_type"])]
        if d.get("n_pages"):
            parts.append(f"{d['n_pages']} стр.")
        if d.get("effective_date"):
            parts.append(f"ред. {_fmt_date(d['effective_date'])}")
        if d.get("scanned"):
            parts.append("скан/OCR")
        lines.append(f"• {d['title']} — {', '.join(parts)}")
    lines += ["", "Задайте вопрос по любому из этих документов — отвечу со ссылкой на пункт и страницу."]
    return Answer(question=question, answer="\n".join(lines), grounded=True,
                  trace={"question": question, "route": "corpus_meta", "documents": len(docs)})


def _is_corpus_about(q: str) -> bool:
    """Вопрос о ТЕМЕ/содержании корпуса в целом («о чём документы», «краткое содержание»)
    — на него отвечаем аннотациями документов, а не поиском по конкретному пункту."""
    ql = " " + q.lower().replace("ё", "е") + " "
    about = any(t in ql for t in (
        "о чем", "про что", "чему посвящ", "краткое содержан", "кратком содержан",
        "о содержании", "какая тема", "какие темы", "тематик", "суть документ"))
    doc_word = any(w in ql for w in ("документ", "файл", "материал", "корпус", "база знан"))
    return about and doc_word


def _corpus_about_answer(question: str) -> Answer:
    docs = _load_corpus_meta()
    if not docs:
        return Answer(question=question, answer=REFUSAL, grounded=False,
                      trace={"question": question, "route": "corpus_about_empty"})
    with_summary = [d for d in docs if d.get("summary")]
    lines = [f"В базе знаний — {len(docs)} документ(ов). Кратко о каждом:", ""]
    for d in sorted(docs, key=lambda x: x["title"]):
        label = _TYPE_LABEL.get(d["doc_type"], d["doc_type"])
        head = f"• {d['title']} ({label}"
        if d.get("n_pages"):
            head += f", {d['n_pages']} стр."
        head += ")"
        summary = d.get("summary")
        lines.append(f"{head} — {summary}" if summary else head)
    if not with_summary:
        lines.append("")
        lines.append("Аннотации ещё не построены — переиндексируйте документы, чтобы увидеть краткое содержание.")
    lines += ["", "Задайте конкретный вопрос по любому документу — отвечу со ссылкой на пункт и страницу."]
    return Answer(question=question, answer="\n".join(lines), grounded=bool(with_summary),
                  trace={"question": question, "route": "corpus_about", "documents": len(docs)})


def _payload_to_chunk(payload: dict) -> Chunk:
    return Chunk(
        chunk_id=payload.get("chunk_id", ""),
        doc_id=payload.get("doc_id", ""),
        doc_title=payload.get("doc_title", ""),
        text=payload.get("text", ""),
        parent_text=payload.get("parent_text", ""),
        section_path=payload.get("section_path", ""),
        clause=payload.get("clause"),
        page=payload.get("page"),
        doc_type=payload.get("doc_type", "regulation"),
        source_url=payload.get("source_url"),
        effective_date=payload.get("effective_date"),
    )


def _alternating_repack(items: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Сильнейшие — в начало и конец (защита от lost-in-the-middle)."""
    if len(items) <= 2:
        return items
    left, right = [], []
    for i, it in enumerate(items):        # items уже по убыванию скора
        (left if i % 2 == 0 else right).append(it)
    return left + right[::-1]


def retrieve(question: str, trace: dict | None = None) -> list[RetrievedChunk]:
    t0 = time.time()
    emb = embed_query(question)
    hits = hybrid_search(emb, limit=settings.retrieve_top_k)
    t_search = time.time() - t0

    # реранк по дочернему тексту фрагмента
    passages = [h.payload.get("text", "") for h in hits]
    ranked = rerank(question, passages)
    t_rerank = time.time() - t0 - t_search

    # дедуп по (документ, пункт) + порог отсечения (retrieval-rail)
    seen: set[tuple] = set()
    contexts: list[RetrievedChunk] = []
    for r in ranked:
        h = hits[r.index]
        # Дедуп по (документ, пункт). Для чанков без пункта (clause=None: преамбула,
        # fallback-нарезка сплошного текста/сканов) НЕЛЬЗЯ схлопывать всё в один ключ —
        # иначе выживает лишь один фрагмент такого документа. Разводим по chunk_id.
        key = (h.payload.get("doc_id"), h.payload.get("clause") or h.payload.get("chunk_id"))
        if key in seen:
            continue
        seen.add(key)
        if r.score < settings.min_rerank_score:
            continue
        contexts.append(
            RetrievedChunk(
                chunk=_payload_to_chunk(h.payload),
                fused_score=h.score,
                rerank_score=r.score,
                rank=len(contexts),
            )
        )
        if len(contexts) >= settings.rerank_top_n:
            break

    # fallback: если порог отсёк всё, но лучший кандидат не абсолютно нерелевантен
    # (score выше жёсткого пола) — оставляем топ-1, пусть модель решит по содержанию.
    # Если же даже лучший кандидат ниже жёсткого пола — не подмешиваем мусор,
    # возвращаем пусто → answer() честно откажет (retrieval-rail не обходится).
    if not contexts and ranked and ranked[0].score >= settings.hard_reject_score:
        h = hits[ranked[0].index]
        contexts.append(
            RetrievedChunk(chunk=_payload_to_chunk(h.payload),
                           fused_score=h.score, rerank_score=ranked[0].score, rank=0)
        )

    # анти lost-in-the-middle: сильнейшие фрагменты в начало и конец контекста
    contexts = _alternating_repack(contexts)
    for i, c in enumerate(contexts):
        c.rank = i

    if trace is not None:
        trace.update(
            {
                "candidates": len(hits),
                "reranked": len(ranked),
                "selected": len(contexts),
                "top_rerank_score": round(ranked[0].score, 3) if ranked else None,
                "t_search_ms": round(t_search * 1000),
                "t_rerank_ms": round(t_rerank * 1000),
                "selected_refs": [
                    {"doc": c.chunk.doc_id, "clause": c.chunk.clause,
                     "page": c.chunk.page, "score": round(c.rerank_score, 3)}
                    for c in contexts
                ],
            }
        )
    return contexts


def _build_citations(answer_text: str, contexts: list[RetrievedChunk]) -> list[Citation]:
    used = [int(m) for m in _MARKER.findall(answer_text)]
    used_unique = [n for n in dict.fromkeys(used) if 1 <= n <= len(contexts)]
    # Если модель не проставила ни одного валидного маркера — НЕ фабрикуем произвольный
    # топ-3 (это подмена проверяемых цитат). Ответ грундирован во всём поданном наборе
    # фрагментов, поэтому честно показываем все использованные контексты как источники —
    # тот фрагмент, на котором реально основан ответ, гарантированно среди них.
    if not used_unique:
        used_unique = list(range(1, len(contexts) + 1))
    citations: list[Citation] = []
    for n in used_unique:
        c = contexts[n - 1].chunk
        citations.append(
            Citation(
                marker=f"[{n}]",
                doc_id=c.doc_id,
                doc_title=c.doc_title,
                clause=c.clause,
                page=c.page,
                section_path=c.section_path,
                snippet=(c.parent_text or c.text)[:600],
                source_url=c.source_url,
                rerank_score=contexts[n - 1].rerank_score,
            )
        )
    return citations


_REFUSAL_MARKERS = (
    "не найден", "не содержится", "нет информации", "не указан", "отсутствует в",
    "не предоставлен", "невозможно ответить", "нет ответа", "не приводится",
)


def _is_refusal(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return False  # пустой вывод обрабатывается отдельно (не отказ, а сбой)
    if t.rstrip(".").startswith(REFUSAL.lower().rstrip(".")[:40]):
        return True
    # перефразированный отказ: короткий ответ с маркером отсутствия сведений И БЕЗ цитат [N].
    # Если модель сослалась на [N] («Комиссия отсутствует в тарифах [1]») — ответ содержателен,
    # понижать grounded и выбрасывать цитаты нельзя.
    return (
        len(t) < 180
        and _MARKER.search(text) is None
        and any(m in t for m in _REFUSAL_MARKERS)
    )


def answer(question: str, model: str | None = None) -> Answer:
    # вопрос о теме/содержании корпуса («о чём документы») → отвечаем аннотациями
    if _is_corpus_about(question):
        return _corpus_about_answer(question)
    # мета-вопрос о составе корпуса → отвечаем списком документов, а не поиском по содержанию
    if _is_corpus_meta(question):
        return _corpus_answer(question)
    trace: dict = {"question": question, "model": model or settings.llm_model}
    contexts = retrieve(question, trace)
    if not contexts:
        return Answer(question=question, answer=REFUSAL, grounded=False, trace=trace)

    llm = get_llm(model)
    user = build_qa_user(question, contexts)
    t0 = time.time()
    text = llm.chat(SYSTEM_QA, user).strip()
    # защита от вырожденного пустого вывода — один ретрай с лёгкой температурой
    if len(text) < 3:
        log.warning("Пустой ответ LLM — повторная генерация")
        text = llm.chat(SYSTEM_QA, user, temperature=0.2).strip()
    # если и ретрай пуст — честный отказ, а не пустой документ (как в answer_stream)
    if len(text) < 3:
        text = REFUSAL
    trace["t_generate_ms"] = round((time.time() - t0) * 1000)

    grounded = bool(text) and not _is_refusal(text)
    citations = _build_citations(text, contexts) if grounded else []
    trace["cited"] = [c.marker for c in citations]
    return Answer(
        question=question,
        answer=text,
        grounded=grounded,
        citations=citations,
        contexts=contexts,
        trace=trace,
    )


def answer_stream(question: str, model: str | None = None) -> Iterator[dict]:
    """Потоковая версия для API: события {'type': ...}."""
    # вопрос о теме/содержании корпуса → аннотации; мета-вопрос о составе → список
    if _is_corpus_about(question) or _is_corpus_meta(question):
        a = _corpus_about_answer(question) if _is_corpus_about(question) else _corpus_answer(question)
        yield {"type": "contexts", "contexts": [], "trace": a.trace}
        yield {"type": "token", "text": a.answer}
        yield {"type": "done", "grounded": a.grounded, "citations": [], "trace": a.trace}
        return
    trace: dict = {"question": question, "model": model or settings.llm_model}
    contexts = retrieve(question, trace)
    yield {"type": "contexts", "contexts": [rc.model_dump() for rc in contexts], "trace": trace}
    if not contexts:
        yield {"type": "token", "text": REFUSAL}
        yield {"type": "done", "grounded": False, "citations": [], "trace": trace}
        return

    llm = get_llm(model)
    user = build_qa_user(question, contexts)
    buf: list[str] = []
    for piece in llm.chat_stream(SYSTEM_QA, user):
        buf.append(piece)
        yield {"type": "token", "text": piece}
    text = "".join(buf).strip()
    # вырожденный пустой вывод T-lite (жадный EOS) — это сбой, а не ответ:
    # отдаём отказ, чтобы не помечать пустоту как grounded и не фабриковать цитаты
    if len(text) < 3:
        text = REFUSAL
        yield {"type": "token", "text": REFUSAL}
    grounded = bool(text) and not _is_refusal(text)
    citations = _build_citations(text, contexts) if grounded else []
    trace["cited"] = [c.marker for c in citations]
    yield {
        "type": "done",
        "grounded": grounded,
        "citations": [c.model_dump() for c in citations],
        "trace": trace,
    }
