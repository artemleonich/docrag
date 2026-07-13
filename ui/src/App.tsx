import { useEffect, useState } from "react";
import { getHealth, type Health } from "./api";
import ChatView from "./components/ChatView";
import GenerateView from "./components/GenerateView";
import DocumentsView from "./components/DocumentsView";
import { IconChat, IconDoc, IconSlides, IconLibrary } from "./icons";

type Tab = "chat" | "docx" | "pptx" | "docs";

const NAV: { id: Tab; label: string; Icon: (p: { className?: string }) => JSX.Element; group: string }[] = [
  { id: "chat", label: "Вопрос-ответ", Icon: IconChat, group: "Модули" },
  { id: "docx", label: "Генерация документа", Icon: IconDoc, group: "Модули" },
  { id: "pptx", label: "Генерация презентации", Icon: IconSlides, group: "Модули" },
  { id: "docs", label: "База документов", Icon: IconLibrary, group: "Данные" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("chat");
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    const ping = () => getHealth().then(setHealth).catch(() => setHealth(null));
    ping();
    const iv = setInterval(ping, 15000);
    return () => clearInterval(iv);
  }, []);

  const ok = health?.status === "ok";
  const points = health?.qdrant?.points;

  let group = "";
  return (
    <div className="app">
      <header className="header">
        <div className="header__brand">
          <img className="header__logo" src="/logo.svg" alt="Логотип" />
          <div className="header__titles">
            <span className="header__title">ИИ-ассистент по документам</span>
            <span className="header__subtitle">Локальный офлайн RAG · поиск, цитаты, генерация docx/pptx</span>
          </div>
        </div>
        <div className="header__status" title={health ? `Модель: ${health.model}` : "Сервер недоступен"}>
          <span className={`dot ${health ? (ok ? "dot--ok" : "dot--bad") : "dot--bad"}`} />
          {health ? (ok ? `Готово · ${points ?? 0} фрагментов` : "Ограниченный режим") : "Сервер недоступен"}
        </div>
      </header>

      <div className="body">
        <nav className="nav">
          {NAV.map((n) => {
            const showGroup = n.group !== group;
            group = n.group;
            return (
              <div key={n.id}>
                {showGroup && <div className="nav__group">{n.group}</div>}
                <button className={`nav__item ${tab === n.id ? "nav__item--active" : ""}`} onClick={() => setTab(n.id)}>
                  <n.Icon className="nav__icon" />
                  {n.label}
                </button>
              </div>
            );
          })}
          <div className="nav__spacer" />
          <div className="nav__hint">
            Ответы строятся строго по загруженным документам, со ссылками на пункт и страницу. Ассистент не заменяет юриста.
          </div>
        </nav>

        <main className="main">
          {tab === "chat" && <ChatView />}
          {tab === "docx" && <GenerateView mode="docx" />}
          {tab === "pptx" && <GenerateView mode="pptx" />}
          {tab === "docs" && <DocumentsView />}
        </main>
      </div>
    </div>
  );
}
