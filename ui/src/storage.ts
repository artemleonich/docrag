// Локальное хранилище истории чатов (localStorage). Инструмент офлайн-однопользовательский,
// поэтому серверная БД избыточна — сессии с полными сообщениями/цитатами/трейсом живут в браузере.

export interface StoredSession {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: unknown[]; // Msg[] из ChatView — храним целиком (цитаты и трейс восстанавливаются)
}

const KEY = "docrag.chat.sessions";
const MAX_SESSIONS = 50;

export function loadSessions(): StoredSession[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr)) return [];
    return (arr as StoredSession[]).sort((a, b) => b.updatedAt - a.updatedAt);
  } catch {
    return [];
  }
}

export function saveSession(session: StoredSession): StoredSession[] {
  const sessions = loadSessions().filter((s) => s.id !== session.id);
  sessions.unshift(session);
  const trimmed = sessions.sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_SESSIONS);
  try {
    localStorage.setItem(KEY, JSON.stringify(trimmed));
  } catch {
    /* превышен лимит localStorage — молча пропускаем */
  }
  return trimmed;
}

export function deleteSession(id: string): StoredSession[] {
  const sessions = loadSessions().filter((s) => s.id !== id);
  try {
    localStorage.setItem(KEY, JSON.stringify(sessions));
  } catch {
    /* ignore */
  }
  return sessions;
}

export function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `s_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}
