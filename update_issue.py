"""
Пример использования модуля jira_comment.py

Добавление комментария к задаче DAX-11253
"""
from pathlib import Path
from dotenv import load_dotenv
from jira_client import JiraClient
from jira_comment import add_comment
from config import load_config


def main():
    # Загрузка конфигурации
    env_path = Path(__file__).parent / ".env"
    load_dotenv(env_path)
    config = load_config()

    # Подключение к Jira
    client = JiraClient(config)
    user = client.check_connection()
    print(f"Подключение: {user.get('displayName')}")

    # Чтение комментария из файла
    comment_file = Path(__file__).parent / "comments" / "DAX-11253.txt"
    comment = comment_file.read_text(encoding="utf-8").strip()

    # Добавление комментария
    result = add_comment(client, "DAX-11253", comment)

    # Вывод результата
    if result["success"]:
        print(f"\nКомментарий добавлен к {result['issue_key']}")
        print(f"Ссылка: {result['url']}")
    else:
        print(f"\nОшибка: {result['message']}")


if __name__ == "__main__":
    main()
