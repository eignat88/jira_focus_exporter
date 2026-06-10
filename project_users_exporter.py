import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from requests.exceptions import HTTPError

from config import AppConfig, load_config, validate_config
from exporters import export_to_files
from filters import escape_jql_value
from jira_client import JiraClient

PROJECT_USERS_COLUMNS = [
    "project_key",
    "account_id",
    "name",
    "key",
    "display_name",
    "email",
    "active",
    "roles",
    "source_groups",
]


@dataclass
class ProjectUserRow:
    project_key: str
    account_id: str = ""
    name: str = ""
    key: str = ""
    display_name: str = ""
    email: str = ""
    active: bool | None = None
    roles: set[str] = field(default_factory=set)
    source_groups: set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "project_key": self.project_key,
            "account_id": self.account_id,
            "name": self.name,
            "key": self.key,
            "display_name": self.display_name,
            "email": self.email,
            "active": self.active,
            "roles": ", ".join(sorted(self.roles)),
            "source_groups": ", ".join(sorted(self.source_groups)),
        }


def user_identity(user: dict) -> str:
    for field_name in ("accountId", "name", "key", "emailAddress", "displayName"):
        value = user.get(field_name)
        if value:
            return f"{field_name}:{str(value).lower()}"
    return f"unknown:{id(user)}"


def merge_user(
    users_by_identity: dict[str, ProjectUserRow],
    project_key: str,
    user: dict,
    role_name: str,
    source_group: str = "",
) -> None:
    identity = user_identity(user)
    row = users_by_identity.get(identity)
    if row is None:
        row = ProjectUserRow(
            project_key=project_key,
            account_id=user.get("accountId") or "",
            name=user.get("name") or "",
            key=user.get("key") or "",
            display_name=user.get("displayName") or "",
            email=user.get("emailAddress") or "",
            active=user.get("active"),
        )
        users_by_identity[identity] = row

    row.roles.add(role_name)
    if source_group:
        row.source_groups.add(source_group)


def actor_group_name(actor: dict) -> str:
    group = actor.get("actorGroup") or {}
    return group.get("name") or actor.get("name") or actor.get("displayName") or ""


def actor_user(actor: dict) -> dict | None:
    user = actor.get("actorUser") or {}
    if user:
        return user
    if actor.get("type") == "atlassian-user-role-actor":
        return actor
    return None


def is_permission_error(error: HTTPError) -> bool:
    return bool(
        error.response is not None and error.response.status_code in {401, 403}
    )


def collect_project_users_from_roles(
    project_key: str, client: JiraClient
) -> dict[str, ProjectUserRow]:
    logging.info("Получение ролей проекта Jira: %s", project_key)
    roles = client.get_project_roles(project_key)
    users_by_identity: dict[str, ProjectUserRow] = {}

    for role_name, role_url in sorted(roles.items()):
        logging.info(
            "Получение участников роли '%s' проекта %s", role_name, project_key
        )
        role = client.get_project_role(role_url)
        for actor in role.get("actors", []):
            user = actor_user(actor)
            if user:
                merge_user(users_by_identity, project_key, user, role_name)
                continue

            group_name = actor_group_name(actor)
            if not group_name:
                logging.warning(
                    "Неизвестный actor в роли '%s' проекта %s пропущен: %s",
                    role_name,
                    project_key,
                    actor,
                )
                continue

            try:
                members = client.get_group_members(group_name)
            except HTTPError as error:
                if is_permission_error(error):
                    raise RuntimeError(
                        "Недостаточно прав для чтения участников группы Jira "
                        f"'{group_name}' из роли '{role_name}' проекта {project_key}. "
                        "Без раскрытия групп нельзя выгрузить полный список пользователей проекта. "
                        "Запустите скрипт с токеном пользователя, у которого есть права на чтение групп."
                    ) from error
                raise

            logging.info(
                "Группа '%s' в роли '%s': получено участников %s",
                group_name,
                role_name,
                len(members),
            )
            for member in members:
                merge_user(
                    users_by_identity, project_key, member, role_name, group_name
                )

    return users_by_identity


def collect_assignable_project_users(
    project_key: str, client: JiraClient, users_by_identity: dict[str, ProjectUserRow]
) -> None:
    logging.info(
        "Упрощённая выгрузка назначаемых пользователей проекта Jira: %s",
        project_key,
    )
    users = client.get_assignable_users(project_key)
    logging.info(
        "Через assignable/search для проекта %s получено пользователей: %s",
        project_key,
        len(users),
    )
    for user in users:
        merge_user(users_by_identity, project_key, user, "Assignable user")


