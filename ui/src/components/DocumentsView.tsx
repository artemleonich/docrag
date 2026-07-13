import { useEffect, useRef, useState } from "react";
import {
  deleteDocument,
  listDocuments,
  uploadDocumentStream,
  documentViewUrl,
  downloadKbDocument,
  type DocumentInfo,
  type UploadEvent,
} from "../api";
import { IconDoc, IconTrash, IconUpload, IconDownload } from "../icons";

const TYPE_LABEL: Record<string, string> = {
  rules: "Правила",
  tariff: "Тарифы",
  charter: "Устав",
  regulation: "Регламент",
};

// Этапы обработки документа (в порядке выполнения) — для наглядного индикатора прогресса.
// weight — доля этапа в общем прогресс-баре (парсинг/OCR и эмбеддинги — самые долгие).
const STAGES = [
  { key: "receive", label: "Приём файла", weight: 5 },
  { key: "parse", label: "Извлечение текста из PDF", weight: 30 },
  { key: "summarize", label: "Составление аннотации", weight: 10 },
  { key: "chunk", label: "Разбиение на фрагменты", weight: 5 },
  { key: "embed", label: "Построение эмбеддингов", weight: 40 },
  { key: "index", label: "Индексация в базе знаний", weight: 10 },
] as const;

type StepStatus = "pending" | "run" | "done" | "error";
type Steps = Record<string, { status: StepStatus; detail?: string; frac?: number }>;

// Человекочитаемая подпись к текущему этапу из события прогресса
function stepDetail(ev: UploadEvent): string | undefined {
  if (ev.stage === "parse" && ev.status === "progress")
    return `стр. ${ev.page}/${ev.total}${ev.ocr ? " · распознавание (OCR)" : ""}`;
  if (ev.stage === "parse" && ev.status === "done")
    return `${ev.pages} стр., ${ev.nodes} пунктов${ev.scanned ? " · скан (OCR)" : ""}`;
  if (ev.stage === "chunk" && ev.status === "done") return `${ev.chunks} фрагментов`;
  if (ev.stage === "embed" && ev.status === "start") return `0/${ev.total}`;
  if (ev.stage === "embed" && ev.status === "progress") return `${ev.done}/${ev.total}`;
  if (ev.stage === "index" && ev.status === "done") return `${ev.points} векторов`;
  return undefined;
}

// Доля выполнения текущего этапа (0..1) — для полосы под этапом и общего прогресса
function stepFrac(ev: UploadEvent): number | undefined {
  if (ev.stage === "parse" && ev.status === "progress" && ev.total) return (ev.page ?? 0) / ev.total;
  if (ev.stage === "embed" && ev.status === "progress" && ev.total) return (ev.done ?? 0) / ev.total;
  if (ev.stage === "embed" && ev.status === "start") return 0;
  return undefined;
}

// Общий прогресс (0..100) с учётом весов этапов и доли выполнения активного
function overallPct(steps: Steps): number {
  let pct = 0;
  for (const s of STAGES) {
    const st = steps[s.key];
    if (!st) continue;
    if (st.status === "done") pct += s.weight;
    else if (st.status === "run" || st.status === "error") pct += s.weight * (st.frac ?? 0);
  }
  return Math.min(100, Math.round(pct));
}

