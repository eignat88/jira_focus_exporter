"""
Модуль для добавления комментариев к задачам Jira.

Использование:
    python jira_comment.py --issue DAX-11253 --comment "Текст комментария"
    python jira_comment.py --issue DAX-11253 --file comment.txt
    python jira_comment.py --issue DAX-11253 --comment "Текст" --dry-run
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from jira_client import JiraClient
from config import load_config


def load_comment_from_file(file_path: str) -> str:
    """Чтение комментария из файла."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {file_path}")
    return path.read_text(encoding="utf-8").strip()


def add_comment(
    client: JiraClient,
    issue_key: str,
    comment: str,
    dry_run: bool = False,
) -> dict:
    """
    Добавление комментария к задаче Jira.

    Args:
        client: Клиент Jira
        issue_key: Ключ задачи (например, DAX-11253)
        comment: Текст комментария
        dry_run: Если True, только показать что будет отправлено

    Returns:
        dict с результатом операции
    """
    # Получение информации о задаче
    issue = client.get_issue(issue_key)
    fields = issue.get("fields", {})
    status = fields.get("status", {}).get("name", "неизвестно")
    assignee = fields.get("assignee", {})
    assignee_name = assignee.get("displayName", "не назначен") if assignee else "не назначен"

    result = {
        "issue_key": issue_key,
        "status": status,
        "assignee": assignee_name,
        "comment_length": len(comment),
        "dry_run": dry_run,
        "success": False,
    }

    if dry_run:
        result["message"] = "DRY RUN: комментарий не отправлен"
        return result

    # Отправка комментария
    url = f"{client.jira_url}/rest/api/2/issue/{issue_key}/comment"
    payload = {"body": comment}

    response = client._request("POST", url, json=payload)

    if response.status_code == 201:
        result["success"] = True
        result["message"] = "Комментарий успешно добавлен"
        result["url"] = f"{client.jira_url}/browse/{issue_key}"
    else:
        result["message"] = f"Ошибка: HTTP {response.status_code}"
        result["error"] = response.text

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Добавление комментария к задаче Jira"
    )
    parser.add_argument(
        "--issue",
        required=True,
        help="Ключ задачи (например, DAX-11253)",
    )
    parser.add_argument(
        "--comment",
        help="Текст комментария",
    )
    parser.add_argument(
        "--file",
        help="Путь к файлу с текстом комментария",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать что будет отправлено, без фактической отправки",
    )

    args = parser.parse_args()

    # Определение комментария
    if args.comment:
        comment = args.comment
    elif args.file:
        comment = load_comment_from_file(args.file)
    else:
        print("Ошибка: укажите --comment или --file")
        sys.exit(1)

    if not comment:
        print("Ошибка: комментарий пустой")
        sys.exit(1)

    # Загрузка конфигурации
    env_path = Path(__file__).parent / ".env"
    load_dotenv(env_path)
    config = load_config()

    # Подключение к Jira
    client = JiraClient(config)
    user = client.check_connection()
    print(f"Подключение: {user.get('displayName')}")

    # Добавление комментария
    result = add_comment(client, args.issue, comment, dry_run=args.dry_run)

    # Вывод результата
    print(f"\nЗадача: {result['issue_key']}")
    print(f"Статус: {result['status']}")
    print(f"Исполнитель: {result['assignee']}")
    print(f"Длина комментария: {result['comment_length']} символов")

    if result["dry_run"]:
        print(f"\n[DRY RUN] Комментарий:")
        print("-" * 40)
        print(comment)
        print("-" * 40)
        print(f"\n{result['message']}")
    elif result["success"]:
        print(f"\n{result['message']}")
        print(f"Ссылка: {result['url']}")
    else:
        print(f"\n{result['message']}")
        if "error" in result:
            print(f"Ответ: {result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
