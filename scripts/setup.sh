#!/usr/bin/env bash
# Подготовка окружения: Qdrant, модель Ollama, зависимости. Запуск: ./scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "▸ 1/4 Python-зависимости (uv sync, с OCR)…"
uv sync --extra ocr

echo "▸ 2/4 Qdrant (docker compose)…"
docker compose up -d
for i in $(seq 1 30); do
  curl -s http://localhost:16333/ >/dev/null 2>&1 && { echo "  Qdrant готов."; break; }
  sleep 1
done

echo "▸ 3/4 Ollama-сервер…"
if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "  Запустите Ollama: 'ollama serve' (или приложение Ollama), затем повторите."
fi

echo "▸ 4/4 Модель T-lite-it-2.1 + эмбеддер bge-m3…"
ollama pull hf.co/t-tech/T-lite-it-2.1-GGUF:Q4_K_M
ollama pull bge-m3 || true

echo "▸ UI-зависимости…"
( cd ui && npm install )

echo
echo "Готово. Дальше:"
echo "  uv run docrag ingest      # собрать корпус (скрейп + парсинг + индексация)"
echo "  ./scripts/run.sh        # запустить приложение"
