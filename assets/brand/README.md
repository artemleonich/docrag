# Брендовые ассеты / Brand assets

Положите сюда свой логотип для генерации .docx/.pptx:

- `logo.png` — основной логотип (для светлых шапок документов);
- `logo-white.png` — белый вариант (для тёмных слайдов презентаций).

Для веб-интерфейса логотип лежит отдельно: `ui/public/logo.svg`.

Файлы не входят в репозиторий (`.gitignore`); при их отсутствии система использует
текстовый вариант (название организации из `DOCRAG_ORG_NAME`).

---

Drop your logo here for .docx/.pptx generation: `logo.png` (light) and `logo-white.png`
(dark slides). The web UI logo lives at `ui/public/logo.svg`. Missing files fall back to text.
