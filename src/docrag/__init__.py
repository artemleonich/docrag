"""Пакет Docs RAG.

Форсируем офлайн-режим HuggingFace: все веса (bge-m3, реранкер) уже в локальном кэше,
поэтому обращения в интернет не нужны — это требование «полностью офлайн».
Значения можно переопределить через окружение до импорта пакета.
"""

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
