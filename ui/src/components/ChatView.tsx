import { useEffect, useRef, useState } from "react";
import { askStream, getModels, type Citation, type ModelInfo } from "../api";
import { loadSessions, saveSession, deleteSession, newId, type StoredSession } from "../storage";
import { RichText } from "../markdown";
import { IconChat, IconSend } from "../icons";

interface Msg {
  role: "user" | "bot";
  text: string;
  grounded?: boolean;
  citations?: Citation[];
  trace?: Record<string, unknown>;
  streaming?: boolean;
  error?: boolean;
  model?: string;        // id модели, которой сгенерирован ответ (для сравнения)
}

const SUGGESTIONS = [
  "Какие документы есть в базе знаний?",
  "Что означает термин … (по вашему документу)?",
  "Какие требования предъявляются к … ?",
  "Каков порядок … согласно правилам?",
];

function SourceCard({ c }: { c: Citation }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="source">
      <div className="source__head" onClick={() => setOpen((o) => !o)}>
        <span className="source__marker">{c.marker}</span>
        <span className="source__ref">{refText(c)}</span>
        <span className="source__path">{open ? "▲" : "▼"}</span>
      </div>
      {open && (
        <div className="source__body">
          {c.section_path && <div style={{ color: "var(--muted)", marginBottom: 6 }}>{c.section_path}</div>}
          {c.snippet}
          {typeof c.rerank_score === "number" && (
            <div className="source__score">релевантность реранкера: {c.rerank_score.toFixed(2)}</div>
          )}
        </div>
      )}
    </div>
  );
}

function refText(c: Citation) {
  const parts = [c.doc_title];
  if (c.clause) parts.push(`п. ${c.clause}`);
  if (c.page != null) parts.push(`стр. ${c.page}`);
  return parts.join(", ");
}

function Trace({ trace }: { trace: Record<string, unknown> }) {
  const items: [string, string][] = [];
  const t = trace as any;
  if (t.candidates != null) items.push(["кандидатов", String(t.candidates)]);
  if (t.selected != null) items.push(["в контекст", String(t.selected)]);
  if (t.top_rerank_score != null) items.push(["top реранк", String(t.top_rerank_score)]);
  if (t.t_search_ms != null) items.push(["поиск", `${t.t_search_ms} мс`]);
  if (t.t_rerank_ms != null) items.push(["реранк", `${t.t_rerank_ms} мс`]);
  if (t.t_generate_ms != null) items.push(["генерация", `${t.t_generate_ms} мс`]);
  if (!items.length) return null;
  return (
    <details className="trace">
      <summary>Детали поиска (проверяемость)</summary>
      <div className="trace__grid">
        {items.map(([k, v]) => (
          <span key={k} className="trace__item">{k}: {v}</span>
        ))}
      </div>
    </details>
  );
}

