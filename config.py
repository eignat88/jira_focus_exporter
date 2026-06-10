import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

VALID_MODES = {"focus", "assigned", "wms-activity", "project-users", "explain"}


@dataclass
class JiraFilterConfig:
    assignee: str
    excluded_status_categories: list[str]
    excluded_statuses: list[str]
    included_statuses: list[str]
    focus_priorities: list[str]
    focus_labels: list[str]
    due_soon_days: int
    stale_days: int
    include_medium_in_focus: bool
    include_empty_due_in_focus: bool
    projects: list[str]
    issue_types: list[str]
    focus_extra_jql: str
    assigned_extra_jql: str


@dataclass
class WmsConfig:
    group_name: str
    member_identities: list[str]
    activity_from: str
    activity_to: str
    activity_extra_jql: str


@dataclass
class ProjectUsersConfig:
    project_key: str


@dataclass
class AppConfig:
    jira_url: str
    jira_token: str
    export_dir: Path
    log_dir: Path
    default_mode: str
    filters: JiraFilterConfig
    wms: WmsConfig
    project_users: ProjectUsersConfig


def parse_csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on", "да"}


def parse_positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} должен быть целым числом") from error
    if value < 0:
        raise ValueError(f"{name} не может быть отрицательным")
    return value


def load_config() -> AppConfig:
    load_dotenv()

    focus_priorities = parse_csv_list(
        os.getenv("JIRA_FOCUS_PRIORITIES", "High,Highest,Critical,Blocker")
    )
    if parse_bool(os.getenv("JIRA_INCLUDE_MEDIUM_IN_FOCUS"), False):
        priority_names = {priority.lower() for priority in focus_priorities}
        if "medium" not in priority_names:
            focus_priorities.append("Medium")

    default_mode = os.getenv("JIRA_DEFAULT_MODE", "focus").strip() or "focus"
    if default_mode not in VALID_MODES - {"explain"}:
        raise ValueError(
            "JIRA_DEFAULT_MODE должен быть одним из: focus, assigned, wms-activity, project-users"
        )

    return AppConfig(
        jira_url=os.getenv("JIRA_URL", "").rstrip("/"),
        jira_token=os.getenv("JIRA_TOKEN", ""),
        export_dir=Path(os.getenv("JIRA_EXPORT_DIR", "exports")),
        log_dir=Path(os.getenv("JIRA_LOG_DIR", "logs")),
        default_mode=default_mode,
        filters=JiraFilterConfig(
            assignee=os.getenv("JIRA_ASSIGNEE", "").strip(),
            excluded_status_categories=parse_csv_list(
                os.getenv("JIRA_EXCLUDED_STATUS_CATEGORIES", "Done")
            ),
            excluded_statuses=parse_csv_list(os.getenv("JIRA_EXCLUDED_STATUSES", "")),
            included_statuses=parse_csv_list(os.getenv("JIRA_INCLUDED_STATUSES", "")),
            focus_priorities=focus_priorities,
            focus_labels=parse_csv_list(
                os.getenv("JIRA_FOCUS_LABELS", "focus,urgent,critical")
            ),
            due_soon_days=parse_positive_int("JIRA_DUE_SOON_DAYS", 7),
            stale_days=parse_positive_int("JIRA_STALE_DAYS", 3),
            include_medium_in_focus=parse_bool(
                os.getenv("JIRA_INCLUDE_MEDIUM_IN_FOCUS"), False
            ),
            include_empty_due_in_focus=parse_bool(
                os.getenv("JIRA_INCLUDE_EMPTY_DUE_IN_FOCUS"), False
            ),
            projects=parse_csv_list(os.getenv("JIRA_PROJECTS", "")),
            issue_types=parse_csv_list(os.getenv("JIRA_ISSUE_TYPES", "")),
            focus_extra_jql=os.getenv("JIRA_FOCUS_EXTRA_JQL", "").strip(),
            assigned_extra_jql=os.getenv("JIRA_ASSIGNED_EXTRA_JQL", "").strip(),
        ),
        wms=WmsConfig(
            group_name=os.getenv("JIRA_WMS_GROUP_NAME", "wms").strip() or "wms",
            member_identities=parse_csv_list(
                os.getenv("JIRA_WMS_MEMBER_IDENTITIES")
                or os.getenv("JIRA_WMS_MEMBERS", "")
            ),
            activity_from=os.getenv("JIRA_WMS_ACTIVITY_FROM", "09:00").strip()
            or "09:00",
            activity_to=os.getenv("JIRA_WMS_ACTIVITY_TO", "17:50").strip() or "17:50",
            activity_extra_jql=os.getenv("JIRA_WMS_ACTIVITY_EXTRA_JQL", "").strip(),
        ),
        project_users=ProjectUsersConfig(
            project_key=os.getenv("JIRA_PROJECT_USERS_PROJECT_KEY", "DEVAX12").strip()
            or "DEVAX12",
        ),
    )


def validate_config(config: AppConfig) -> None:
    if not config.jira_url:
        raise ValueError("Не заполнен JIRA_URL в .env")
    if not config.jira_token:
        raise ValueError("Не заполнен JIRA_TOKEN в .env")
