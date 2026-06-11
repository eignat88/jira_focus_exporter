import logging
from datetime import datetime, timezone
from pathlib import Path

from config import AppConfig
from filters import clean_jql, quote_values
from jira_client import JiraClient, SEARCH_FIELDS

from .actual_tasks_classifier import (
    classify_actual_task,
    in_recent_window,
    is_group_user,
    user_display,
)
from .actual_tasks_models import ActivityEvent, ActualTask, ActualTasksResult
from .devax12_users_loader import (
    build_identity_set,
    build_jql_user_values,
    load_devax12_users,
)

ACTUAL_TASK_FIELDS = sorted(set(SEARCH_FIELDS + ["creator", "description"]))
CHANGELOG_FIELDS = {
    "status": "status_changed",
    "assignee": "assignee_changed",
    "priority": "priority_changed",
    "duedate": "duedate_changed",
    "fixversion": "fixversion_changed",
    "fix version": "fixversion_changed",
    "fixversions": "fixversion_changed",
    "components": "component_changed",
    "component": "component_changed",
    "labels": "label_changed",
    "description": "description_changed",
    "summary": "summary_changed",
}


def _status_open_clause(config: AppConfig) -> str:
    if config.filters.excluded_status_categories:
        return f"statusCategory not in ({quote_values(config.filters.excluded_status_categories)})"
    return "statusCategory != Done"


def _limited_search(
    client: JiraClient,
    jql: str,
    max_issues: int,
    expand: list[str] | None = None,
) -> list[dict]:
    issues = client.search_issues(
        jql,
        fields=ACTUAL_TASK_FIELDS,
        expand=expand,
        max_results=max_issues,
    )
    if len(issues) >= max_issues:
        logging.warning(
            "Достигнут лимит JIRA_MAX_ISSUES_PER_QUERY=%s для JQL: %s",
            max_issues,
            jql,
        )
    return issues


def build_group_assignee_jql(
    config: AppConfig, jql_user_values: list[str]
) -> str | None:
    if not jql_user_values:
        return None
    clauses = [
        f"assignee in ({quote_values(jql_user_values)})",
        _status_open_clause(config),
    ]
    return clean_jql(" AND ".join(clauses) + " ORDER BY updated DESC")


def build_recent_updated_jql(config: AppConfig, days: int) -> str:
    clauses = [f"updated >= -{days}d", _status_open_clause(config)]
    return clean_jql(" AND ".join(clauses) + " ORDER BY updated DESC")


def build_stale_jql(
    config: AppConfig, jql_user_values: list[str], stale_days: int
) -> str | None:
    if not jql_user_values:
        return None
    clauses = [
        f"assignee in ({quote_values(jql_user_values)})",
        _status_open_clause(config),
        f"updated <= -{stale_days}d",
    ]
    return clean_jql(" AND ".join(clauses) + " ORDER BY updated ASC")


def _merge_issues(target: dict[str, dict], issues: list[dict]) -> None:
    for issue in issues:
        issue_key = issue.get("key")
        if issue_key:
            target[issue_key] = issue


def _event_type_for_field(field_name: str) -> str | None:
    return CHANGELOG_FIELDS.get(field_name.strip().lower())


def extract_group_events(
    issue: dict,
    group_identities: set[str],
    days: int,
    now: datetime,
) -> list[ActivityEvent]:
    issue_key = issue.get("key") or ""
    fields = issue.get("fields") or {}
    events: list[ActivityEvent] = []

    creator = fields.get("creator") or fields.get("reporter") or {}
    if is_group_user(creator, group_identities) and in_recent_window(
        fields.get("created"), days, now
    ):
        events.append(
            ActivityEvent(
                issue_key=issue_key,
                event_type="issue_created",
                author=user_display(creator),
                created=fields.get("created") or "",
            )
        )

    comments = ((fields.get("comment") or {}).get("comments")) or []
    for comment in comments:
        author = comment.get("author") or {}
        if not is_group_user(author, group_identities):
            continue
        events.append(
            ActivityEvent(
                issue_key=issue_key,
                event_type="comment_added",
                author=user_display(author),
                created=comment.get("created") or comment.get("updated") or "",
            )
        )

    for history in ((issue.get("changelog") or {}).get("histories")) or []:
        author = history.get("author") or {}
        if not is_group_user(author, group_identities):
            continue
        created = history.get("created") or ""
        for item in history.get("items") or []:
            field_name = item.get("field") or ""
            event_type = _event_type_for_field(field_name)
            if not event_type:
                continue
            events.append(
                ActivityEvent(
                    issue_key=issue_key,
                    event_type=event_type,
                    author=user_display(author),
                    created=created,
                    field=field_name,
                    from_value=item.get("fromString") or item.get("from") or "",
                    to_value=item.get("toString") or item.get("to") or "",
                )
            )
    return events


