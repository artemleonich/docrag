"""Брендинг: палитра, название организации (из окружения) и пути к логотипам.

Название организации подставляется в шапки .docx/.pptx и системный промпт; задаётся
через переменные окружения DOCRAG_ORG_NAME / DOCRAG_ORG_FULL (см. .env.example).
По умолчанию — нейтральный плейсхолдер. Логотипы — свои, положите в assets/brand/.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

# --- Палитра (замените под свой бренд) ---
BRAND = {
    "primary": "#0F6857",
    "primary_dark": "#0B4D40",
    "accent": "#2ABA6A",
    "accent_dark": "#287945",
    "slate": "#4D5A5F",
    "ink": "#1A2327",
    "muted": "#6B7A80",
    "line": "#E2E8E6",
    "bg": "#F5F8F7",
    "surface": "#FFFFFF",
    "white": "#FFFFFF",
    "subtitle": "#BFE3D5",   # светло-мятный текст на тёмном (primary) фоне слайдов
}

ORG_NAME = os.getenv("DOCRAG_ORG_NAME", "Организация")
ORG_FULL = os.getenv("DOCRAG_ORG_FULL", "Организация")
PRODUCT_NAME = os.getenv("DOCRAG_PRODUCT_NAME", "ИИ-ассистент по нормативным документам")

# Реквизиты для официальных бланков (справка / проект ответа). Пустые поля НЕ печатаются —
# заполните значениями своей организации (ИНН/ОГРН не выдумываем, оставлены пустыми).
REQUISITES: dict[str, str] = {
    "inn": "",
    "ogrn": "",
    "address": "",
    "phone": "",
    "email": "",
    "site": os.getenv("DOCRAG_ORG_SITE", ""),
}

# Подпись под официальным документом (должность подписанта заполните под задачу).
SIGN_ROLE = os.getenv("DOCRAG_SIGN_ROLE", "Уполномоченный сотрудник")

LOGO_SVG = _REPO / "assets" / "brand" / "logo.svg"
LOGO_PNG = _REPO / "assets" / "brand" / "logo.png"
LOGO_PNG_WHITE = _REPO / "assets" / "brand" / "logo-white.png"   # для тёмных фонов


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
