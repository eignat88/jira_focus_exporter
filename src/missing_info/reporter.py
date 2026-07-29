import logging
from datetime import datetime
from pathlib import Path

from .analyzer import AnalyzedMatch


REPORT_COLUMNS = [
    "Режим",
    "Что ищем",
    "Категория",
    "Jira-задача",
    "Где найдено",
    "Фрагмент",
    "Статус",
    "Уверенность",
]


def _escape_md(text: str) -> str:
    """Escape pipe characters for markdown table cells."""
    return text.replace("|", "\\|").replace("\n", " ")


def write_report(matches: list[AnalyzedMatch], output_dir: Path) -> Path:
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = output_dir / f"jira_missing_info_report_{ts}.md"

    # Статистика по уровням уверенности
    high = sum(1 for m in matches if m.confidence == "Высокая")
    medium = sum(1 for m in matches if m.confidence == "Средняя")
    low = sum(1 for m in matches if m.confidence == "Низкая")

    lines = [
        "# Отчёт поиска недостающей информации",
        "",
        f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Всего совпадений: {len(matches)}",
        f"- Высокая уверенность: {high}",
        f"- Средняя уверенность: {medium}",
        f"- Низкая уверенность: {low}",
        "",
        "## Результаты",
        "",
        "| Режим | Что ищем | Категория | Jira-задача | Где найдено | Фрагмент | Статус | Уверенность |",
        "|-------|----------|-----------|-------------|-------------|----------|--------|-------------|",
    ]

    for m in matches:
        row = (
            f"| {_escape_md(m.mode)} "
            f"| {_escape_md(m.description)} "
            f"| {_escape_md(m.category)} "
            f"| [{m.issue_key}]({m.issue_url}) "
            f"| {_escape_md(m.found_in)} "
            f"| {_escape_md(m.fragment)} "
            f"| {m.status} "
            f"| {m.confidence} |"
        )
        lines.append(row)

    if not matches:
        lines.append("| — | — | — | — | — | — | — | — |")

    path.write_text("\n".join(lines), encoding="utf-8")
    logging.info("Отчёт сохранён: %s", path)
    return path


def write_log(log_lines: list[str], output_dir: Path) -> Path:
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = output_dir / f"jira_search_log_{ts}.md"

    lines = [
        "# Лог поиска",
        "",
        f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    lines.extend(log_lines)

    path.write_text("\n".join(lines), encoding="utf-8")
    logging.info("Лог сохранён: %s", path)
    return path
