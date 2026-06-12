import logging
import time

import requests

from config import AppConfig

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 5
BASE_DELAY = 1.0
MAX_DELAY = 30.0

SEARCH_FIELDS = [
    "summary",
    "status",
    "priority",
    "assignee",
    "reporter",
    "creator",
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

    def _request(
        self,
        method: str,
        url: str,
        timeout: int = 30,
        **kwargs,
    ) -> requests.Response:
        last_exc = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = requests.request(
                    method, url, headers=self.get_headers(), timeout=timeout, **kwargs
                )
            except requests.ConnectionError as exc:
                last_exc = exc
                delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                logging.warning(
                    "Сетевая ошибка (попытка %s/%s), повтор через %.1fс: %s",
                    attempt + 1, MAX_RETRIES + 1, delay, exc,
                )
                time.sleep(delay)
                continue

            if response.status_code not in RETRY_STATUSES:
                return response

            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = min(float(retry_after), MAX_DELAY)
                except ValueError:
                    delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
            else:
                delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)

            if attempt < MAX_RETRIES:
                logging.warning(
                    "HTTP %s (попытка %s/%s), повтор через %.1fс",
                    response.status_code, attempt + 1, MAX_RETRIES + 1, delay,
                )
                time.sleep(delay)
            else:
                logging.error(
                    "HTTP %s после %s попыток: %s",
                    response.status_code, MAX_RETRIES + 1, url,
                )
        if last_exc:
            raise last_exc
        return response

    def check_connection(self) -> dict:
        url = f"{self.jira_url}/rest/api/2/myself"
        response = self._request("GET", url)
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
        response = self._request("GET", url)
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
        max_results: int | None = None,
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
                payload["expand"] = expand
            logging.info("Запрос задач Jira. startAt=%s", start_at)
            response = self._request("POST", url, timeout=60, json=payload)
            if response.status_code != 200:
                logging.error("Ошибка поиска задач")
                logging.error("HTTP status: %s", response.status_code)
                logging.error("Response: %s", response.text)
                response.raise_for_status()
            data = response.json()
            issues = data.get("issues", [])
            total = data.get("total", 0)
            if max_results is not None:
                remaining = max_results - len(all_issues)
                issues = issues[:remaining]
            all_issues.extend(issues)
            logging.info("Получено задач: %s из %s", len(all_issues), total)
            if max_results is not None and len(all_issues) >= max_results:
                break
            start_at += max_results_per_page
            if start_at >= total:
                break
        return all_issues

    def get_issue(self, issue_key: str, expand: list[str] | None = None) -> dict:
        url = f"{self.jira_url}/rest/api/2/issue/{issue_key}"
        params = {"fields": ",".join(SEARCH_FIELDS)}
        if expand:
            params["expand"] = ",".join(expand)
        response = self._request("GET", url, params=params)
        if response.status_code != 200:
            logging.error("Не удалось получить задачу %s", issue_key)
            logging.error("HTTP status: %s", response.status_code)
            logging.error("Response: %s", response.text)
            response.raise_for_status()
        return response.json()

    def get_assignable_users(
        self, project_key: str, max_pages: int = 20
    ) -> list[dict]:
        users = []
        start_at = 0
        max_results = 50
        seen_pages: set[tuple[str, ...]] = set()
        while True:
            url = f"{self.jira_url}/rest/api/2/user/assignable/search"
            params = {
                "project": project_key,
                "startAt": start_at,
                "maxResults": max_results,
            }
            logging.info(
                "Запрос назначаемых пользователей Jira. project=%s startAt=%s maxResults=%s",
                project_key,
                start_at,
                max_results,
            )
            response = self._request("GET", url, params=params)
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
            page_signature = tuple(
                str(
                    user.get("accountId")
                    or user.get("name")
                    or user.get("key")
                    or user.get("displayName")
                    or index
                )
                for index, user in enumerate(page_users)
            )
            if page_signature in seen_pages:
                logging.warning(
                    "Jira вернула повторяющуюся страницу назначаемых пользователей для проекта %s; остановка пагинации",
                    project_key,
                )
                break
            seen_pages.add(page_signature)
            users.extend(page_users)
            logging.info(
                "Получено назначаемых пользователей Jira: page=%s total=%s",
                len(page_users),
                len(users),
            )
            if len(page_users) < max_results:
                break
            start_at += len(page_users)
            if len(seen_pages) >= max_pages:
                logging.warning(
                    "Достигнут лимит страниц assignable/search для проекта %s: %s страниц, %s пользователей. Продолжаем выгрузку дальше.",
                    project_key,
                    max_pages,
                    len(users),
                )
                break
        return users

    def get_project_roles(self, project_key: str) -> dict[str, str]:
        url = f"{self.jira_url}/rest/api/2/project/{project_key}/role"
        response = self._request("GET", url)
        if response.status_code != 200:
            logging.error("Не удалось получить роли проекта Jira: %s", project_key)
            logging.error("HTTP status: %s", response.status_code)
            logging.error("Response: %s", response.text)
            response.raise_for_status()
        return response.json()

    def get_project_role(self, role_url: str) -> dict:
        response = self._request("GET", role_url)
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
            response = self._request("GET", url, params=params)
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
