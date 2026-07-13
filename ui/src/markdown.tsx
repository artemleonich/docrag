// Лёгкий рендер markdown → React (без внешних зависимостей, офлайн).
// Поддержка: заголовки #/##/###, списки маркированные и нумерованные, **жирный**, `код`,
// абзацы с переносами, и маркеры цитат [N] как выделенные span (класс .cite).
import type { ReactNode } from "react";

// маркеры цитат согласованы с бэком: [1], [1,2], [1, 2], [1-3]
const CITE = /^\[\s*\d+(?:\s*[,\-–]\s*\d+)*\s*\]$/;
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[\s*\d+(?:\s*[,\-–]\s*\d+)*\s*\])/g;

function inline(text: string, kp = ""): ReactNode[] {
  return text.split(INLINE).map((p, i) => {
    if (/^\*\*[\s\S]+\*\*$/.test(p)) return <strong key={kp + i}>{p.slice(2, -2)}</strong>;
    if (/^`[^`]+`$/.test(p)) return <code key={kp + i} className="md-code">{p.slice(1, -1)}</code>;
    if (CITE.test(p)) return <span key={kp + i} className="cite">{p}</span>;
    return <span key={kp + i}>{p}</span>;
  });
}

function withBreaks(text: string): ReactNode[] {
  // префикс ключей индексом строки — иначе ключи inline() (0,1,2…) дублируются между строками
  return text.split("\n").flatMap((ln, i) => (i === 0 ? inline(ln, "l0-") : [<br key={`b${i}`} />, ...inline(ln, `l${i}-`)]));
}

export function RichText({ text }: { text: string }) {
  const blocks = (text || "").trim().split(/\n{2,}/);
  return (
    <>
      {blocks.map((block, bi) => {
        const lines = block.split("\n");
        const heading = lines.length === 1 ? block.match(/^(#{1,3})\s+(.*)$/) : null;
        const isBullet = lines.length > 0 && lines.every((l) => /^\s*[-*•▸]\s+/.test(l));
        const isNumbered = lines.length > 0 && lines.every((l) => /^\s*\d+[.)]\s+/.test(l));
        if (heading) {
          return (
            <div key={bi} className={`md-h md-h${heading[1].length}`}>{inline(heading[2])}</div>
          );
        }
        if (isBullet) {
          return (
            <ul key={bi} className="md-ul">
              {lines.map((l, i) => <li key={i}>{inline(l.replace(/^\s*[-*•▸]\s+/, ""))}</li>)}
            </ul>
          );
        }
        if (isNumbered) {
          return (
            <ol key={bi} className="md-ol">
              {lines.map((l, i) => <li key={i}>{inline(l.replace(/^\s*\d+[.)]\s+/, ""))}</li>)}
            </ol>
          );
        }
        return <p key={bi} className="md-p">{withBreaks(block)}</p>;
      })}
    </>
  );
}
