#!/bin/bash
# Останавливает сервисы приложения (бэкенд + фронт). Qdrant/Ollama не трогает.
pkill -f "uvicorn docrag.api.app" 2>/dev/null && echo "Бэкенд остановлен"
pkill -f "vite" 2>/dev/null && echo "Фронтенд остановлен"
echo "Готово. Qdrant и Ollama оставлены работать (остановить: docker compose down)."
