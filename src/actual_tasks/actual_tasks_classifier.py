from datetime import datetime, timedelta, timezone

from focus_reason import jira_datetime_to_utc, parse_jira_date, parse_jira_datetime

from .actual_tasks_models import ActivityEvent, ActualTask

WORKING_STATUSES = {
    "in progress",
    "development",
    "review",
    "testing",
    "в работе",
    "разработка",
    "тестирование",
}
REVIEW_TESTING_STATUSES = {"review", "testing", "тестирование"}
TODO_STATUSES = {"to do", "todo", "open", "открыто", "сделать"}
HIGH_PRIORITIES = {
    "critical",
    "blocker",
    "highest",
    "high",
    "критический",
    "блокер",
    "высокий",
}
CRITICAL_PRIORITIES = {"critical", "blocker", "highest", "критический", "блокер"}


def user_identities(user: dict | None) -> set[str]:
    if not user:
        return set()
    return {
        str(value).strip().lower()
        for key in ("accountId", "name", "key", "displayName", "emailAddress")
        if (value := user.get(key))
    }


def user_display(user: dict | None) -> str:
    if not user:
        return ""
    return (
        user.get("displayName")
        or user.get("emailAddress")
        or user.get("name")
        or user.get("key")
        or user.get("accountId")
        or ""
    )


def is_group_user(user: dict | None, group_identities: set[str]) -> bool:
    return bool(user_identities(user) & group_identities)


def in_recent_window(value: str | None, days: int, now: datetime) -> bool:
    parsed = parse_jira_datetime(value)
    if not parsed:
        return False
    return jira_datetime_to_utc(parsed) >= now - timedelta(days=days)


def days_since(value: str | None, now: datetime) -> int | None:
    parsed = parse_jira_datetime(value)
    if not parsed:
        return None
    return (now - jira_datetime_to_utc(parsed)).days


def normalize_status(value: str) -> str:
    return value.strip().lower()


def event_datetime(event: ActivityEvent) -> datetime | None:
    return parse_jira_datetime(event.created)


def event_is_recent(event: ActivityEvent, days: int, now: datetime) -> bool:
    return in_recent_window(event.created, days, now)


def classify_actual_task(
    issue: dict,
    events: list[ActivityEvent],
    group_identities: set[str],
    days: int,
    stale_days: int,
    now: datetime | None = None,
) -> ActualTask | None:
    now = now or datetime.now(timezone.utc)
    today = now.date()
    fields = issue.get("fields") or {}
    status = fields.get("status") or {}
    status_name = status.get("name") or ""
    status_category = (status.get("statusCategory") or {}).get("name") or ""
    priority = (fields.get("priority") or {}).get("name") or ""
    assignee = fields.get("assignee") or {}
    reporter = fields.get("reporter") or fields.get("creator") or {}
    creator = fields.get("creator") or {}
    due_date = parse_jira_date(fields.get("duedate"))
    updated_days = days_since(fields.get("updated"), now)
    created_recently = in_recent_window(fields.get("created"), days, now)
    updated_recently = in_recent_window(fields.get("updated"), days, now)
    is_done = status_category.lower() == "done"
    assignee_in_group = is_group_user(assignee, group_identities)
    reporter_in_group = is_group_user(reporter, group_identities)
    creator_in_group = is_group_user(creator, group_identities)
    recent_events = [event for event in events if event_is_recent(event, days, now)]
    recent_event_types = {event.event_type for event in recent_events}
    recent_group_activity = bool(recent_events)
    status_lower = normalize_status(status_name)
    working_status = status_lower in WORKING_STATUSES
    todo_status = status_lower in TODO_STATUSES
    review_testing = status_lower in REVIEW_TESTING_STATUSES
    high_priority = priority.lower() in HIGH_PRIORITIES
    critical_priority = priority.lower() in CRITICAL_PRIORITIES
    due_soon = due_date is not None and today <= due_date <= today + timedelta(days=1)
    overdue = due_date is not None and due_date < today and not is_done
    stale = (
        not is_done
        and assignee_in_group
        and updated_days is not None
        and updated_days >= stale_days
    )
    open_related_to_group = not is_done and (
        assignee_in_group or reporter_in_group or creator_in_group or bool(events)
    )

    score = 0
    reasons: list[str] = []
    categories: list[str] = []

    if recent_group_activity:
        score += 10
        reasons.append("Есть активность участника DEVAX12 за период")
    if working_status:
        score += 8
        reasons.append("Статус рабочий")
    if assignee_in_group:
        score += 7
        reasons.append("Задача назначена на участника DEVAX12")
    if "status_changed" in recent_event_types:
        score += 6
        reasons.append("Была смена статуса за период")
    if "comment_added" in recent_event_types:
        score += 5
        reasons.append("Есть комментарий участника DEVAX12")
    if critical_priority:
        score += 5
        reasons.append(f"Критический приоритет: {priority}")
    elif high_priority:
        reasons.append(f"Высокий приоритет: {priority}")
    if due_soon:
        score += 4
        reasons.append("Срок выполнения сегодня или завтра")
    if created_recently:
        score += 4
        reasons.append("Задача создана за период")
    if updated_recently:
        score += 3
        reasons.append("Задача обновлялась за период")
    if review_testing:
        score += 3
        reasons.append("Задача находится в Review / Testing")
    if todo_status and recent_group_activity:
        score += 2
        reasons.append("Задача в To Do, но активно обсуждается")
    if stale:
        score -= 5
        reasons.append(f"Нет активности больше {stale_days} дней")
    if overdue:
        reasons.append("Задача просрочена")

    if (
        working_status
        and not is_done
        and assignee_in_group
        and (recent_group_activity or updated_recently)
    ):
        categories.append("active_now")
    if updated_recently and recent_group_activity:
        categories.append("changed_recently")
    if (
        overdue
        or high_priority
        or due_soon
        or stale
        or (
            assignee_in_group
            and updated_days is not None
            and updated_days >= stale_days
        )
        or (todo_status and recent_group_activity)
    ) and not is_done:
        categories.append("needs_attention")
    if stale:
        categories.append("stale")
    if overdue:
        categories.append("overdue")
    if open_related_to_group and not recent_group_activity and not updated_recently:
        categories.append("backlog_actual")

    is_actual = not is_done and (
        assignee_in_group
        or working_status
        or updated_recently
        or recent_group_activity
        or high_priority
        or due_soon
        or overdue
        or stale
    )
    if not is_actual and not categories:
        return None

    active_users = sorted({event.author for event in events if event.author})
    if assignee_in_group and user_display(assignee):
        active_users.append(user_display(assignee))
    active_users = sorted(set(active_users))
    sorted_events = sorted(events, key=lambda event: event.created or "", reverse=True)
    last_activity = (
        sorted_events[0].created if sorted_events else fields.get("updated") or ""
    )
    days_overdue = (today - due_date).days if overdue and due_date else None

    return ActualTask(
        issue_key=issue.get("key") or "",
        summary=fields.get("summary") or "",
        status=status_name,
        status_category=status_category,
        assignee=user_display(assignee),
        reporter=user_display(reporter),
        priority=priority,
        created=fields.get("created") or "",
        updated=fields.get("updated") or "",
        due_date=fields.get("duedate") or "",
        actual_score=score,
        categories=categories,
        active_users=active_users,
        activity_events=sorted_events,
        reasons=reasons,
        raw_issue=issue,
        days_without_activity=updated_days,
        days_overdue=days_overdue,
        last_activity=last_activity,
    )
