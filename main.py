import argparse
import logging
import sys
from datetime import datetime, time

from requests.exceptions import HTTPError

from config import AppConfig, VALID_MODES, load_config, validate_config
from exporters import WMS_ACTIVITY_COLUMNS, export_to_files, normalize_issue
from filters import build_assigned_jql, build_focus_jql, build_wms_activity_jql
from focus_reason import build_focus_reason, get_stale_days, parse_jira_datetime
from jira_client import JiraClient
from project_users_exporter import export_project_users


def setup_logging(config: AppConfig) -> None:
    config.log_dir.mkdir(exist_ok=True)
    log_file = config.log_dir / "jira_focus_exporter.log"
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def parse_args(default_mode: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Экспорт задач Jira")
    parser.add_argument(
        "--mode",
        choices=sorted(VALID_MODES),
        default=default_mode,
        help="Режим выгрузки: focus, assigned, wms-activity, project-users или explain",
    )
    parser.add_argument("--issue", help="Ключ задачи для режима explain")
    parser.add_argument(
        "--project",
        help="Ключ проекта для режима project-users (по умолчанию JIRA_PROJECT_USERS_PROJECT_KEY или DEVAX12)",
    )
    return parser.parse_args()


def assignee_display(config: AppConfig) -> str:
    return config.filters.assignee or "currentUser()"


def log_effective_filters(mode: str, config: AppConfig, jql: str) -> None:
    filters = config.filters
    logging.info("Mode: %s", mode)
    logging.info("JIRA_ASSIGNEE: %s", assignee_display(config))
    logging.info("JIRA_PROJECTS: %s", ",".join(filters.projects) or "<all>")
    logging.info("JIRA_ISSUE_TYPES: %s", ",".join(filters.issue_types) or "<all>")
    logging.info(
        "JIRA_EXCLUDED_STATUS_CATEGORIES: %s",
        ",".join(filters.excluded_status_categories) or "<none>",
    )
    logging.info(
        "JIRA_EXCLUDED_STATUSES: %s", ",".join(filters.excluded_statuses) or "<none>"
    )
    logging.info(
        "JIRA_INCLUDED_STATUSES: %s", ",".join(filters.included_statuses) or "<none>"
    )
    logging.info(
        "JIRA_FOCUS_PRIORITIES: %s", ",".join(filters.focus_priorities) or "<none>"
    )
    logging.info("JIRA_FOCUS_LABELS: %s", ",".join(filters.focus_labels) or "<none>")
    logging.info("JIRA_DUE_SOON_DAYS: %s", filters.due_soon_days)
    logging.info("JIRA_STALE_DAYS: %s", filters.stale_days)
    logging.info(
        "JIRA_INCLUDE_EMPTY_DUE_IN_FOCUS: %s", filters.include_empty_due_in_focus
    )
    logging.info("Final JQL: %s", jql)


def run_issue_export(
    mode: str,
    config: AppConfig,
    client: JiraClient,
    jql: str,
    existing_priorities: list[str] | None = None,
) -> tuple[int, object, object]:
    log_effective_filters(mode, config, jql)
    issues = client.search_issues(jql)
    rows = [normalize_issue(issue, config, existing_priorities) for issue in issues]
    csv_path, xlsx_path = export_to_files(rows, config.export_dir, mode)
    return len(rows), csv_path, xlsx_path


def author_identity(user: dict | None) -> set[str]:
    if not user:
        return set()
    return {
        str(value).lower()
        for key in ("accountId", "name", "key", "emailAddress", "displayName")
        if (value := user.get(key))
    }


def is_wms_author(user: dict | None, member_identities: set[str]) -> bool:
    return bool(author_identity(user) & member_identities)


def parse_activity_time(value: str | None) -> datetime | None:
    return parse_jira_datetime(value)


def in_wms_time_window(value: str | None, config: AppConfig) -> bool:
    parsed = parse_activity_time(value)
    if not parsed:
        return False
    local_time = parsed.time().replace(tzinfo=None)
    start = time.fromisoformat(config.wms.activity_from)
    end = time.fromisoformat(config.wms.activity_to)
    return start <= local_time <= end


def collect_wms_activities(
    config: AppConfig, issue: dict, member_identities: set[str]
) -> list[dict]:
    fields = issue.get("fields", {})
    issue_key = issue.get("key")
    summary = fields.get("summary")
    url = f"{config.jira_url}/browse/{issue_key}"
    rows = []

    for history in (issue.get("changelog") or {}).get("histories", []):
        author = history.get("author") or {}
        created = history.get("created")
        if not is_wms_author(author, member_identities) or not in_wms_time_window(
            created, config
        ):
            continue
        changes = []
        for item in history.get("items", []):
            field = item.get("field")
            from_value = item.get("fromString") or ""
            to_value = item.get("toString") or ""
            changes.append(f"{field}: {from_value} -> {to_value}")
        rows.append(
            {
                "issue_key": issue_key,
                "url": url,
                "summary": summary,
                "activity_type": "changelog",
                "author": author.get("displayName"),
                "created": created,
                "updated": fields.get("updated"),
                "details": "; ".join(changes),
            }
        )

    comments = (fields.get("comment") or {}).get("comments") or []
    for comment in comments:
        author = comment.get("author") or {}
        created = comment.get("created")
        if not is_wms_author(author, member_identities) or not in_wms_time_window(
            created, config
        ):
            continue
        rows.append(
            {
                "issue_key": issue_key,
                "url": url,
                "summary": summary,
                "activity_type": "comment",
                "author": author.get("displayName"),
                "created": created,
                "updated": comment.get("updated"),
                "details": (comment.get("body") or "").replace("\n", " "),
            }
        )
    return rows


def get_configured_wms_member_identities(config: AppConfig) -> set[str]:
    return {identity.lower() for identity in config.wms.member_identities}


def get_wms_member_identities(config: AppConfig, client: JiraClient) -> set[str]:
    configured_identities = get_configured_wms_member_identities(config)
    if configured_identities:
        logging.info(
            "JIRA_WMS_MEMBER_IDENTITIES: %s",
            ",".join(config.wms.member_identities),
        )
        logging.info(
            "Участники WMS взяты из JIRA_WMS_MEMBER_IDENTITIES; запрос участников группы Jira пропущен"
        )
        return configured_identities

    logging.info("JIRA_WMS_MEMBER_IDENTITIES: <none>")
    try:
        members = client.get_group_members(config.wms.group_name)
    except HTTPError as error:
        status_code = error.response.status_code if error.response is not None else None
        if status_code in {401, 403}:
            raise RuntimeError(
                "Недостаточно прав для чтения участников группы Jira "
                f"'{config.wms.group_name}' через REST API. "
                "Укажите участников вручную в JIRA_WMS_MEMBER_IDENTITIES "
                "через запятую (логины, displayName, email, key или accountId) "
                "либо запустите скрипт с токеном пользователя, у которого есть права администратора Jira."
            ) from error
        raise

    member_identities = (
        set().union(*(author_identity(member) for member in members))
        if members
        else set()
    )
    if not member_identities:
        logging.warning(
            "Список участников WMS пуст; итоговая выгрузка активности будет пустой"
        )
    return member_identities


def run_wms_activity(
    config: AppConfig, client: JiraClient
) -> tuple[int, object, object]:
    jql = build_wms_activity_jql(config)
    log_effective_filters("wms-activity", config, jql)
    logging.info("JIRA_WMS_GROUP_NAME: %s", config.wms.group_name)
    logging.info("JIRA_WMS_ACTIVITY_FROM: %s", config.wms.activity_from)
    logging.info("JIRA_WMS_ACTIVITY_TO: %s", config.wms.activity_to)
    member_identities = get_wms_member_identities(config, client)
    issues = client.search_issues(jql, expand=["changelog"])
    rows = []
    for issue in issues:
        rows.extend(collect_wms_activities(config, issue, member_identities))
    csv_path, xlsx_path = export_to_files(
        rows,
        config.export_dir,
        "wms_activity",
        columns=WMS_ACTIVITY_COLUMNS,
    )
    return len(rows), csv_path, xlsx_path


def matches_assignee(fields: dict, config: AppConfig, current_user: dict) -> bool:
    assignee = fields.get("assignee") or {}
    if config.filters.assignee:
        expected = config.filters.assignee.lower()
        return expected in author_identity(assignee)
    return bool(author_identity(assignee) & author_identity(current_user))


def is_not_excluded_status(fields: dict, config: AppConfig) -> bool:
    status = fields.get("status") or {}
    status_name = (status.get("name") or "").lower()
    category_name = ((status.get("statusCategory") or {}).get("name") or "").lower()
    filters = config.filters
    if filters.included_statuses and status_name not in {
        item.lower() for item in filters.included_statuses
    }:
        return False
    if status_name in {item.lower() for item in filters.excluded_statuses}:
        return False
    if category_name in {item.lower() for item in filters.excluded_status_categories}:
        return False
    return True


def explain_issue(
    config: AppConfig,
    client: JiraClient,
    issue_key: str,
    current_user: dict,
    existing_priorities: list[str],
) -> None:
    issue = client.get_issue(issue_key)
    fields = issue.get("fields", {})
    assignee = fields.get("assignee") or {}
    status = fields.get("status") or {}
    priority = fields.get("priority") or {}
    labels = fields.get("labels") or []
    assigned_match = matches_assignee(fields, config, current_user)
    status_match = is_not_excluded_status(fields, config)
    focus_reason = build_focus_reason(fields, config.filters, existing_priorities)
    focus_match = assigned_match and status_match and bool(focus_reason)
    stale_days = get_stale_days(fields)

    print(f"Issue: {issue.get('key')}")
    print(f"Assignee: {assignee.get('displayName') or 'пусто'}")
    print(f"Status: {status.get('name') or 'пусто'}")
    print(
        f"Status category: {(status.get('statusCategory') or {}).get('name') or 'пусто'}"
    )
    print(f"Priority: {priority.get('name') or 'пусто'}")
    print(f"Due date: {fields.get('duedate') or 'пусто'}")
    print(f"Labels: {', '.join(labels) if labels else 'пусто'}")
    print(f"Updated: {fields.get('updated') or 'пусто'}")
    print()
    print(f"Попадает в assigned: {'Да' if assigned_match and status_match else 'Нет'}")
    print(f"Попадает в focus: {'Да' if focus_match else 'Нет'}")
    print()
    print("Причины:")
    print(
        f"- задача {'назначена' if assigned_match else 'не назначена'} на выбранного пользователя;"
    )
    print(f"- задача {'не завершена' if status_match else 'исключена по статусу'};")
    priority_name = priority.get("name")
    priority_names = {item.lower() for item in existing_priorities}
    if priority_name and priority_name.lower() in priority_names:
        print(f"- приоритет {priority_name} входит в JIRA_FOCUS_PRIORITIES;")
    else:
        print(
            f"- приоритет {priority_name or 'пусто'} не входит в JIRA_FOCUS_PRIORITIES;"
        )
    if fields.get("duedate"):
        print(f"- срок исполнения заполнен: {fields.get('duedate')};")
    else:
        print("- срок исполнения не заполнен;")
    matched_labels = [
        label
        for label in labels
        if label.lower() in {item.lower() for item in config.filters.focus_labels}
    ]
    if matched_labels:
        print(f"- найдены focus labels: {', '.join(matched_labels)};")
    else:
        print("- focus labels отсутствуют;")
    if stale_days is not None and stale_days >= config.filters.stale_days:
        print(f"- задача давно не обновлялась: {stale_days} дней.")
    else:
        print("- задача обновлялась недавно.")
    if focus_reason:
        print(f"\nfocus_reason: {focus_reason}")


def main() -> None:
    config = load_config()
    setup_logging(config)
    args = parse_args(config.default_mode)

    try:
        logging.info("Старт выгрузки задач Jira")
        validate_config(config)
        client = JiraClient(config)
        current_user = client.check_connection()

        if args.mode == "focus":
            existing_priorities = client.get_existing_focus_priorities()
            jql = build_focus_jql(config.filters, existing_priorities)
            count, csv_path, xlsx_path = run_issue_export(
                "focus", config, client, jql, existing_priorities
            )
        elif args.mode == "assigned":
            existing_priorities = client.get_existing_focus_priorities()
            jql = build_assigned_jql(config.filters)
            count, csv_path, xlsx_path = run_issue_export(
                "assigned", config, client, jql, existing_priorities
            )
        elif args.mode == "wms-activity":
            count, csv_path, xlsx_path = run_wms_activity(config, client)
        elif args.mode == "project-users":
            project_key = args.project or config.project_users.project_key
            logging.info("Mode: project-users")
            logging.info("JIRA_PROJECT_USERS_PROJECT_KEY: %s", project_key)
            count, csv_path, xlsx_path = export_project_users(
                project_key,
                client,
                config.export_dir,
            )
        elif args.mode == "explain":
            if not args.issue:
                raise ValueError(
                    "Для режима explain укажите --issue, например --issue DAX-11253"
                )
            existing_priorities = client.get_existing_focus_priorities()
            explain_issue(config, client, args.issue, current_user, existing_priorities)
            return
        else:
            raise ValueError(f"Неизвестный режим: {args.mode}")

        logging.info("Выгрузка завершена успешно")
        logging.info("Количество строк: %s", count)
        print()
        print("Готово.")
        print(f"Найдено строк: {count}")
        print(f"CSV: {csv_path.resolve()}")
        print(f"Excel: {xlsx_path.resolve()}")
    except Exception as error:
        logging.exception("Ошибка выполнения скрипта: %s", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