export default function DocumentsView() {
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState("regulation");
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [steps, setSteps] = useState<Steps>({});
  const fileRef = useRef<HTMLInputElement>(null);

  // Обновляем состояние этапов по событию прогресса от сервера
  function applyEvent(ev: UploadEvent) {
    if (ev.type !== "progress" || !ev.stage) return;
    const stage = ev.stage;
    setSteps((prev) => {
      const next: Steps = { ...prev };
      if (ev.status === "start") {
        // всё, что раньше текущего этапа, помечаем завершённым
        const idx = STAGES.findIndex((s) => s.key === stage);
        STAGES.slice(0, idx).forEach((s) => {
          if (next[s.key]?.status !== "done") next[s.key] = { ...next[s.key], status: "done", frac: 1 };
        });
        next[stage] = { status: "run", detail: next[stage]?.detail, frac: stepFrac(ev) ?? 0 };
      } else if (ev.status === "progress") {
        next[stage] = { status: "run", detail: stepDetail(ev) ?? next[stage]?.detail, frac: stepFrac(ev) ?? next[stage]?.frac };
      } else if (ev.status === "done") {
        next[stage] = { status: "done", detail: stepDetail(ev) ?? next[stage]?.detail, frac: 1 };
      }
      return next;
    });
  }

  async function refresh() {
    setLoading(true);
    try {
      setDocs(await listDocuments());
    } catch {
      setNote({ ok: false, text: "Не удалось загрузить список документов (сервер недоступен?)" });
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    refresh();
  }, []);

  async function doUpload() {
    if (!file || !title.trim() || busy) return;
    setBusy(true);
    setNote(null);
    setSteps({}); // сбрасываем индикатор для новой загрузки
    const docTitle = title;
    try {
      const r = (await uploadDocumentStream(file, title, docType, applyEvent)) as any;
      const scanned = r.scanned ? " (скан → OCR)" : "";
      setNote({ ok: true, text: `Добавлен «${docTitle}»${scanned}: ${r.pages} стр., ${r.chunks} фрагментов.` });
      setTitle("");
      setFile(null);
      await refresh();
    } catch (e) {
      // помечаем этап, на котором оборвалось, красным — видно, ГДЕ упало
      setSteps((prev) => {
        const next: Steps = { ...prev };
        for (const s of STAGES) if (next[s.key]?.status === "run") next[s.key] = { ...next[s.key], status: "error" };
        return next;
      });
      setNote({ ok: false, text: String((e as Error).message || e) });
    } finally {
      setBusy(false);
    }
  }

  async function doDelete(id: string, name: string) {
    if (!confirm(`Удалить «${name}» из базы?`)) return;
    try {
      await deleteDocument(id);
      await refresh();
    } catch (e) {
      setNote({ ok: false, text: `Не удалось удалить: ${(e as Error).message}` });
    }
  }

  async function doDownload(d: DocumentInfo) {
    try {
      await downloadKbDocument(d.doc_id, d.file_name || `${d.doc_id}.pdf`);
    } catch (e) {
      setNote({ ok: false, text: `Не удалось скачать: ${(e as Error).message}` });
    }
  }

  const pct = overallPct(steps);

  return (
    <div className="view">
      <div className="view__head">
        <div className="view__title">Документы базы знаний</div>
        <div className="view__desc">Нормативные документы, по которым система отвечает. Можно добавить новый документ — текстовый или скан (распознаётся автоматически).</div>
      </div>

      {/* Загрузка */}
      <div className="card form-card" style={{ marginBottom: 24 }}>
        <div
          className={`upload-zone ${drag ? "upload-zone--drag" : ""}`}
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            const f = e.dataTransfer.files?.[0];
            if (f && f.name.toLowerCase().endsWith(".pdf")) setFile(f);
          }}
        >
          <IconUpload className="nav__icon" />
          <div style={{ marginTop: 8 }}>
            {file ? <b>{file.name}</b> : "Перетащите PDF сюда или нажмите для выбора"}
          </div>
          <input ref={fileRef} type="file" accept=".pdf" hidden onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </div>
        <div className="form-row" style={{ marginTop: 16 }}>
          <div className="field" style={{ marginBottom: 0 }}>
            <label className="field__label">Название документа</label>
            <input className="input" placeholder="Например: Регламент или Правила" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label className="field__label">Тип</label>
            <select className="select" value={docType} onChange={(e) => setDocType(e.target.value)}>
              <option value="regulation">Регламент</option>
              <option value="rules">Правила</option>
              <option value="tariff">Тарифы</option>
              <option value="charter">Устав</option>
            </select>
          </div>
        </div>
        <button className="btn btn--primary" style={{ marginTop: 16 }} disabled={busy || !file || !title.trim()} onClick={doUpload}>
          {busy ? <span className="spinner" /> : <IconUpload className="nav__icon" />}
          {busy ? "Обработка документа…" : "Добавить в базу"}
        </button>

        {/* Живой индикатор этапов обработки — чтобы было понятно, что происходит с документом */}
        {Object.keys(steps).length > 0 && (
          <div className="up-steps" aria-live="polite">
            <div className="up-steps__head">
              <span className="up-steps__title">{pct >= 100 ? "Готово" : "Обработка документа"}</span>
              <span className="up-steps__pct">{pct}%</span>
            </div>
            <div
              className="up-steps__track"
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div className="up-steps__fill" style={{ width: `${pct}%` }} />
            </div>
            {STAGES.map((s) => {
              const st = steps[s.key]?.status ?? "pending";
              const detail = steps[s.key]?.detail;
              const frac = steps[s.key]?.frac ?? 0;
              const determinate = st === "run" && frac > 0;
              const indeterminate = st === "run" && frac === 0;
              return (
                <div key={s.key} className={`up-step up-step--${st}`}>
                  <span className="up-step__icon">
                    {st === "done" ? "✓" : st === "error" ? "✕" : st === "run" ? <span className="spinner spinner--dark" /> : "○"}
                  </span>
                  <span className="up-step__label">{s.label}</span>
                  {detail && <span className="up-step__detail">{detail}</span>}
                  {st === "done" && <span className="up-step__bar up-step__bar--full" />}
                  {determinate && <span className="up-step__bar" style={{ width: `${Math.round(frac * 100)}%` }} />}
                  {indeterminate && <span className="up-step__bar up-step__bar--indet" />}
                </div>
              );
            })}
          </div>
        )}

        {note && <div className={`result-note ${note.ok ? "result-note--ok" : "result-note--err"}`}>{note.text}</div>}
      </div>

      {/* Список */}
      {loading ? (
        <div className="empty"><span className="spinner spinner--dark" /></div>
      ) : (
        <div className="doc-list">
          {docs.map((d) => (
            <div key={d.doc_id} className="card doc-item">
              <div className="doc-item__icon"><IconDoc className="nav__icon" /></div>
              <div className="doc-item__main">
                <div className="doc-item__title">{d.title}</div>
                <div className="doc-item__meta">
                  <span className="chip">{TYPE_LABEL[d.doc_type] || d.doc_type}</span>
                  {d.pages != null && <span>{d.pages} стр.</span>}
                  {d.effective_date && <span>ред. {d.effective_date}</span>}
                  {d.scanned && <span className="chip chip--scan">скан · OCR</span>}
                </div>
              </div>
              <div className="doc-item__actions">
                <button className="btn btn--ghost btn--sm" title="Открыть PDF в браузере" onClick={() => window.open(documentViewUrl(d.doc_id), "_blank", "noopener")}>
                  Просмотр
                </button>
                <button className="btn btn--ghost btn--sm" title="Скачать PDF" onClick={() => doDownload(d)}>
                  <IconDownload className="nav__icon" />
                </button>
                <button className="btn btn--danger btn--sm" title="Удалить из базы" onClick={() => doDelete(d.doc_id, d.title)}>
                  <IconTrash className="nav__icon" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
