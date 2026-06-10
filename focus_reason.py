from datetime import datetime, timezone

from config import JiraFilterConfig


def parse_jira_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value
    if len(value) >= 5 and value[-5] in {"+", "-"} and value[-3] != ":":
        normalized = f"{value[:-2]}:{value[-2:]}"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def parse_jira_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def jira_datetime_to_utc(value: datetime) -> datetime:
    if value.tzinfo:
        return value.astimezone(timezone.utc)
    return value.replace(tzinfo=timezone.utc)


def get_stale_days(fields: dict, now: datetime | None = None) -> int | None:
    updated = parse_jira_datetime(fields.get("updated"))
    if not updated:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - jira_datetime_to_utc(updated)).days


def build_focus_reason(
    fields: dict,
    config: JiraFilterConfig,
    existing_priorities: list[str] | None = None,
    now: datetime | None = None,
) -> str:
    reasons = []
    now = now or datetime.now(timezone.utc)
    today = now.date()
    priority = (fields.get("priority") or {}).get("name")
    labels = fields.get("labels") or []
    due_date = parse_jira_date(fields.get("duedate"))

    focus_priorities = existing_priorities if existing_priorities is not None else config.focus_priorities
    focus_priority_names = {priority_name.lower() for priority_name in focus_priorities}
    if priority and priority.lower() in focus_priority_names:
        reasons.append(f"высокий приоритет: {priority}")

    if due_date and due_date < today:
        reasons.append("срок просрочен")
    elif due_date and (due_date - today).days <= config.due_soon_days:
        reasons.append(f"срок до {config.due_soon_days} дней")
    elif due_date is None and config.include_empty_due_in_focus:
        reasons.append("срок исполнения не заполнен")

    focus_label_names = {label.lower() for label in config.focus_labels}
    matched_labels = sorted({label for label in labels if label.lower() in focus_label_names})
    if matched_labels:
        reasons.append(f"label: {', '.join(matched_labels)}")

    stale_days = get_stale_days(fields, now)
    if stale_days is not None and stale_days >= config.stale_days:
        reasons.append(f"давно не обновлялась: {stale_days} дней")

    return "; ".join(reasons)


def is_focus_issue(
    fields: dict,
    config: JiraFilterConfig,
    existing_priorities: list[str] | None = None,
    now: datetime | None = None,
) -> bool:
    return bool(build_focus_reason(fields, config, existing_priorities, now))
