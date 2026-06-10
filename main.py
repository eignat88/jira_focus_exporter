import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv


load_dotenv()

JIRA_URL = os.getenv("JIRA_URL", "").rstrip("/")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")
JIRA_ASSIGNEE = os.getenv("JIRA_ASSIGNEE", "").strip()
JIRA_FOCUS_PRIORITIES = os.getenv(
    "JIRA_FOCUS_PRIORITIES",
    "High,Highest,Critical,Blocker",
)
EXPORT_DIR = Path(os.getenv("JIRA_EXPORT_DIR", "exports"))
LOG_DIR = Path(os.getenv("JIRA_LOG_DIR", "logs"))

EXPORT_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / "jira_focus_exporter.log"
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
HIGH_PRIORITY_NAMES = {"highest", "high", "critical", "blocker"}
FOCUS_LABELS = {"focus", "urgent", "critical"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)


def validate_config():
    if not JIRA_URL:
        raise ValueError("Не заполнен JIRA_URL в .env")

    if not JIRA_TOKEN:
        raise ValueError("Не заполнен JIRA_TOKEN в .env")


def get_headers():
    return {
        "Authorization": f"Bearer {JIRA_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def check_connection():
    url = f"{JIRA_URL}/rest/api/2/myself"
    response = requests.get(url, headers=get_headers(), timeout=30)

    if response.status_code != 200:
        logging.error("Ошибка подключения к Jira")
        logging.error("HTTP status: %s", response.status_code)
        logging.error("Response: %s", response.text)
        response.raise_for_status()

    data = response.json()
    logging.info(
        "Подключение к Jira успешно. Пользователь: %s",
        data.get("displayName") or data.get("name") or data.get("emailAddress"),
    )
    return data


def escape_jql_value(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def get_assignee_jql():
    if JIRA_ASSIGNEE:
        return f'assignee = "{escape_jql_value(JIRA_ASSIGNEE)}"'

    return "assignee = currentUser()"


def parse_csv_list(value):
    return [item.strip() for item in value.split(",") if item.strip()]


def get_configured_focus_priorities():
    return parse_csv_list(JIRA_FOCUS_PRIORITIES)


def get_available_priority_names():
    url = f"{JIRA_URL}/rest/api/2/priority"
    response = requests.get(url, headers=get_headers(), timeout=30)

    if response.status_code != 200:
        logging.warning("Не удалось получить список приоритетов Jira")
        logging.warning("HTTP status: %s", response.status_code)
        logging.warning("Response: %s", response.text)
        return None

    return {priority.get("name") for priority in response.json() if priority.get("name")}


def get_existing_focus_priorities():
    configured_priorities = get_configured_focus_priorities()
    if not configured_priorities:
        logging.info("Фильтр по приоритетам отключён: JIRA_FOCUS_PRIORITIES пустой")
        return []

    available_priorities = get_available_priority_names()
    if available_priorities is None:
        logging.warning(
            "Фильтр по приоритетам отключён, чтобы не получить ошибку JQL "
            "из-за неизвестных значений"
        )
        return []

    available_by_lower = {priority.lower(): priority for priority in available_priorities}
    existing_priorities = [
        available_by_lower[priority.lower()]
        for priority in configured_priorities
        if priority.lower() in available_by_lower
    ]
    missing_priorities = [
        priority
        for priority in configured_priorities
        if priority.lower() not in available_by_lower
    ]

    if missing_priorities:
        logging.warning(
            "Эти приоритеты отсутствуют в Jira и будут исключены из JQL: %s",
            ", ".join(missing_priorities),
        )

    if existing_priorities:
        logging.info("Приоритеты для focus-фильтра: %s", ", ".join(existing_priorities))
    else:
        logging.warning(
            "Ни один настроенный приоритет не найден в Jira; "
            "условие priority будет исключено из JQL"
        )

    return existing_priorities


def build_priority_jql(priority_names):
    if not priority_names:
        return None

    escaped_priorities = [
        f'"{escape_jql_value(priority)}"'
        for priority in priority_names
    ]
    return f"priority in ({', '.join(escaped_priorities)})"


def build_focus_conditions(priority_names):
    conditions = [
        "due <= 7d",
        "due < now()",
        "labels in (focus, urgent, critical)",
        "updated <= -3d",
    ]
    priority_jql = build_priority_jql(priority_names)
    if priority_jql:
        conditions.insert(0, priority_jql)

    return conditions


def build_focus_jql(priority_names=None):
    """
    JQL для задач, которые требуют фокуса.

    Логика:
    1. Задача назначена на JIRA_ASSIGNEE или на текущего пользователя.
    2. Задача не завершена.
    3. Задача важная, срочная, просроченная, скоро подходит срок,
       давно не обновлялась или помечена label'ом focus.
    """
    if priority_names is None:
        priority_names = get_configured_focus_priorities()

    assignee_jql = get_assignee_jql()
    focus_conditions = build_focus_conditions(priority_names)
    focus_conditions_jql = " OR ".join(focus_conditions)
    jql = f"""
    {assignee_jql}
    AND statusCategory != Done
    AND ({focus_conditions_jql})
    ORDER BY priority DESC, due ASC, updated ASC
    """
    return " ".join(jql.split())


def search_issues(jql, max_results_per_page=50):
    all_issues = []
    start_at = 0

    while True:
        url = f"{JIRA_URL}/rest/api/2/search"
        payload = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": max_results_per_page,
            "fields": [
                "summary",
                "status",
                "priority",
                "assignee",
                "reporter",
                "created",
                "updated",
                "duedate",
                "labels",
                "project",
                "issuetype",
                "components",
                "fixVersions",
            ],
        }

        logging.info("Запрос задач Jira. startAt=%s", start_at)
        response = requests.post(url, headers=get_headers(), json=payload, timeout=60)

        if response.status_code != 200:
            logging.error("Ошибка поиска задач")
            logging.error("HTTP status: %s", response.status_code)
            logging.error("Response: %s", response.text)
            response.raise_for_status()

        data = response.json()
        issues = data.get("issues", [])
        total = data.get("total", 0)
        all_issues.extend(issues)

        logging.info("Получено задач: %s из %s", len(all_issues), total)

        start_at += max_results_per_page
        if start_at >= total:
            break

    return all_issues


def parse_jira_datetime(value):
    if not value:
        return None

    normalized = value
    if len(value) >= 5 and value[-5] in {"+", "-"} and value[-3] != ":":
        normalized = f"{value[:-2]}:{value[-2:]}"

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        logging.warning("Не удалось разобрать дату Jira: %s", value)
        return None


def parse_jira_date(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        logging.warning("Не удалось разобрать дату Jira: %s", value)
        return None


def build_focus_reason(fields):
    reasons = []
    priority = (fields.get("priority") or {}).get("name")
    labels = fields.get("labels") or []
    updated = parse_jira_datetime(fields.get("updated"))
    due_date = parse_jira_date(fields.get("duedate"))
    today = datetime.now(timezone.utc).date()

    if priority and priority.lower() in HIGH_PRIORITY_NAMES:
        reasons.append("высокий приоритет")

    if due_date and due_date < today:
        reasons.append("срок просрочен")
    elif due_date and (due_date - today).days <= 7:
        reasons.append("срок до 7 дней")

    matched_labels = sorted({label for label in labels if label.lower() in FOCUS_LABELS})
    if matched_labels:
        reasons.append(f"label: {', '.join(matched_labels)}")

    if updated:
        if updated.tzinfo:
            updated_utc = updated.astimezone(timezone.utc)
        else:
            updated_utc = updated.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - updated_utc).days >= 3:
            reasons.append("давно не обновлялась")

    return "; ".join(reasons)


def join_names(items):
    return ", ".join(item.get("name", "") for item in items if item.get("name"))


def normalize_issue(issue):
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
        "url": f"{JIRA_URL}/browse/{issue_key}",
        "project": project.get("key"),
        "issue_type": issue_type.get("name"),
        "summary": fields.get("summary"),
        "status": status.get("name"),
        "status_category": (status.get("statusCategory") or {}).get("name"),
        "priority": priority.get("name"),
        "focus_reason": build_focus_reason(fields),
        "assignee": assignee.get("displayName"),
        "reporter": reporter.get("displayName"),
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "due_date": fields.get("duedate"),
        "labels": ", ".join(labels),
        "components": join_names(components),
        "fix_versions": join_names(fix_versions),
    }


