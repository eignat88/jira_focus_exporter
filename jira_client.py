import logging

import requests

from config import AppConfig

SEARCH_FIELDS = [
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
    "comment",
]


class JiraClient:
    def __init__(self, config: AppConfig):
        self.config = config

    @property
    def jira_url(self) -> str:
        return self.config.jira_url

    def get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.jira_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def check_connection(self) -> dict:
        url = f"{self.jira_url}/rest/api/2/myself"
        response = requests.get(url, headers=self.get_headers(), timeout=30)
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

    def get_available_priority_names(self) -> set[str] | None:
        url = f"{self.jira_url}/rest/api/2/priority"
        response = requests.get(url, headers=self.get_headers(), timeout=30)
        if response.status_code != 200:
            logging.warning("Не удалось получить список приоритетов Jira")
            logging.warning("HTTP status: %s", response.status_code)
            logging.warning("Response: %s", response.text)
            return None
        return {
            priority.get("name") for priority in response.json() if priority.get("name")
        }

    def get_existing_focus_priorities(self) -> list[str]:
        configured_priorities = self.config.filters.focus_priorities
        if not configured_priorities:
            logging.info("Фильтр по приоритетам отключён: JIRA_FOCUS_PRIORITIES пустой")
            return []

        available_priorities = self.get_available_priority_names()
        if available_priorities is None:
            logging.warning(
                "Фильтр по приоритетам отключён, чтобы не получить ошибку JQL из-за неизвестных значений"
            )
            return []

        available_by_lower = {
            priority.lower(): priority for priority in available_priorities
        }
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
            logging.info(
                "Приоритеты для focus-фильтра: %s", ", ".join(existing_priorities)
            )
        else:
            logging.warning(
                "Ни один настроенный приоритет не найден в Jira; условие priority будет исключено из JQL"
            )
        return existing_priorities

    def search_issues(
        self,
        jql: str,
        max_results_per_page: int = 50,
        fields: list[str] | None = None,
        expand: list[str] | None = None,
    ) -> list[dict]:
        all_issues = []
        start_at = 0
        fields = fields or SEARCH_FIELDS
        while True:
            url = f"{self.jira_url}/rest/api/2/search"
            payload = {
                "jql": jql,
                "startAt": start_at,
                "maxResults": max_results_per_page,
                "fields": fields,
            }
            if expand:
                payload["expand"] = ",".join(expand)
            logging.info("Запрос задач Jira. startAt=%s", start_at)
            response = requests.post(
                url, headers=self.get_headers(), json=payload, timeout=60
            )
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

    def get_issue(self, issue_key: str, expand: list[str] | None = None) -> dict:
        url = f"{self.jira_url}/rest/api/2/issue/{issue_key}"
        params = {"fields": ",".join(SEARCH_FIELDS)}
        if expand:
            params["expand"] = ",".join(expand)
        response = requests.get(
            url, headers=self.get_headers(), params=params, timeout=30
        )
        if response.status_code != 200:
            logging.error("Не удалось получить задачу %s", issue_key)
            logging.error("HTTP status: %s", response.status_code)
            logging.error("Response: %s", response.text)
            response.raise_for_status()
        return response.json()

    def get_assignable_users(self, project_key: str) -> list[dict]:
        users = []
        start_at = 0
        max_results = 50
        while True:
            url = f"{self.jira_url}/rest/api/2/user/assignable/search"
            params = {
                "project": project_key,
                "startAt": start_at,
                "maxResults": max_results,
            }
            response = requests.get(
                url, headers=self.get_headers(), params=params, timeout=30
            )
            if response.status_code != 200:
                logging.error(
                    "Не удалось получить назначаемых пользователей проекта Jira: %s",
                    project_key,
                )
                logging.error("HTTP status: %s", response.status_code)
                logging.error("Response: %s", response.text)
                response.raise_for_status()
            page_users = response.json()
            if not isinstance(page_users, list):
                logging.warning(
                    "Неожиданный формат ответа assignable/search для проекта %s: %s",
                    project_key,
                    page_users,
                )
                break
            users.extend(page_users)
            if len(page_users) < max_results:
                break
            start_at += len(page_users)
        return users

    def get_project_roles(self, project_key: str) -> dict[str, str]:
        url = f"{self.jira_url}/rest/api/2/project/{project_key}/role"
        response = requests.get(url, headers=self.get_headers(), timeout=30)
        if response.status_code != 200:
            logging.error("Не удалось получить роли проекта Jira: %s", project_key)
            logging.error("HTTP status: %s", response.status_code)
            logging.error("Response: %s", response.text)
            response.raise_for_status()
        return response.json()

    def get_project_role(self, role_url: str) -> dict:
        response = requests.get(role_url, headers=self.get_headers(), timeout=30)
        if response.status_code != 200:
            logging.error(
                "Не удалось получить участников роли проекта Jira: %s", role_url
            )
            logging.error("HTTP status: %s", response.status_code)
            logging.error("Response: %s", response.text)
            response.raise_for_status()
        return response.json()

    def get_group_members(self, group_name: str) -> list[dict]:
        members = []
        start_at = 0
        while True:
            url = f"{self.jira_url}/rest/api/2/group/member"
            params = {"groupname": group_name, "startAt": start_at, "maxResults": 50}
            response = requests.get(
                url, headers=self.get_headers(), params=params, timeout=30
            )
            if response.status_code != 200:
                logging.error(
                    "Не удалось получить участников группы Jira: %s", group_name
                )
                logging.error("HTTP status: %s", response.status_code)
                logging.error("Response: %s", response.text)
                response.raise_for_status()
            data = response.json()
            values = data.get("values", [])
            members.extend(values)
            if data.get("isLast") or not values:
                break
            start_at += len(values)
        return members
