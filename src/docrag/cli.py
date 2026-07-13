"""CLI Docs RAG.  Примеры:

    uv run docrag ingest                     # полный конвейер: скрейп -> парсинг -> индексация
    uv run docrag add ~/doc.pdf --title "Регламент X" --type rules
    uv run docrag ask "Что означает термин X по вашему документу?"
    uv run docrag serve                      # запустить API
    uv run docrag status
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(add_completion=False, help="Локальный RAG-ассистент по нормативке организации")
console = Console()


@app.command()
def scrape(dry_run: bool = typer.Option(False, help="Только показать, что будет скачано")):
    """Скачать целевой субкорпус нормативных документов с example.com."""
    from docrag.scraper.run import scrape as run_scrape

    recs = run_scrape(dry_run=dry_run)
    console.print(f"[green]Документов:[/green] {len(recs)}")
    for r in recs:
        console.print(f"  • {r['doc_id']}: {r.get('title', r.get('file_name'))}")


@app.command()
def parse(ocr: bool = typer.Option(True, help="OCR для сканов (macOS Vision)")):
    """Распарсить скачанные PDF в структурированный вид."""
    from docrag.parser.run import parse_corpus

    summ = parse_corpus(ocr=ocr)
    for s in summ:
        console.print(f"  • {s['doc_id']}: {s['pages']} стр, {s['nodes']} пунктов ({s['parser']})")


@app.command()
def index():
    """Построить векторный индекс (чанкинг + bge-m3 + Qdrant)."""
    from docrag.indexer.run import build_index

    s = build_index()
    console.print(f"[green]Готово:[/green] {s['chunks']} чанков в коллекции '{s['collection']}'")


@app.command()
def ingest(ocr: bool = typer.Option(True)):
    """Полный конвейер с нуля: скрейп -> парсинг -> индексация."""
    from docrag.indexer.run import build_index
    from docrag.parser.run import parse_corpus
    from docrag.scraper.run import scrape as run_scrape

    console.print("[bold]1/3 Скрейпинг…[/bold]")
    run_scrape()
    console.print("[bold]2/3 Парсинг…[/bold]")
    parse_corpus(ocr=ocr)
    console.print("[bold]3/3 Индексация…[/bold]")
    s = build_index()
    console.print(f"[green]Корпус готов:[/green] {s['chunks']} чанков")


@app.command()
def add(
    path: str = typer.Argument(..., help="Путь к PDF или URL"),
    title: str = typer.Option(..., "--title", "-t", help="Название документа (для цитат)"),
    doc_type: str = typer.Option("regulation", "--type", help="rules|tariff|charter|regulation"),
    doc_id: str = typer.Option(None, help="Явный id (по умолчанию из названия)"),
    date: str = typer.Option(None, help="Дата редакции YYYY-MM-DD"),
    no_ocr: bool = typer.Option(False, help="Отключить OCR"),
):
    """Добавить новый документ в базу (текст или скан — OCR автоматически)."""
    from docrag.ingest import add_document

    s = add_document(path, title=title, doc_id=doc_id, doc_type=doc_type,
                     doc_date=date, ocr=not no_ocr)
    tag = " [скан→OCR]" if s["scanned"] else ""
    console.print(f"[green]Добавлен:[/green] {s['doc_id']}{tag} — "
                  f"{s['pages']} стр, {s['chunks']} чанков, +{s['points_added']} точек")


@app.command()
def remove(doc_id: str = typer.Argument(..., help="id документа для удаления")):
    """Удалить документ из индекса и с диска."""
    from docrag.ingest import remove_document

    left = remove_document(doc_id)
    console.print(f"[yellow]Удалён '{doc_id}'.[/yellow] Осталось точек: {left}")


@app.command()
def ask(question: str = typer.Argument(..., help="Вопрос по нормативке")):
    """Задать вопрос (RAG) и показать ответ с цитатами."""
    from docrag.rag.pipeline import answer as run_answer

    a = run_answer(question)
    console.print(f"\n[bold cyan]Вопрос:[/bold cyan] {question}")
    console.print(f"[bold]Ответ{' (не найдено)' if not a.grounded else ''}:[/bold] {a.answer}\n")
    if a.citations:
        t = Table(title="Источники", show_header=True, header_style="green")
        t.add_column("№"); t.add_column("Документ, пункт, страница")
        for c in a.citations:
            t.add_row(c.marker, c.ref())
        console.print(t)


@app.command()
def status():
    """Показать состояние корпуса и индекса."""
    from docrag.indexer.qdrant_store import collection_info
    from docrag.settings import settings

    info = collection_info()
    console.print(f"[bold]Коллекция:[/bold] {settings.qdrant_collection}")
    console.print(f"[bold]Точек в индексе:[/bold] {info.get('points', 0)} ({info.get('status','—')})")
    parsed = list(settings.parsed_dir.glob("*.json"))
    console.print(f"[bold]Распарсено документов:[/bold] {len([p for p in parsed if p.name!='index.json'])}")


@app.command("eval")
def evaluate(verbose: bool = typer.Option(True)):
    """Прогнать контрольный набор (golden-set) и показать метрики качества."""
    from docrag.eval import run_eval

    res = run_eval(verbose=verbose)
    s = res["summary"]
    console.print("\n[bold]Результаты оценки:[/bold]")
    console.print(f"  Точность грундинга (отказ/ответ): [green]{s['grounding_accuracy']:.0%}[/green]")
    if s["doc_hit_rate"] is not None:
        console.print(f"  Попадание в нужный документ (doc-hit): [green]{s['doc_hit_rate']:.0%}[/green]")
    if s["clause_hit_rate"] is not None:
        console.print(f"  Попадание в нужный пункт (clause-hit): {s['clause_hit_rate']:.0%}")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False):
    """Запустить FastAPI-сервер."""
    import uvicorn

    uvicorn.run("docrag.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
