<h1 align="center">DocRAG</h1>

<p align="center"><strong>Ваши PDF → ответы с источниками → документы и презентации.</strong></p>

<p align="center">
  <img src=".github/assets/stack.svg" height="28" alt="Python · FastAPI · React" />
</p>

Локальный RAG-ассистент для работы с документами: гибридный поиск, ответы со ссылками на пункт и страницу, генерация Word и PowerPoint.

[Быстрый старт](#быстрый-старт) · [Конфигурация](#конфигурация) · [CLI](#cli) · [English](#english)

> Корпус документов в репозиторий не входит — добавьте свои PDF. Обработка и генерация работают локально; для установки зависимостей и первой загрузки моделей нужен интернет.

## Возможности

- **Ответы с источниками.** Ссылки на документ, пункт и страницу строятся из метаданных; пороги релевантности позволяют отклонять вопросы без подходящего контекста.
- **Гибридный поиск.** Dense + sparse эмбеддинги `bge-m3`, объединение RRF и cross-encoder реранк `bge-reranker-v2-m3`.
- **Библиотека PDF.** Загрузка, просмотр и скачивание оригиналов; OCR для сканов при установленных дополнительных зависимостях.
- **Word и PowerPoint.** Справки, выписки, проекты ответов и презентации с предпросмотром и историей скачиваний.
- **Веб-интерфейс.** Выбор локальной LLM, история чатов в браузере и история сгенерированных файлов.
- **Брендинг.** Название организации, логотипы и палитра настраиваются.

## Стек и архитектура

Python 3.12 · FastAPI · Qdrant · Ollama · FlagEmbedding / Transformers · PyMuPDF · python-docx / python-pptx · React · TypeScript · Vite.

| Этап | Конвейер |
| --- | --- |
| Индексация | PDF → парсинг / OCR → иерархические фрагменты → dense + sparse эмбеддинги → Qdrant |
| Ответ | Гибридный поиск → реранк → проверка релевантности → локальная LLM → ссылки из метаданных |
| Экспорт | Ответ и источники → предпросмотр → `.docx` / `.pptx` |

## Быстрый старт

Нужны Python **3.12**, [uv](https://github.com/astral-sh/uv), Docker с Compose, [Ollama](https://ollama.com) и Node.js **18+** для интерфейса. По умолчанию используется MPS; если он недоступен, вычисления переключаются на CPU.

### 1. Установите зависимости

```bash
git clone https://github.com/artemleonich/docrag.git
cd docrag
uv sync
cp .env.example .env
npm ci --prefix ui
```

### 2. Запустите сервисы и загрузите LLM

```bash
docker compose up -d
```

Если Ollama ещё не запущена, выполните `ollama serve` в отдельном терминале. Затем:

```bash
ollama pull hf.co/t-tech/T-lite-it-2.1-GGUF:Q4_K_M
```

Эмбеддер `BAAI/bge-m3` и реранкер `BAAI/bge-reranker-v2-m3` загружаются отдельно через Python при первом использовании. После загрузки моделей работа с локальными PDF не требует внешнего API.

### 3. Добавьте PDF и задайте вопрос

```bash
uv run docrag add путь/к/документу.pdf --title "Название документа" --type rules
uv run docrag ask "Ваш вопрос по документу"
```

### 4. Откройте веб-приложение

```bash
./scripts/run.sh
```

Интерфейс: [localhost:5173](http://localhost:5173). API: [127.0.0.1:8010](http://127.0.0.1:8010). Qdrant: `localhost:16333`. Для остановки приложения нажмите `Ctrl+C`.

## Конфигурация

Настройки читаются из `.env` с префиксом `DOCRAG_`; примеры — в [`.env.example`](.env.example), полный список — в [settings.py](src/docrag/settings.py).

| Параметр | Назначение |
| --- | --- |
| `DOCRAG_LLM_MODEL` | Модель генерации в Ollama |
| `DOCRAG_EMBED_DEVICE` | Устройство: `mps`, `cpu` или `cuda` |
| `DOCRAG_QDRANT_URL` | Адрес Qdrant |
| `DOCRAG_PARSER` | Парсер: `pymupdf` или `docling` |
| `DOCRAG_ORG_NAME` / `DOCRAG_ORG_FULL` | Название организации |
| `DOCRAG_DATA_DIR` | Каталог локальных данных |
| `DOCRAG_BASE_URL` | Источник PDF для необязательного скрейпера |

**Сканы:** установите `uv sync --extra ocr`. На macOS используется Vision через `ocrmac`; запасной вариант — RapidOCR, качество кириллицы может отличаться.

**Таблицы:** установите `uv sync --extra docling` и задайте `DOCRAG_PARSER=docling`. Первая индексация может загружать модели; при сбое Docling используется PyMuPDF. Дополнения можно объединять: `uv sync --extra ocr --extra docling`.

**Логотипы:** `ui/public/logo.svg` — веб-интерфейс; `assets/brand/logo.png` и `logo-white.png` — экспорт. Без файлов используется текстовое оформление. Палитра задаётся в [branding.py](src/docrag/branding.py); подробнее — [assets/brand/README.md](assets/brand/README.md).

## CLI

```bash
uv run docrag add путь/к/документу.pdf --title "Название" --type rules
uv run docrag ask "Вопрос"
uv run docrag status
uv run docrag eval
uv run docrag ingest
```

`eval` использует [tests/golden.json](tests/golden.json): контрольный набор нужно согласовать со своим корпусом. `ingest` запускает скрейпинг, парсинг и индексацию после настройки источника.

## Разработка

```bash
uv sync --extra dev
uv run pytest -m "not slow"
npm run build --prefix ui
```

Сквозные тесты с меткой `slow` требуют работающих Qdrant и Ollama, загруженных моделей и корпуса, соответствующего контрольному набору.

```text
src/docrag/   API, CLI, парсинг, индексация, RAG и генераторы
ui/          React-интерфейс
scripts/     установка, запуск и остановка
tests/       проверки и контрольный набор
assets/      оформление экспортируемых документов
```

## English

DocRAG is a local PDF assistant with hybrid retrieval, source citations, and Word / PowerPoint generation. It uses FastAPI, Qdrant, Ollama, BGE models, and a React / TypeScript interface.

Bring your own PDFs. Internet access is needed for dependencies and initial model downloads; document processing and generation use local models. Follow the quick-start commands above, then open `http://localhost:5173`. Install the `ocr` extra for scans or the `docling` extra for table-heavy documents. Configure `DOCRAG_*` variables in `.env`.

## Лицензия

[MIT](LICENSE).
