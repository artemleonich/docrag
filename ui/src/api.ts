// Клиент API Docs RAG. Все запросы идут через прокси Vite на /api -> FastAPI :8010.

const BASE = "/api";

export interface Citation {
  marker: string;
  doc_id: string;
  doc_title: string;
  clause?: string | null;
  page?: number | null;
  section_path?: string;
  snippet?: string;
  source_url?: string | null;
  rerank_score?: number | null;
}

export interface RetrievedChunk {
  chunk: {
    doc_id: string;
    doc_title: string;
    clause?: string | null;
    page?: number | null;
    section_path?: string;
    parent_text?: string;
    text: string;
  };
  rerank_score?: number | null;
  fused_score?: number;
}

export interface Answer {
  question: string;
  answer: string;
  grounded: boolean;
  citations: Citation[];
  contexts: RetrievedChunk[];
  trace: Record<string, unknown>;
}

export interface DocumentInfo {
  doc_id: string;
  title: string;
  doc_type: string;
  pages?: number | null;
  effective_date?: string | null;
  scanned: boolean;
  file_name?: string | null;
}

// URL исходного PDF для просмотра во вкладке браузера (inline)
export function documentViewUrl(docId: string): string {
  return `${BASE}/documents/${encodeURIComponent(docId)}/file?inline=true`;
}

export async function downloadKbDocument(docId: string, filename: string) {
  const r = await fetch(`${BASE}/documents/${encodeURIComponent(docId)}/file`);
  if (!r.ok) {
    const d = await r.json().catch(() => null);
    throw new Error(d?.detail || `Не удалось скачать (${r.status})`);
  }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || `${docId}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export interface Health {
  status: string;
  qdrant: { exists: boolean; points?: number; status?: string };
  llm: boolean;
  model: string;
}

export interface ModelInfo {
  id: string;
  label: string;
  default: boolean;
  thinking: boolean;
}

export async function getHealth(): Promise<Health> {
  const r = await fetch(`${BASE}/health`);
  return r.json();
}

export async function getModels(): Promise<{ models: ModelInfo[]; default: string }> {
  const r = await fetch(`${BASE}/models`);
  return r.json();
}

export async function listDocuments(): Promise<DocumentInfo[]> {
  const r = await fetch(`${BASE}/documents`);
  return r.json();
}

// Потоковый ответ через SSE
export function askStream(
  question: string,
  handlers: {
    onContexts?: (contexts: RetrievedChunk[], trace: Record<string, unknown>) => void;
    onToken?: (text: string) => void;
    onDone?: (payload: { grounded: boolean; citations: Citation[]; trace: Record<string, unknown> }) => void;
    onError?: () => void;
  },
  model?: string
): () => void {
  const q = encodeURIComponent(question);
  const url = model ? `${BASE}/ask/stream?q=${q}&model=${encodeURIComponent(model)}` : `${BASE}/ask/stream?q=${q}`;
  const es = new EventSource(url);
  es.addEventListener("contexts", (e) => {
    const d = JSON.parse((e as MessageEvent).data);
    handlers.onContexts?.(d.contexts, d.trace);
  });
  es.addEventListener("token", (e) => {
    const d = JSON.parse((e as MessageEvent).data);
    handlers.onToken?.(d.text);
  });
  es.addEventListener("done", (e) => {
    const d = JSON.parse((e as MessageEvent).data);
    handlers.onDone?.(d);
    es.close();
  });
  es.onerror = () => {
    handlers.onError?.();
    es.close();
  };
  return () => es.close();
}

async function downloadBlob(path: string, body: unknown, fallbackName: string) {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`Ошибка генерации (${r.status})`);
  const cd = r.headers.get("Content-Disposition") || "";
  const m = cd.match(/filename="?([^"]+)"?/);
  const name = m ? m[1] : fallbackName;
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a); // некоторые браузеры игнорируют click() вне DOM
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000); // не отзываем до старта скачивания
}

export function generateDocx(question: string, docType: string, subject?: string, model?: string) {
  return downloadBlob("/generate/docx", { question, doc_type: docType, subject, model }, "spravka.docx");
}

export function generatePptx(question: string, subject?: string, model?: string) {
  return downloadBlob("/generate/pptx", { question, subject, model }, "presentation.pptx");
}

// ===== История сгенерированных документов (серверный реестр) =====
export interface GeneratedDoc {
  id: string;
  kind: "docx" | "pptx";
  doc_type: string | null;
  question: string;
  subject: string | null;
  model: string;
  filename: string;
  size: number;
  grounded: boolean;
  n_citations: number;
  created_at: string;
}

export async function getGenHistory(): Promise<GeneratedDoc[]> {
  const r = await fetch(`${BASE}/generate/history`);
  if (!r.ok) return [];
  return r.json();
}

export async function downloadById(id: string, filename: string) {
  const r = await fetch(`${BASE}/generate/download/${encodeURIComponent(id)}`);
  if (!r.ok) {
    const detail = await r.json().catch(() => null);
    throw new Error(detail?.detail || `Не удалось скачать (${r.status})`);
  }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadGenerated(rec: GeneratedDoc) {
  return downloadById(rec.id, rec.filename);
}

// Предпросмотр: строит файл, возвращает ответ+цитаты+record_id (скачивание — по record_id)
export interface PreviewResult {
  answer: string;
  grounded: boolean;
  citations: Citation[];
  record_id: string;
  filename: string;
  kind: "docx" | "pptx";
  model: string;
}

export async function generatePreview(
  kind: "docx" | "pptx",
  question: string,
  docType: string,
  subject?: string,
  model?: string
): Promise<PreviewResult> {
  const r = await fetch(`${BASE}/generate/preview/${kind}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, doc_type: docType, subject, model }),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => null);
    throw new Error(detail?.detail || `Ошибка генерации (${r.status})`);
  }
  return r.json();
}

