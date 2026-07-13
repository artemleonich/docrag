#!/usr/bin/env bash
# Запуск приложения DocRAG: бэкенд (FastAPI) + фронт (Vite) + открытие браузера.
# Запуск: ./scripts/run.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# Ollama-сервер (если не запущен)
if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "▸ Запускаю Ollama…"
  nohup ollama serve >/tmp/docrag-ollama.log 2>&1 &
  for i in $(seq 1 20); do curl -s http://localhost:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
fi

# Qdrant (если не запущен)
if ! curl -s http://localhost:16333/ >/dev/null 2>&1; then
  echo "▸ Поднимаю Qdrant…"
  docker compose up -d
  for i in $(seq 1 30); do curl -s http://localhost:16333/ >/dev/null 2>&1 && break; sleep 1; done
fi

PIDS=()
cleanup() { echo; echo "Остановка…"; for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

echo "▸ Бэкенд FastAPI на http://127.0.0.1:8010 …"
# PYTHONPATH=src делает пакет импортируемым независимо от состояния editable-инсталла
PYTHONPATH=src uv run --no-sync python -m uvicorn docrag.api.app:app --host 127.0.0.1 --port 8010 &
PIDS+=($!)

echo "▸ Фронтенд Vite на http://localhost:5173 …"
( cd ui && npm run dev ) &
PIDS+=($!)

sleep 3
echo "▸ Открываю браузер…"
open http://localhost:5173 2>/dev/null || true

echo
echo "Приложение запущено. Нажмите Ctrl+C для остановки."
wait