export default function ChatView() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [model, setModel] = useState<string>("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<null | (() => void)>(null);

  // список моделей генерации для селектора (дефолт — как на бэкенде)
  useEffect(() => {
    getModels()
      .then((r) => {
        setModels(r.models);
        setModel((cur) => cur || r.default);
      })
      .catch(() => {});
  }, []);

  const modelLabel = (id?: string) => models.find((m) => m.id === id)?.label ?? id ?? "";

  // ===== История чатов (localStorage) =====
  const [sessions, setSessions] = useState<StoredSession[]>([]);
  const [currentId, setCurrentId] = useState<string>(() => newId());

  useEffect(() => {
    setSessions(loadSessions());
  }, []);

  // сохраняем сессию после каждого завершённого обмена (последнее сообщение — готовый ответ бота)
  useEffect(() => {
    if (!messages.length) return;
    const last = messages[messages.length - 1];
    if (last.role !== "bot" || last.streaming) return;
    const firstUser = messages.find((m) => m.role === "user");
    const now = Date.now();
    const prev = loadSessions().find((s) => s.id === currentId);
    setSessions(
      saveSession({
        id: currentId,
        title: (firstUser?.text || "Диалог").slice(0, 60),
        createdAt: prev?.createdAt ?? now,
        updatedAt: now,
        messages,
      })
    );
  }, [messages, currentId]);

  function newChat() {
    closeRef.current?.();
    setMessages([]);
    setCurrentId(newId());
  }

  function loadSession(id: string) {
    const s = loadSessions().find((x) => x.id === id);
    if (!s) return;
    closeRef.current?.();
    setBusy(false);
    setMessages(s.messages as Msg[]);
    setCurrentId(s.id);
  }

  function removeSession(id: string) {
    setSessions(deleteSession(id));
    if (id === currentId) newChat();
  }

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // закрываем активный стрим при размонтировании (защита от утечки соединения)
  useEffect(() => () => closeRef.current?.(), []);

  function send(q: string) {
    const question = q.trim();
    if (!question || busy) return;
    closeRef.current?.(); // закрываем предыдущий стрим, если был
    setInput("");
    setBusy(true);
    setMessages((m) => [...m, { role: "user", text: question }, { role: "bot", text: "", streaming: true, model }]);

    closeRef.current = askStream(question, {
      onToken: (piece) =>
        setMessages((m) => {
          const copy = [...m];
          copy[copy.length - 1] = { ...copy[copy.length - 1], text: copy[copy.length - 1].text + piece };
          return copy;
        }),
      onDone: (d) => {
        setMessages((m) => {
          const copy = [...m];
          copy[copy.length - 1] = {
            ...copy[copy.length - 1],
            streaming: false,
            grounded: d.grounded,
            citations: d.citations,
            trace: d.trace,
          };
          return copy;
        });
        setBusy(false);
        closeRef.current = null;
      },
      onError: () => {
        setMessages((m) => {
          const copy = [...m];
          const last = copy[copy.length - 1];
          // обрыв стрима: помечаем ответ как неполный/ошибочный, снимаем цитаты
          copy[copy.length - 1] = {
            ...last,
            streaming: false,
            error: true,
            grounded: false,
            citations: [],
            text: last.text
              ? last.text + "\n\n⚠️ Ответ прерван из-за ошибки связи — возможно, он неполон."
              : "Ошибка соединения с сервером.",
          };
          return copy;
        });
        setBusy(false);
        closeRef.current = null;
      },
    }, model || undefined);
  }

  return (
    <div className="view chat">
      <div className="view__head">
        <div className="view__title">Вопрос-ответ по нормативным документам</div>
        <div className="view__desc">Ответы строго на основе документов организации, с проверяемыми ссылками на пункт и страницу.</div>
      </div>

      <div className="chat__bar">
        <button className="btn btn--ghost btn--sm" onClick={newChat}>+ Новый чат</button>
        {sessions.length > 0 && (
          <select
            className="select select--sm"
            value={sessions.some((s) => s.id === currentId) ? currentId : ""}
            onChange={(e) => e.target.value && loadSession(e.target.value)}
          >
            {!sessions.some((s) => s.id === currentId) && <option value="">История чатов ({sessions.length})</option>}
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>{s.title}</option>
            ))}
          </select>
        )}
        {sessions.some((s) => s.id === currentId) && (
          <button className="btn btn--danger btn--sm" onClick={() => removeSession(currentId)} title="Удалить этот диалог">✕</button>
        )}
      </div>

      <div className="chat__scroll" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="empty">
            <div className="empty__icon"><IconChat className="nav__icon" /></div>
            <div style={{ fontWeight: 600, color: "var(--ink)", fontSize: 16 }}>Задайте вопрос по нормативной базе</div>
            <div style={{ marginTop: 6 }}>Например:</div>
            <div className="suggestions" style={{ justifyContent: "center" }}>
              {SUGGESTIONS.map((s) => (
                <button key={s} className="suggestion" onClick={() => send(s)}>{s}</button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`msg msg--${m.role === "user" ? "user" : "bot"}`}>
            <div className="msg__avatar">{m.role === "user" ? "Вы" : "ИИ"}</div>
            <div style={{ maxWidth: 660 }}>
              <div className="msg__bubble">
                {m.role === "bot" && !m.text && m.streaming ? (
                  <span className="typing"><span></span><span></span><span></span></span>
                ) : (
                  <div className="msg__text"><RichText text={m.text} /></div>
                )}
              </div>
              {m.role === "bot" && !m.streaming && (
                <>
                  <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    {m.error ? (
                      <span className="badge badge--warn">Ошибка связи — ответ может быть неполным</span>
                    ) : m.grounded === false ? (
                      <span className="badge badge--warn">Ответ не найден в документах</span>
                    ) : (
                      m.citations && m.citations.length > 0 && <span className="badge badge--ok">Подтверждено источниками</span>
                    )}
                    {m.model && <span className="badge badge--model" title={m.model}>⚡ {modelLabel(m.model)}</span>}
                  </div>
                  {m.citations && m.citations.length > 0 && (
                    <div className="sources">
                      <div className="sources__title">Источники</div>
                      {m.citations.map((c) => <SourceCard key={c.marker} c={c} />)}
                    </div>
                  )}
                  {m.trace && <Trace trace={m.trace} />}
                </>
              )}
            </div>
          </div>
        ))}
      </div>

      {models.length > 1 && (
        <div className="composer__tools">
          <label className="model-pick">
            <span className="model-pick__label">Модель генерации</span>
            <select
              className="select select--sm"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              disabled={busy}
            >
              {models.map((mm) => (
                <option key={mm.id} value={mm.id}>{mm.label}</option>
              ))}
            </select>
          </label>
          <span className="model-pick__hint">смените модель и спросите снова — чтобы сравнить ответы</span>
        </div>
      )}

      <div className="composer">
        <textarea
          className="textarea"
          placeholder="Введите вопрос по нормативным документам…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(input);
            }
          }}
        />
        <button className="btn btn--primary" disabled={busy || !input.trim()} onClick={() => send(input)}>
          {busy ? <span className="spinner" /> : <IconSend className="nav__icon" />}
          Спросить
        </button>
      </div>
    </div>
  );
}
