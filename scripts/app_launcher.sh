#!/bin/bash
# Лаунчер Mac-приложения «DocRAG»: поднимает сервисы и открывает
# приложение в отдельном окне без вкладок браузера (выглядит как нативная программа).
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Корень проекта — на уровень выше этого скрипта (scripts/..)
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$HOME/Library/Logs/docrag-assistant.log"
URL="http://127.0.0.1:5173"

notify() { osascript -e "display notification \"$1\" with title \"DocRAG\"" >/dev/null 2>&1; }
fail()   { osascript -e "display dialog \"$1\" buttons {\"OK\"} with icon caution with title \"DocRAG\"" >/dev/null 2>&1; exit 1; }

cd "$PROJECT" 2>/dev/null || fail "Проект RAG-docrag не найден: $PROJECT"
mkdir -p "$(dirname "$LOG")"
echo "=== $(date) запуск ===" >> "$LOG"

command -v uv >/dev/null 2>&1 || fail "Не найден uv. Установите зависимости: см. README."

notify "Запускаю сервисы…"

# 1. Ollama (LLM)
if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
  open -a Ollama >/dev/null 2>&1 || nohup ollama serve >>"$LOG" 2>&1 &
fi

# 2. Qdrant (векторная БД) — через Docker
if ! curl -s http://localhost:16333/ >/dev/null 2>&1; then
  if command -v docker >/dev/null 2>&1; then
    docker info >/dev/null 2>&1 || open -a Docker >/dev/null 2>&1
    for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
    docker compose up -d >>"$LOG" 2>&1
  fi
fi

# 3. Бэкенд FastAPI (порт 8010)
if ! curl -s http://127.0.0.1:8010/health >/dev/null 2>&1; then
  PYTHONPATH=src nohup uv run --no-sync python -m uvicorn docrag.api.app:app \
    --host 127.0.0.1 --port 8010 >>"$LOG" 2>&1 &
fi

# 4. Фронтенд Vite (порт 5173)
if ! curl -s "$URL" >/dev/null 2>&1; then
  ( cd "$PROJECT/ui" && nohup npm run dev >>"$LOG" 2>&1 & )
fi

# Ждём готовности фронта и бэкенда
for i in $(seq 1 90); do
  curl -s "$URL" >/dev/null 2>&1 && curl -s http://127.0.0.1:8010/health >/dev/null 2>&1 && break
  sleep 1
done

if ! curl -s "$URL" >/dev/null 2>&1; then
  fail "Не удалось запустить приложение. Лог: $LOG"
fi

notify "Готово — открываю окно"

# Открываем в отдельном окне без адресной строки/вкладок (app-mode)
if [ -d "/Applications/Google Chrome.app" ]; then
  open -na "Google Chrome" --args --app="$URL" --user-data-dir="$HOME/.docrag-app-window" >/dev/null 2>&1
elif [ -d "/Applications/Microsoft Edge.app" ]; then
  open -na "Microsoft Edge" --args --app="$URL" --user-data-dir="$HOME/.docrag-app-window" >/dev/null 2>&1
else
  open "$URL"
fi
