# DocRAG

**Локальный офлайн RAG-ассистент по вашим PDF-документам: вопрос-ответ со строгими проверяемыми цитатами + генерация .docx/.pptx.**
_Local, fully-offline RAG assistant over your own PDFs: grounded Q&A with verifiable citations + .docx/.pptx generation._

Работает полностью локально (без внешних API): парсинг PDF → гибридный поиск → реранк → грундинг-генерация через локальную LLM. Каждый ответ — с проверяемыми ссылками на пункт и страницу; если ответа в документах нет — честный отказ, а не выдумка.

> ⚠️ Корпус документов в репозиторий **не входит** — добавьте свои PDF (см. «Быстрый старт»). / No document corpus is shipped — bring your own PDFs.

---

## 🇷🇺 Русский

### Возможности
- **Вопрос-ответ (RAG)** по загруженным PDF со **строгими цитатами** уровня «Документ, п. 4.2.1, стр. 12».
- **Гибридный поиск** (dense + sparse, слияние RRF) + **cross-encoder реранк** + порог отсечения («честный отказ»).
- **Детерминированные цитаты** — строятся из метаданных, модель их не выдумывает.
- **Выбор LLM** в интерфейсе (переключение моделей для сравнения ответов).
- **История чатов** (локально) и **история сгенерированных документов** (с повторным скачиванием).
- **Генерация .docx** (справка / выписка / проект ответа) и **.pptx** (презентация) с предпросмотром до скачивания.
- **База документов**: загрузка PDF (в т.ч. сканов — OCR), просмотр и скачивание исходников.
- **Брендинг из конфига**: название организации и логотип настраиваются, ничего не захардкожено.

### Архитектура (два развязанных процесса)
- **Офлайн-индексация** (CLI): парсинг PDF (PyMuPDF, +OCR для сканов) → иерархический чанкинг (small-to-big) → эмбеддинги bge-m3 (dense+sparse) → Qdrant.
- **Онлайн-запрос** (FastAPI): гибридный поиск (RRF) → реранк (bge-reranker-v2-m3) → retrieval-rail → грундинг-генерация (Ollama) → цитаты из метаданных по маркерам `[N]`.

### Стек
Python 3.12 · FastAPI · Qdrant · Ollama · bge-m3 · bge-reranker-v2-m3 · PyMuPDF · python-docx / python-pptx · React + Vite + TypeScript.

