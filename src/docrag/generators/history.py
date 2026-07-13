"""Реестр сгенерированных документов (docx/pptx) для истории и повторного скачивания.

Файлы физически лежат в data/generated/, метаданные — в data/generated/history.json.
Запись файла атомарна (temp + os.replace); весь цикл read-modify-write сериализован
межпроцессной блокировкой (fcntl.flock), поэтому параллельные генерации не теряют записей.
"""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from docrag.common.logging import get_logger
from docrag.common.models import Answer
from docrag.settings import settings

log = get_logger("gen.history")


def _registry_path() -> Path:
    return settings.data_dir / "generated" / "history.json"


@contextmanager
def _lock():
    """Эксклюзивная межпроцессная блокировка на время read-modify-write реестра."""
    lock_path = _registry_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def _load() -> list[dict]:
    p = _registry_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001 — битый реестр не должен ронять генерацию
        log.warning("Реестр истории повреждён, начинаю с пустого")
        return []


def _save(records: list[dict]) -> None:
    p = _registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp-{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)  # атомарная замена


def record(*, kind: str, doc_type: str | None, question: str, subject: str | None,
           model: str | None, path: Path, answer: Answer) -> dict:
    """Добавить запись о сгенерированном файле. Возвращает саму запись."""
    rec = {
        "id": uuid.uuid4().hex,
        "kind": kind,                         # docx | pptx
        "doc_type": doc_type,                 # spravka|vypiska|proekt_otveta | None для pptx
        "question": question,
        "subject": subject,
        "model": model or settings.llm_model,
        "filename": path.name,
        "path": str(path),
        "size": path.stat().st_size if path.exists() else 0,
        "grounded": bool(answer.grounded),
        "n_citations": len(answer.citations or []),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _lock():
        records = _load()
        records.append(rec)
        _save(records)
    return rec


def list_records() -> list[dict]:
    """Все записи, новые сверху."""
    return sorted(_load(), key=lambda r: r.get("created_at", ""), reverse=True)


def get_record(rec_id: str) -> dict | None:
    for r in _load():
        if r.get("id") == rec_id:
            return r
    return None


def delete_record(rec_id: str, *, remove_file: bool = False) -> bool:
    with _lock():
        records = _load()
        kept = [r for r in records if r.get("id") != rec_id]
        if len(kept) == len(records):
            return False
        if remove_file:
            for r in records:
                if r.get("id") == rec_id:
                    try:
                        Path(r["path"]).unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001
                        pass
        _save(kept)
    return True
