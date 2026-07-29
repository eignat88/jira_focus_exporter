import logging
import time

from jira_client import JiraClient

RATE_LIMIT_DELAY = 0.3


def search_jira(client: JiraClient, jql: str, max_results: int = 50) -> list[dict]:
    """Search Jira using the existing JiraClient with rate-limiting."""
    try:
        issues = client.search_issues(
            jql,
            max_results=max_results,
            fields=[
                "summary",
                "description",
                "status",
                "assignee",
                "reporter",
                "created",
                "updated",
                "comment",
                "attachment",
                "issuelinks",
                "labels",
                "priority",
            ],
        )
        time.sleep(RATE_LIMIT_DELAY)
        return issues
    except Exception as exc:
        logging.warning("Ошибка поиска Jira (JQL: %s): %s", jql, exc)
        return []


def get_linked_issue(client: JiraClient, issue_key: str) -> dict | None:
    """Fetch a linked issue by key, with rate-limiting."""
    try:
        issue = client.get_issue(issue_key)
        time.sleep(RATE_LIMIT_DELAY)
        return issue
    except Exception as exc:
        logging.warning("Не удалось получить задачу %s: %s", issue_key, exc)
        return None
