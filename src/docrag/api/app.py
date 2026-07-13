"""FastAPI-бэкенд Docs RAG.

Онлайн-часть (только чтение из Qdrant + вызовы Ollama). Эндпоинты:
  GET  /health                — статус сервисов
  GET  /documents             — список документов корпуса
  POST /ask                   — ответ с цитатами (JSON)
  GET  /ask/stream?q=...      — потоковый ответ (SSE)
  POST /generate/docx         — .docx (справка/выписка/проект ответа)
  POST /generate/pptx         — .pptx (презентация)
  POST /documents/upload      — дозагрузка нового документа (текст/скан, OCR авто)
  DELETE /documents/{doc_id}  — удаление документа
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse

from docrag.api.schemas import AskRequest, DocumentInfo, GenerateRequest
from docrag.common.logging import get_logger
from docrag.settings import settings

log = get_logger("api")

app = FastAPI(title="Docs RAG API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
@app.get("/health")
def health() -> dict:
    from docrag.indexer.qdrant_store import collection_info
    from docrag.rag.llm import OllamaBackend

    try:
        info = collection_info()
    except Exception:  # noqa: BLE001 — Qdrant недоступен: не роняем /health в 500
        info = {"exists": False}
    try:
        llm_ok = OllamaBackend().health()
    except Exception:  # noqa: BLE001
        llm_ok = False
    return {
        "status": "ok" if info.get("exists") and llm_ok else "degraded",
        "qdrant": info,
        "llm": llm_ok,
        "model": settings.llm_model,
    }


@app.get("/documents", response_model=list[DocumentInfo])
def documents() -> list[DocumentInfo]:
    out: list[DocumentInfo] = []
    for path in sorted(settings.parsed_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        meta = data.get("meta", {})
        out.append(
            DocumentInfo(
                doc_id=meta.get("doc_id", path.stem),
                title=meta.get("title", path.stem),
                doc_type=meta.get("doc_type", "regulation"),
                pages=meta.get("n_pages"),
                effective_date=meta.get("effective_date"),
                scanned="ocr" in data.get("parser", ""),
                file_name=meta.get("file_name"),
            )
        )
    return out


@app.get("/documents/{doc_id}/file")
def document_file(doc_id: str, inline: bool = False):
    """Отдаёт исходный PDF документа корпуса — для просмотра (inline) или скачивания."""
    safe = Path(doc_id).name  # только имя, без разделителей пути (защита от traversal)
    raw_dir = settings.raw_dir.resolve()
    path = raw_dir / f"{safe}.pdf"
    # defense-in-depth: путь обязан оставаться внутри raw_dir (устойчиво к будущим правкам роутинга)
    if not path.resolve().is_relative_to(raw_dir) or not path.exists():
        raise HTTPException(404, "Исходный файл документа не найден")
    fname = f"{safe}.pdf"
    meta_path = settings.parsed_dir / f"{safe}.json"
    if meta_path.exists():
        try:
            fn = json.loads(meta_path.read_text(encoding="utf-8")).get("meta", {}).get("file_name")
            if fn:
                fname = Path(fn).name
        except Exception:  # noqa: BLE001
            pass
    return FileResponse(
        str(path),
        media_type="application/pdf",
        filename=fname,
        content_disposition_type="inline" if inline else "attachment",
    )


@app.get("/models")
def models() -> dict:
    """Список моделей генерации для селектора в UI + текущая дефолтная."""
    out = []
    for mid in settings.llm_models:
        out.append({
            "id": mid,
            "label": settings.llm_model_labels.get(mid, mid),
            "default": mid == settings.llm_model,
            "thinking": mid in settings.llm_thinking_models,
        })
    return {"models": out, "default": settings.llm_model}


def _resolve_model(model: str | None) -> str | None:
    """Валидируем выбранную модель по allowlist. Пусто/None → дефолт (возвращаем None)."""
    if not model:
        return None
    if model not in settings.llm_models:
        raise HTTPException(400, f"Неизвестная модель: {model}")
    return model


@app.post("/ask")
def ask(req: AskRequest) -> dict:
    from docrag.rag.pipeline import answer

    if not req.question.strip():
        raise HTTPException(400, "Пустой вопрос")
    a = answer(req.question, model=_resolve_model(req.model))
    return a.model_dump()


@app.get("/ask/stream")
async def ask_stream(q: str, model: str | None = None):
    from starlette.concurrency import iterate_in_threadpool

    from docrag.rag.pipeline import answer_stream

    if not q.strip():
        raise HTTPException(400, "Пустой вопрос")
    resolved = _resolve_model(model)

    async def event_gen():
        # answer_stream синхронный (Ollama/реранк) — гоним его в threadpool,
        # чтобы не блокировать event loop и не сериализовать все запросы
        try:
            async for ev in iterate_in_threadpool(answer_stream(q, model=resolved)):
                yield {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False)}
        except Exception as e:  # noqa: BLE001
            yield {"event": "error", "data": json.dumps({"type": "error", "message": str(e)})}

    return EventSourceResponse(event_gen())


_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@app.post("/generate/docx")
def generate_docx(req: GenerateRequest):
    from docrag.generators import history
    from docrag.generators.docx_generator import build_docx
    from docrag.rag.pipeline import answer

    model = _resolve_model(req.model)
    a = answer(req.question, model=model)
    path = build_docx(a, doc_type=req.doc_type, subject=req.subject)
    history.record(kind="docx", doc_type=req.doc_type, question=req.question,
                   subject=req.subject, model=model, path=path, answer=a)
    return FileResponse(str(path), media_type=_DOCX_MIME, filename=path.name)


@app.post("/generate/pptx")
def generate_pptx(req: GenerateRequest):
    from docrag.generators import history
    from docrag.generators.pptx_generator import build_pptx
    from docrag.rag.pipeline import answer

    model = _resolve_model(req.model)
    a = answer(req.question, model=model)
    path = build_pptx(a, topic=req.subject or req.question, model=model)
    history.record(kind="pptx", doc_type=None, question=req.question,
                   subject=req.subject, model=model, path=path, answer=a)
    return FileResponse(str(path), media_type=_PPTX_MIME, filename=path.name)


@app.post("/generate/preview/{kind}")
def generate_preview(kind: str, req: GenerateRequest) -> dict:
    """Строит файл, пишет в историю и возвращает ответ+цитаты для предпросмотра ДО скачивания.
    Файл затем скачивается по record_id (тот же артефакт, без повторной генерации)."""
    from docrag.generators import history
    from docrag.rag.pipeline import answer

    if kind not in ("docx", "pptx"):
        raise HTTPException(400, "kind должен быть docx или pptx")
    if not req.question.strip():
        raise HTTPException(400, "Пустой вопрос")
    model = _resolve_model(req.model)
    a = answer(req.question, model=model)
    if kind == "docx":
        from docrag.generators.docx_generator import build_docx

        path = build_docx(a, doc_type=req.doc_type, subject=req.subject)
    else:
        from docrag.generators.pptx_generator import build_pptx

        path = build_pptx(a, topic=req.subject or req.question, model=model)
    rec = history.record(kind=kind, doc_type=(req.doc_type if kind == "docx" else None),
                         question=req.question, subject=req.subject, model=model, path=path, answer=a)
    return {
        "answer": a.answer,
        "grounded": a.grounded,
        "citations": [c.model_dump() for c in a.citations],
        "record_id": rec["id"],
        "filename": path.name,
        "kind": kind,
        "model": model or settings.llm_model,
    }


@app.get("/generate/history")
def generate_history() -> list[dict]:
    from docrag.generators import history

    return history.list_records()


@app.get("/generate/download/{rec_id}")
def generate_download(rec_id: str):
    from docrag.generators import history

    rec = history.get_record(rec_id)
    if rec is None:
        raise HTTPException(404, "Запись не найдена")
    path = Path(rec["path"])
    if not path.exists():
        raise HTTPException(410, "Файл удалён с диска")
    mime = _DOCX_MIME if rec.get("kind") == "docx" else _PPTX_MIME
    return FileResponse(str(path), media_type=mime, filename=path.name)


@app.delete("/generate/history/{rec_id}")
def generate_history_delete(rec_id: str, remove_file: bool = False) -> dict:
    from docrag.generators import history

    ok = history.delete_record(rec_id, remove_file=remove_file)
    if not ok:
        raise HTTPException(404, "Запись не найдена")
    return {"deleted": rec_id}


_MAX_UPLOAD_BYTES = 60 * 1024 * 1024  # 60 МБ — потолок против OOM/DoS


@app.post("/documents/upload", response_model=dict)
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(...),
    doc_type: str = Form("regulation"),
    doc_date: str = Form(None),
):
    import shutil

    from docrag.ingest import add_document

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Ожидается PDF-файл")
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Файл больше {_MAX_UPLOAD_BYTES // (1024*1024)} МБ")
    if not data:
        raise HTTPException(400, "Пустой файл")
    tmpdir = Path(tempfile.mkdtemp())
    # берём ТОЛЬКО базовое имя без разделителей пути (защита от path traversal)
    safe_name = Path(file.filename).name.replace("\\", "_") or "upload.pdf"
    tmp = tmpdir / safe_name
    tmp.write_bytes(data)
    try:
        summary = add_document(str(tmp), title=title, doc_type=doc_type, doc_date=doc_date or None)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Ошибка обработки: {e}") from e
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)  # чистим временные файлы
    return summary


@app.post("/documents/upload/stream")
async def upload_document_stream(
    file: UploadFile = File(...),
    title: str = Form(...),
    doc_type: str = Form("regulation"),
    doc_date: str = Form(None),
):
    """Как /documents/upload, но стримит прогресс по этапам (NDJSON), чтобы UI показывал
    пользователю, что сейчас делается с документом: приём → извлечение текста (+OCR постранично)
    → разбиение → эмбеддинги → индексация. Каждая строка ответа — JSON-событие."""
    import asyncio
    import queue
    import shutil
    import threading

    from docrag.ingest import add_document

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Ожидается PDF-файл")
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Файл больше {_MAX_UPLOAD_BYTES // (1024*1024)} МБ")
    if not data:
        raise HTTPException(400, "Пустой файл")
    tmpdir = Path(tempfile.mkdtemp())
    safe_name = Path(file.filename).name.replace("\\", "_") or "upload.pdf"
    tmp = tmpdir / safe_name
    tmp.write_bytes(data)

    events: "queue.Queue" = queue.Queue()

    def worker() -> None:
        # add_document блокирующий (модели, OCR) — гоним в отдельном потоке, а прогресс
        # прокидываем в очередь, которую асинхронный генератор ниже отдаёт клиенту.
        try:
            summary = add_document(
                str(tmp), title=title, doc_type=doc_type, doc_date=doc_date or None,
                on_progress=lambda ev: events.put(("progress", ev)),
            )
            events.put(("done", summary))
        except Exception as e:  # noqa: BLE001 — любую ошибку показываем пользователю строкой
            events.put(("error", str(e)))
        finally:
            events.put(("_end", None))

    threading.Thread(target=worker, daemon=True).start()

    async def gen():
        loop = asyncio.get_event_loop()
        try:
            while True:
                kind, payload = await loop.run_in_executor(None, events.get)
                if kind == "_end":
                    break
                if kind == "progress":
                    yield json.dumps({"type": "progress", **payload}, ensure_ascii=False) + "\n"
                elif kind == "done":
                    yield json.dumps({"type": "done", "summary": payload}, ensure_ascii=False) + "\n"
                elif kind == "error":
                    yield json.dumps({"type": "error", "message": payload}, ensure_ascii=False) + "\n"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)  # чистим временные файлы

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str) -> dict:
    from docrag.ingest import remove_document

    left = remove_document(doc_id)
    return {"doc_id": doc_id, "points_left": left}
