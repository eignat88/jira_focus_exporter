from pathlib import Path

import pandas as pd

from .actual_tasks_models import ActualTask, ActualTasksResult

SUMMARY_ROWS = [
    ("Всего актуальных задач", lambda result: len(result.tasks)),
    ("Активно в работе", lambda result: _count_category(result.tasks, "active_now")),
    (
        "Изменялись за период",
        lambda result: _count_category(result.tasks, "changed_recently"),
    ),
    (
        "Требуют внимания",
        lambda result: _count_category(result.tasks, "needs_attention"),
    ),
    ("Зависшие", lambda result: _count_category(result.tasks, "stale")),
    ("Просроченные", lambda result: _count_category(result.tasks, "overdue")),
    ("Участников DEVAX12 из файла", lambda result: result.users_count),
    ("Период анализа", lambda result: f"{result.days} дн."),
    ("Порог зависания", lambda result: f"{result.stale_days} дн."),
    (
        "Дата формирования",
        lambda result: result.generated_at.strftime("%Y-%m-%d %H:%M:%S"),
    ),
]


def _count_category(tasks: list[ActualTask], category: str) -> int:
    return sum(1 for task in tasks if category in task.categories)


def _join(values: list[str]) -> str:
    return ", ".join(values)


def summary_rows(result: ActualTasksResult) -> list[dict]:
    return [
        {"Показатель": name, "Значение": getter(result)}
        for name, getter in SUMMARY_ROWS
    ]


def actual_tasks_rows(tasks: list[ActualTask]) -> list[dict]:
    return [
        {
            "Issue Key": task.issue_key,
            "Summary": task.summary,
            "Status": task.status,
            "Assignee": task.assignee,
            "Priority": task.priority,
            "Updated": task.updated,
            "Due Date": task.due_date,
            "Actual Score": task.actual_score,
            "Categories": _join(task.categories),
            "Reasons": "; ".join(task.reasons),
        }
        for task in tasks
    ]


def active_now_rows(tasks: list[ActualTask]) -> list[dict]:
    return [
        {
            "Issue Key": task.issue_key,
            "Summary": task.summary,
            "Status": task.status,
            "Assignee": task.assignee,
            "Active Users": _join(task.active_users),
            "Last Activity": task.last_activity,
            "Events": _join([event.event_type for event in task.activity_events]),
            "Actual Score": task.actual_score,
        }
        for task in tasks
        if "active_now" in task.categories
    ]


def changed_recently_rows(tasks: list[ActualTask]) -> list[dict]:
    return [
        {
            "Issue Key": task.issue_key,
            "Summary": task.summary,
            "Status": task.status,
            "Assignee": task.assignee,
            "Active Users": _join(task.active_users),
            "Changed Fields": _join(
                sorted({event.field for event in task.activity_events if event.field})
            ),
            "Last Activity": task.last_activity,
        }
        for task in tasks
        if "changed_recently" in task.categories
    ]


def needs_attention_rows(tasks: list[ActualTask]) -> list[dict]:
    return [
        {
            "Issue Key": task.issue_key,
            "Summary": task.summary,
            "Status": task.status,
            "Assignee": task.assignee,
            "Priority": task.priority,
            "Due Date": task.due_date,
            "Reason": "; ".join(task.reasons),
            "Actual Score": task.actual_score,
        }
        for task in tasks
        if "needs_attention" in task.categories
    ]


def stale_rows(tasks: list[ActualTask]) -> list[dict]:
    return [
        {
            "Issue Key": task.issue_key,
            "Summary": task.summary,
            "Status": task.status,
            "Assignee": task.assignee,
            "Updated": task.updated,
            "Days Without Activity": task.days_without_activity,
        }
        for task in tasks
        if "stale" in task.categories
    ]


def overdue_rows(tasks: list[ActualTask]) -> list[dict]:
    return [
        {
            "Issue Key": task.issue_key,
            "Summary": task.summary,
            "Status": task.status,
            "Assignee": task.assignee,
            "Due Date": task.due_date,
            "Days Overdue": task.days_overdue,
        }
        for task in tasks
        if "overdue" in task.categories
    ]


def event_rows(tasks: list[ActualTask]) -> list[dict]:
    rows = []
    for task in tasks:
        for event in task.activity_events:
            rows.append(
                {
                    "Issue Key": event.issue_key,
                    "Event Type": event.event_type,
                    "Author": event.author,
                    "Created": event.created,
                    "Field": event.field,
                    "From": event.from_value,
                    "To": event.to_value,
                }
            )
    return rows


def raw_issue_rows(tasks: list[ActualTask]) -> list[dict]:
    return [task.as_dict() for task in tasks]


def export_actual_tasks_report(result: ActualTasksResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = result.generated_at.strftime("%Y-%m-%d_%H%M")
    output_path = output_dir / f"devax12_actual_tasks_{timestamp}.xlsx"

    sheets = {
        "Summary": summary_rows(result),
        "Actual Tasks": actual_tasks_rows(result.tasks),
        "Active Now": active_now_rows(result.tasks),
        "Changed Recently": changed_recently_rows(result.tasks),
        "Needs Attention": needs_attention_rows(result.tasks),
        "Stale": stale_rows(result.tasks),
        "Overdue": overdue_rows(result.tasks),
        "Events": event_rows(result.tasks),
        "Raw Issues": raw_issue_rows(result.tasks),
    }
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for sheet_name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, index=False)
    return output_path