def issue_related_to_group(
    issue: dict, group_identities: set[str], events: list[ActivityEvent]
) -> bool:
    fields = issue.get("fields") or {}
    return (
        is_group_user(fields.get("assignee"), group_identities)
        or is_group_user(fields.get("reporter"), group_identities)
        or is_group_user(fields.get("creator"), group_identities)
        or bool(events)
    )


def collect_actual_tasks(
    config: AppConfig,
    client: JiraClient,
    days: int | None = None,
    stale_days: int | None = None,
    generate_report: bool = True,
) -> ActualTasksResult:
    from .actual_tasks_report import export_actual_tasks_report

    logging.info("Старт формирования актуальных задач DEVAX12")
    actual_config = config.actual_tasks
    days = days if days is not None else actual_config.days
    stale_days = stale_days if stale_days is not None else actual_config.stale_days
    users = load_devax12_users(
        actual_config.users_file,
        exclude_inactive=actual_config.exclude_inactive,
        exclude_patterns=actual_config.exclude_patterns,
    )
    group_identities = build_identity_set(users)
    jql_user_values = build_jql_user_values(users)
    max_issues = actual_config.max_issues_per_query
    issues_by_key: dict[str, dict] = {}

    group_jql = build_group_assignee_jql(config, jql_user_values)
    if group_jql:
        group_issues = _limited_search(
            client, group_jql, max_issues, expand=["changelog"]
        )
    else:
        group_issues = []
    logging.info("Получено открытых задач группы: %s", len(group_issues))
    _merge_issues(issues_by_key, group_issues)

    recent_jql = build_recent_updated_jql(config, days)
    recent_issues = _limited_search(
        client, recent_jql, max_issues, expand=["changelog"]
    )
    logging.info("Получено задач, обновлённых за период: %s", len(recent_issues))
    _merge_issues(issues_by_key, recent_issues)

    stale_jql = build_stale_jql(config, jql_user_values, stale_days)
    if stale_jql:
        stale_issues = _limited_search(
            client, stale_jql, max_issues, expand=["changelog"]
        )
        _merge_issues(issues_by_key, stale_issues)
    logging.info("После объединения уникальных задач: %s", len(issues_by_key))

    now = datetime.now(timezone.utc)
    tasks: list[ActualTask] = []
    analyzed_events = 0
    for issue in issues_by_key.values():
        events = extract_group_events(issue, group_identities, days, now)
        analyzed_events += len(events)
        if not issue_related_to_group(issue, group_identities, events):
            continue
        task = classify_actual_task(
            issue, events, group_identities, days, stale_days, now
        )
        if task:
            tasks.append(task)

    tasks.sort(key=lambda task: (task.actual_score, task.updated), reverse=True)
    result = ActualTasksResult(
        tasks=tasks,
        users_count=len(users),
        days=days,
        stale_days=stale_days,
        generated_at=datetime.now(),
    )
    logging.info("Проанализировано changelog/comments: %s", analyzed_events)
    logging.info("Актуальных задач найдено: %s", len(tasks))
    logging.info("Активно в работе: %s", _count_category(tasks, "active_now"))
    logging.info("Требуют внимания: %s", _count_category(tasks, "needs_attention"))
    logging.info("Зависшие: %s", _count_category(tasks, "stale"))
    logging.info("Просроченные: %s", _count_category(tasks, "overdue"))

    if generate_report:
        result.report_path = str(
            export_actual_tasks_report(result, Path(actual_config.output_dir))
        )
        logging.info("Отчёт сохранён: %s", result.report_path)
    return result


def _count_category(tasks: list[ActualTask], category: str) -> int:
    return sum(1 for task in tasks if category in task.categories)