def export_to_files(rows):
    now = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    csv_path = EXPORT_DIR / f"jira_focus_tasks_{now}.csv"
    xlsx_path = EXPORT_DIR / f"jira_focus_tasks_{now}.xlsx"
    df = pd.DataFrame(rows, columns=EXPORT_COLUMNS)

    if df.empty:
        logging.info("Нет задач для выгрузки.")

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False)

    logging.info("CSV сохранён: %s", csv_path.resolve())
    logging.info("Excel сохранён: %s", xlsx_path.resolve())
    return csv_path, xlsx_path


def main():
    try:
        logging.info("Старт выгрузки задач Jira")
        validate_config()
        check_connection()
        logging.info("Фильтр исполнителя Jira: %s", JIRA_ASSIGNEE or "currentUser()")

        priority_names = get_existing_focus_priorities()
        jql = build_focus_jql(priority_names)
        logging.info("JQL: %s", jql)

        issues = search_issues(jql)
        rows = [normalize_issue(issue) for issue in issues]
        csv_path, xlsx_path = export_to_files(rows)

        logging.info("Выгрузка завершена успешно")
        logging.info("Количество задач: %s", len(rows))

        print()
        print("Готово.")
        print(f"Найдено задач: {len(rows)}")
        print(f"CSV: {csv_path.resolve()}")
        print(f"Excel: {xlsx_path.resolve()}")
    except Exception as error:
        logging.exception("Ошибка выполнения скрипта: %s", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