### Требования
- **Python 3.12** и [`uv`](https://github.com/astral-sh/uv)
- **Docker** (для Qdrant) или свой инстанс Qdrant
- **[Ollama](https://ollama.com)** + модели: `ollama pull hf.co/t-tech/T-lite-it-2.1-GGUF:Q4_K_M` и `ollama pull bge-m3` (LLM по умолчанию — русскоязычная T-lite; можно заменить, напр. на `qwen3.5:9b`)
- Node.js 18+ (для веб-интерфейса)

### Быстрый старт
```bash
# 1. Зависимости
uv sync
cp .env.example .env        # при необходимости отредактируйте

# 2. Сервисы
docker compose up -d        # Qdrant
ollama serve                # если ещё не запущен

# 3. Добавьте свои документы и проиндексируйте
uv run docrag add путь/к/документу.pdf --title "Название" --type rules

# 4. Спросите из CLI…
uv run docrag ask "ваш вопрос по документам"

# …или запустите приложение (бэкенд :8010 + фронт :5173)
./scripts/run.sh
```

### Команды CLI
```bash
uv run docrag add <pdf> --title "..." --type rules   # добавить документ (текст/скан)
uv run docrag ask "вопрос"                           # спросить из CLI
uv run docrag eval                                   # метрики на golden-set (tests/golden.json)
uv run docrag ingest                                 # скрейп+парсинг+индексация (если настроен источник)
```

### Конфигурация
Все параметры — через переменные окружения с префиксом `DOCRAG_` (см. `.env.example`): модель LLM, устройство эмбеддингов, URL Qdrant, параметры ретрива, название организации для шапок документов и т.д.

**Табличные документы (Docling):** для точного извлечения таблиц поставьте `uv sync --extra docling` и задайте `DOCRAG_PARSER=docling` — таблицы распознаются и сохраняют структуру колонок (по умолчанию — быстрый PyMuPDF; при сбое Docling — авто-откат на него).

### Свой брендинг
- Название организации — `DOCRAG_ORG_NAME` / `DOCRAG_ORG_FULL` в `.env`.
- Логотип — положите `logo.svg` в `ui/public/` (веб) и `logo.png` / `logo-white.png` в `assets/brand/` (для .docx/.pptx). Без логотипа используется текстовый вариант.
- Палитра — `src/docrag/branding.py` (`BRAND`).

### Лицензия
MIT — см. файл [`LICENSE`](LICENSE).

---

## 🇬🇧 English

### Features
- **Grounded Q&A (RAG)** over your PDFs with **strict citations** like “Document, §4.2.1, p. 12”.
- **Hybrid retrieval** (dense + sparse, RRF fusion) + **cross-encoder reranking** + a cutoff gate (honest refusal instead of hallucination).
- **Deterministic citations** — built from metadata, never invented by the model.
- **In-UI model switcher** to compare answers across LLMs.
- **Chat history** (client-side) and **generated-document history** (with re-download).
- **.docx** (memo / extract / draft reply) and **.pptx** (slide deck) generation with preview-before-download.
- **Document library**: upload PDFs (including scans via OCR), view and download originals.
- **Config-driven branding**: organization name and logo are configurable — nothing hardcoded.

### Architecture (two decoupled processes)
- **Offline indexing** (CLI): parse PDF (PyMuPDF, +OCR for scans) → hierarchical (small-to-big) chunking → bge-m3 embeddings (dense+sparse) → Qdrant.
- **Online query** (FastAPI): hybrid search (RRF) → rerank (bge-reranker-v2-m3) → retrieval-rail → grounded generation (Ollama) → citations from metadata via `[N]` markers.

### Stack
Python 3.12 · FastAPI · Qdrant · Ollama · bge-m3 · bge-reranker-v2-m3 · PyMuPDF · python-docx / python-pptx · React + Vite + TypeScript.

### Requirements
- **Python 3.12** and [`uv`](https://github.com/astral-sh/uv)
- **Docker** (for Qdrant) or your own Qdrant instance
- **[Ollama](https://ollama.com)** + models: `ollama pull hf.co/t-tech/T-lite-it-2.1-GGUF:Q4_K_M` and `ollama pull bge-m3` (the default LLM is configurable — e.g. `qwen3.5:9b`)
- Node.js 18+ (for the web UI)

### Quickstart
```bash
uv sync
cp .env.example .env
docker compose up -d                 # Qdrant
ollama serve                         # if not already running
uv run docrag add path/to/doc.pdf --title "Title" --type rules
uv run docrag ask "your question"
./scripts/run.sh                     # backend :8010 + frontend :5173
```

### Configuration
All settings are environment variables prefixed `DOCRAG_` (see `.env.example`): LLM model, embedding device, Qdrant URL, retrieval params, organization name for document headers, etc.

**Table-heavy documents (Docling):** for accurate table extraction run `uv sync --extra docling` and set `DOCRAG_PARSER=docling` — tables are detected and keep their column structure (default is fast PyMuPDF; on any Docling failure it auto-falls back).

### Branding
Set `DOCRAG_ORG_NAME` / `DOCRAG_ORG_FULL` in `.env`; drop `logo.svg` into `ui/public/` and `logo.png` / `logo-white.png` into `assets/brand/`; tweak the palette in `src/docrag/branding.py`. Missing logos fall back to text.

### License
MIT — see [`LICENSE`](LICENSE).