def collect_issue_participant_users(
    project_key: str, client: JiraClient, users_by_identity: dict[str, ProjectUserRow]
) -> None:
    jql = f'project = "{escape_jql_value(project_key)}" ORDER BY updated DESC'
    logging.info(
        "Дополнительная выгрузка пользователей из задач проекта Jira: %s",
        project_key,
    )
    issues = client.search_issues(
        jql,
        fields=["assignee", "reporter", "creator"],
    )
    logging.info(
        "Для проекта %s получено задач для анализа пользователей: %s",
        project_key,
        len(issues),
    )
    for issue in issues:
        fields = issue.get("fields") or {}
        for field_name, role_name in (
            ("assignee", "Issue assignee"),
            ("reporter", "Issue reporter"),
            ("creator", "Issue creator"),
        ):
            user = fields.get(field_name)
            if user:
                merge_user(users_by_identity, project_key, user, role_name)


def collect_project_users(project_key: str, client: JiraClient) -> list[dict]:
    try:
        users_by_identity = collect_project_users_from_roles(project_key, client)
    except HTTPError as error:
        if not is_permission_error(error):
            raise
        logging.warning(
            "Нет прав на чтение ролей проекта %s через /project/%s/role. "
            "Будет выполнена упрощённая выгрузка без ролей и групп: "
            "назначаемые пользователи проекта + пользователи, найденные в задачах проекта.",
            project_key,
            project_key,
        )
        users_by_identity = {}
        assignable_error = None
        try:
            collect_assignable_project_users(project_key, client, users_by_identity)
        except HTTPError as assignable_http_error:
            assignable_error = assignable_http_error
            logging.warning(
                "Не удалось получить назначаемых пользователей проекта %s; "
                "пробуем собрать пользователей из задач проекта",
                project_key,
            )

        try:
            collect_issue_participant_users(project_key, client, users_by_identity)
        except HTTPError as issue_http_error:
            if assignable_error is not None:
                raise RuntimeError(
                    "Недостаточно прав для упрощённой выгрузки пользователей проекта "
                    f"{project_key}: Jira не дала прочитать роли проекта, назначаемых "
                    "пользователей и задачи проекта. Нужен токен пользователя с правом "
                    "Browse Projects/просмотра задач или правом чтения ролей проекта."
                ) from issue_http_error
            raise

        if not users_by_identity:
            logging.warning(
                "Упрощённая выгрузка не нашла пользователей проекта %s",
                project_key,
            )

    rows = [row.as_dict() for row in users_by_identity.values()]
    rows.sort(
        key=lambda row: (row.get("display_name") or row.get("name") or "").lower()
    )
    return rows


def export_project_users(
    project_key: str, client: JiraClient, export_dir: Path
) -> tuple[int, Path, Path]:
    rows = collect_project_users(project_key, client)
    csv_path, xlsx_path = export_to_files(
        rows,
        export_dir,
        f"project_{project_key.lower()}",
        columns=PROJECT_USERS_COLUMNS,
        entity_name="users",
    )
    return len(rows), csv_path, xlsx_path


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Jira project users")
    parser.add_argument(
        "--project",
        help="Jira project key. Defaults to JIRA_PROJECT_USERS_PROJECT_KEY or DEVAX12.",
    )
    return parser.parse_args()


def main() -> None:
    config = load_config()
    setup_logging(config)
    args = parse_args()
    project_key = args.project or config.project_users.project_key

    try:
        logging.info("Start Jira project users export")
        logging.info("Mode: project-users")
        logging.info("JIRA_PROJECT_USERS_PROJECT_KEY: %s", project_key)
        validate_config(config)

        client = JiraClient(config)
        client.check_connection()
        count, csv_path, xlsx_path = export_project_users(
            project_key,
            client,
            config.export_dir,
        )

        logging.info("Export completed successfully")
        logging.info("Rows: %s", count)
        print()
        print("Done.")
        print(f"Rows: {count}")
        print(f"CSV: {csv_path.resolve()}")
        print(f"Excel: {xlsx_path.resolve()}")
    except Exception as error:
        logging.exception("Script execution failed: %s", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
