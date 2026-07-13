import { useEffect, useState } from "react";
import {
  generatePreview,
  downloadById,
  getModels,
  getGenHistory,
  downloadGenerated,
  deleteGenerated,
  type ModelInfo,
  type GeneratedDoc,
  type PreviewResult,
  type Citation,
} from "../api";
import { RichText } from "../markdown";
import { IconDownload } from "../icons";

const DOC_TYPES = [
  { v: "spravka", l: "Справка" },
  { v: "vypiska", l: "Выписка из нормативных документов" },
  { v: "proekt_otveta", l: "Проект ответа" },
];

const DOC_TYPE_LABEL: Record<string, string> = {
  spravka: "Справка",
  vypiska: "Выписка",
  proekt_otveta: "Проект ответа",
};

function citeRef(c: Citation): string {
  const parts = [c.doc_title];
  if (c.clause) parts.push(`п. ${c.clause}`);
  if (c.page != null) parts.push(`стр. ${c.page}`);
  return parts.join(", ");
}

export default function GenerateView({ mode }: { mode: "docx" | "pptx" }) {
  const [question, setQuestion] = useState("");
  const [subject, setSubject] = useState("");
  const [docType, setDocType] = useState("spravka");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [model, setModel] = useState<string>("");
  const [history, setHistory] = useState<GeneratedDoc[]>([]);

  const isDocx = mode === "docx";

  useEffect(() => {
    getModels()
      .then((r) => {
        setModels(r.models);
        setModel((cur) => cur || r.default);
      })
      .catch(() => {});
    refreshHistory();
  }, []);

  const modelLabel = (id?: string) => models.find((m) => m.id === id)?.label ?? id ?? "";

  function refreshHistory() {
    getGenHistory().then(setHistory).catch(() => {});
  }

  async function run() {
    if (!question.trim() || busy) return;
    setBusy(true);
    setError(null);
    setPreview(null);
    try {
      const p = await generatePreview(mode, question, docType, subject || undefined, model || undefined);
      setPreview(p);
      refreshHistory();
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  }

  async function reDownload(rec: GeneratedDoc) {
    try {
      await downloadGenerated(rec);
    } catch (e) {
      setError(String((e as Error).message || e));
    }
  }

  async function remove(id: string) {
    try {
      await deleteGenerated(id);
    } catch {
      /* всё равно обновим */
    }
    refreshHistory();
  }

  return (
    <div className="view">
      <div className="view__head">
        <div className="view__title">{isDocx ? "Генерация документа (.docx)" : "Генерация презентации (.pptx)"}</div>
        <div className="view__desc">
          {isDocx
            ? "Справки, выписки и проекты ответов на основе нормативной базы, с проверяемыми ссылками."
            : "Презентация по теме на основе нормативных документов, в фирменном стиле."}
        </div>
      </div>

      <div className="card form-card">
        <div className="field">
          <label className="field__label">Вопрос / содержание {isDocx ? "документа" : "презентации"}</label>
          <textarea
            className="textarea"
            placeholder={isDocx ? "Например: Каков порядок … согласно правилам?" : "Например: Ключевые требования по теме …"}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
        </div>

        <div className="form-row">
          {isDocx && (
            <div className="field">
              <label className="field__label">Тип документа</label>
              <select className="select" value={docType} onChange={(e) => setDocType(e.target.value)}>
                {DOC_TYPES.map((d) => (
                  <option key={d.v} value={d.v}>{d.l}</option>
                ))}
              </select>
            </div>
          )}
          <div className="field">
            <label className="field__label">Тема / заголовок {isDocx ? "(необязательно)" : ""}</label>
            <input
              className="input"
              placeholder={isDocx ? "Заголовок темы" : "Заголовок презентации"}
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
            />
          </div>
          {models.length > 1 && (
            <div className="field">
              <label className="field__label">Модель генерации</label>
              <select className="select" value={model} onChange={(e) => setModel(e.target.value)} disabled={busy}>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>{m.label}</option>
                ))}
              </select>
            </div>
          )}
        </div>

        <button className="btn btn--primary" disabled={busy || !question.trim()} onClick={run}>
          {busy ? <span className="spinner" /> : <IconDownload className="nav__icon" />}
          {busy ? "Генерация…" : "Сгенерировать и посмотреть"}
        </button>

        {error && <div className="result-note result-note--err">{error}</div>}
      </div>

      {preview && (
        <div className="card preview-card">
          <div className="preview__head">
            {preview.grounded ? (
              preview.citations.length > 0 && <span className="badge badge--ok">Подтверждено источниками</span>
            ) : (
              <span className="badge badge--warn">Ответ не найден в документах</span>
            )}
            <span className="badge badge--model">⚡ {modelLabel(preview.model)}</span>
            <button
              className="btn btn--primary btn--sm preview__dl"
              onClick={async () => {
                try {
                  await downloadById(preview.record_id, preview.filename);
                } catch (e) {
                  setError(String((e as Error).message || e));
                }
              }}
            >
              <IconDownload className="nav__icon" />
              Скачать {isDocx ? ".docx" : ".pptx"}
            </button>
          </div>
          <div className="preview__body"><RichText text={preview.answer} /></div>
          {preview.citations.length > 0 && (
            <div className="preview__sources">
              <div className="sources__title">Источники</div>
              {preview.citations.map((c) => (
                <div key={c.marker} className="preview__src">
                  <span className="source__marker">{c.marker}</span>
                  <span className="source__ref">{citeRef(c)}</span>
                </div>
              ))}
            </div>
          )}
          <div className="preview__note">Предпросмотр. Файл уже готов — нажмите «Скачать». Перед использованием требуется проверка уполномоченным сотрудником.</div>
        </div>
      )}

      {history.length > 0 && (
        <div className="gen-history">
          <div className="gen-history__title">История документов</div>
          <div className="gen-history__list">
            {history.map((r) => (
              <div key={r.id} className="gen-item">
                <span className={`gen-item__kind gen-item__kind--${r.kind}`}>{r.kind === "docx" ? (r.doc_type ? DOC_TYPE_LABEL[r.doc_type] ?? "DOCX" : "DOCX") : "PPTX"}</span>
                <div className="gen-item__body">
                  <div className="gen-item__q">{r.subject || r.question}</div>
                  <div className="gen-item__meta">
                    ⚡ {modelLabel(r.model)} · {new Date(r.created_at).toLocaleString("ru-RU")}
                    {r.grounded ? ` · ${r.n_citations} цит.` : " · без цитат"}
                  </div>
                </div>
                <button className="btn btn--ghost btn--sm" onClick={() => reDownload(r)} title="Скачать снова">
                  <IconDownload className="nav__icon" />
                </button>
                <button className="btn btn--danger btn--sm" onClick={() => remove(r.id)} title="Удалить из истории">✕</button>
              </div>
            ))}
          </div>
        </div>
      )}

      <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 16 }}>
        Документ формируется из ответа RAG-системы и содержит раздел «Источники». Перед использованием требуется проверка уполномоченным сотрудником.
      </p>
    </div>
  );
}
