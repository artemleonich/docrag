"""Центральная конфигурация системы Docs RAG.

Все параметры читаются из переменных окружения с префиксом ``DOCRAG_`` (см. .env.example),
имеют разумные значения по умолчанию и валидируются pydantic.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень репозитория = на два уровня выше этого файла (src/docrag/settings.py -> repo)
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOCRAG_",
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---
    llm_backend: str = "ollama"
    ollama_host: str = "http://localhost:11434"
    llm_model: str = "hf.co/t-tech/T-lite-it-2.1-GGUF:Q4_K_M"
    llm_num_ctx: int = 8192
    llm_temperature: float = 0.0          # грундинг: детерминированность важнее «креатива»
    llm_max_tokens: int = 1024

    # --- Переключение моделей генерации (селектор в UI) ---
    # Разрешённый список моделей для выбора per-request. Первая = дефолт (== llm_model).
    llm_models: list[str] = [
        "hf.co/t-tech/T-lite-it-2.1-GGUF:Q4_K_M",
        "qwen3.5:9b",
    ]
    # Думающие модели: у них ПРИНУДИТЕЛЬНО отключаем thinking (think=false). Иначе весь
    # бюджет num_predict уходит в рассуждения, а content остаётся пустым. Для грундинга
    # рассуждения не нужны — нужен прямой ответ с цитатами. НЕ слать think другим моделям:
    # у T-lite think=false ломает вывод (вставляет мусорный тег </think>).
    llm_thinking_models: list[str] = ["qwen3.5:9b"]
    # Короткие подписи для UI (id -> label). Для незнакомых id label = сам id.
    llm_model_labels: dict[str, str] = {
        "hf.co/t-tech/T-lite-it-2.1-GGUF:Q4_K_M": "T-lite 2.1 · рус. (Q4)",
        "qwen3.5:9b": "Qwen3.5 · 9B",
    }

    # --- Парсинг PDF ---
    # pymupdf (по умолчанию, без тяжёлых зависимостей) | docling (точное извлечение таблиц,
    # требует `uv sync --extra docling`). При сбое docling — автоматический откат на pymupdf.
    # Читается из DOCRAG_PARSER (короткое имя из доков) или DOCRAG_PARSER_BACKEND.
    parser_backend: str = Field(
        default="pymupdf",
        validation_alias=AliasChoices("DOCRAG_PARSER", "DOCRAG_PARSER_BACKEND"),
    )

    # --- Эмбеддинги / реранкер ---
    embed_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    embed_device: str = "mps"  # mps | cpu | cuda
    embed_use_fp16: bool = True
    embed_batch_size: int = 8
    embed_max_length: int = 8192

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:16333"
    qdrant_collection: str = "docrag_docs"
    qdrant_use_quantization: bool = True    # scalar Int8: индекс -75%, recall восстанавливается реранком

    # --- Индексация (contextual retrieval офлайн) ---
    embed_context_header: bool = True       # дописывать [Документ][Раздел][п. X.Y] перед эмбеддингом

    # --- Чанкинг ---
    child_chunk_tokens: int = 380       # целевой размер child-чанка (символы≈токены*ru)
    child_chunk_overlap: int = 64
    parent_max_tokens: int = 1400       # максимум родительского блока для подачи в LLM

    # --- Ретрив ---
    retrieve_top_k: int = 50            # кандидатов из гибридного поиска (dense+sparse fusion)
    rerank_top_n: int = 6               # финальных чанков после реранка -> в контекст LLM
    hybrid_dense_weight: float = 0.5    # вес dense при слиянии (RRF используется по умолчанию)
    rrf_k: int = 60                     # константа сглаживания Reciprocal Rank Fusion
    min_rerank_score: float = 0.0       # порог отсечения по скору реранкера
    hard_reject_score: float = -4.0     # жёсткий пол: ниже — контекст не подаётся в LLM (честный отказ)

    # --- Скрейпер ---
    base_url: str = ""      # целевой домен-источник PDF — задайте через DOCRAG_BASE_URL
    scraper_delay: float = 3.0
    scraper_ua: str = "docrag-bot/0.1 (+local research)"
    scraper_timeout: float = 60.0
    scraper_max_retries: int = 4

    # --- Пути ---
    data_dir: Path = Field(default=REPO_ROOT / "data")

    # ----- производные пути -----
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def parsed_dir(self) -> Path:
        return self.data_dir / "parsed"

    @property
    def chunks_dir(self) -> Path:
        return self.data_dir / "chunks"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def config_dir(self) -> Path:
        return REPO_ROOT / "config"

    @property
    def assets_dir(self) -> Path:
        return REPO_ROOT / "assets"

    def ensure_dirs(self) -> None:
        for p in (self.raw_dir, self.parsed_dir, self.chunks_dir, self.cache_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
