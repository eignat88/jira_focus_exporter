import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import AppConfig
from focus_reason import build_focus_reason


EXPORT_COLUMNS = [
    "key",
    "url",
    "project",
    "issue_type",
    "summary",
    "status",
    "status_category",
    "priority",
    "focus_reason",
    "assignee",
    "reporter",
    "created",
    "updated",
    "due_date",
    "labels",
    "components",
    "fix_versions",
]

WMS_ACTIVITY_COLUMNS = [
    "issue_key",
    "url",
    "summary",
    "activity_type",
    "author",
    "created",
    "updated",
    "details",
]


def join_names(items: list[dict]) -> str:
    return ", ".join(item.get("name", "") for item in items if item.get("name"))


def normalize_issue(
    issue: dict,
    config: AppConfig,
    existing_priorities: list[str] | None = None,
) -> dict:
    fields = issue.get("fields", {})
    project = fields.get("project") or {}
    issue_type = fields.get("issuetype") or {}
    status = fields.get("status") or {}
    priority = fields.get("priority") or {}
    assignee = fields.get("assignee") or {}
    reporter = fields.get("reporter") or {}
    components = fields.get("components") or []
    fix_versions = fields.get("fixVersions") or []
    labels = fields.get("labels") or []
    issue_key = issue.get("key")

    return {
        "key": issue_key,
        "url": f"{config.jira_url}/browse/{issue_key}",
        "project": project.get("key"),
        "issue_type": issue_type.get("name"),
        "summary": fields.get("summary"),
        "status": status.get("name"),
        "status_category": (status.get("statusCategory") or {}).get("name"),
        "priority": priority.get("name"),
        "focus_reason": build_focus_reason(
            fields,
            config.filters,
            existing_priorities,
        ),
        "assignee": assignee.get("displayName"),
        "reporter": reporter.get("displayName"),
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "due_date": fields.get("duedate"),
        "labels": ", ".join(labels),
        "components": join_names(components),
        "fix_versions": join_names(fix_versions),
    }


def export_to_files(
    rows: list[dict],
    export_dir: Path,
    mode: str,
    columns: list[str] | None = None,
) -> tuple[Path, Path]:
    export_dir.mkdir(exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    csv_path = export_dir / f"jira_{mode}_tasks_{now}.csv"
    xlsx_path = export_dir / f"jira_{mode}_tasks_{now}.xlsx"
    df = pd.DataFrame(rows, columns=columns or EXPORT_COLUMNS)

    if df.empty:
        logging.info("Нет задач для выгрузки.")

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False)

    logging.info("CSV сохранён: %s", csv_path.resolve())
    logging.info("Excel сохранён: %s", xlsx_path.resolve())
    return csv_path, xlsx_path