export async function deleteGenerated(id: string) {
  const r = await fetch(`${BASE}/generate/history/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`Ошибка удаления (${r.status})`);
  return r.json();
}

export async function uploadDocument(
  file: File,
  title: string,
  docType: string,
  date?: string
): Promise<Record<string, unknown>> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("title", title);
  fd.append("doc_type", docType);
  if (date) fd.append("doc_date", date);
  const r = await fetch(`${BASE}/documents/upload`, { method: "POST", body: fd });
  if (!r.ok) {
    // ответ об ошибке может быть не-JSON (например, 413/500 от прокси)
    const detail = await r.json().catch(() => null);
    throw new Error(detail?.detail || `Ошибка загрузки (${r.status})`);
  }
  return r.json();
}

// Событие прогресса загрузки документа (см. /documents/upload/stream)
export interface UploadEvent {
  type: "progress" | "done" | "error";
  stage?: "receive" | "parse" | "summarize" | "chunk" | "embed" | "index";
  status?: "start" | "progress" | "done";
  page?: number;
  total?: number;
  ocr?: boolean;
  done?: number;
  pages?: number;
  nodes?: number;
  chunks?: number;
  points?: number;
  scanned?: boolean;
  summary?: Record<string, unknown>;
  message?: string;
}

// Загрузка с потоковым прогрессом по этапам (NDJSON). onEvent зовётся на каждое событие.
export async function uploadDocumentStream(
  file: File,
  title: string,
  docType: string,
  onEvent: (ev: UploadEvent) => void,
  date?: string
): Promise<Record<string, unknown>> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("title", title);
  fd.append("doc_type", docType);
  if (date) fd.append("doc_date", date);
  const r = await fetch(`${BASE}/documents/upload/stream`, { method: "POST", body: fd });
  if (!r.ok || !r.body) {
    const detail = await r.json().catch(() => null);
    throw new Error(detail?.detail || `Ошибка загрузки (${r.status})`);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let summary: Record<string, unknown> = {};
  let errMsg: string | null = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      let ev: UploadEvent;
      try {
        ev = JSON.parse(line);
      } catch {
        continue; // неполная/битая строка — пропускаем
      }
      onEvent(ev);
      if (ev.type === "done") summary = ev.summary || {};
      else if (ev.type === "error") errMsg = ev.message || "Ошибка обработки";
    }
  }
  if (errMsg) throw new Error(errMsg);
  return summary;
}

export async function deleteDocument(docId: string) {
  const r = await fetch(`${BASE}/documents/${encodeURIComponent(docId)}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`Ошибка удаления (${r.status})`);
  return r.json();
}
