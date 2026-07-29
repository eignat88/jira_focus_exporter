"""Скачивание вложений из Jira для задач связанных с Магниткой."""
import os
import requests
from config import load_config, validate_config
from jira_client import JiraClient


def main():
    config = load_config()
    validate_config(config)
    client = JiraClient(config)

    # Проверка подключения
    user = client.check_connection()
    print(f"Подключено как: {user.get('displayName')}\n")

    # Список задач
    issues = [
        "DAX-12515",
        "DAX-12491",
        "DAX-11328",
        "DAX-7096",
        "SDIT-25384",
    ]

    # Папка для сохранения
    save_dir = r"D:\Задачи\DAX-12516_Коректировка_форма_Контроля_инфолога_при_считывании_ШК\Магнитка"

    for issue_key in issues:
        print(f"\n{'='*60}")
        print(f"Обработка задачи: {issue_key}")
        print(f"{'='*60}")

        try:
            # Получаем задачу с вложениями
            issue = client.get_issue(issue_key)
            fields = issue.get("fields", {})
            summary = fields.get("summary", "Без описания")
            attachments = fields.get("attachment", [])

            print(f"Описание: {summary}")
            print(f"Количество вложений: {len(attachments)}")

            if not attachments:
                print("Вложений нет")
                continue

            # Создаем папку для задачи
            issue_dir = os.path.join(save_dir, f"{issue_key}")
            os.makedirs(issue_dir, exist_ok=True)

            # Скачиваем каждое вложение
            for att in attachments:
                filename = att.get("filename", "unknown")
                content_url = att.get("content", "")
                size = att.get("size", 0)

                print(f"  Скачивание: {filename} ({size} байт)")

                if content_url:
                    response = client._request("GET", content_url)
                    if response.status_code == 200:
                        filepath = os.path.join(issue_dir, filename)
                        with open(filepath, "wb") as f:
                            f.write(response.content)
                        print(f"    Сохранено: {filepath}")
                    else:
                        print(f"    Ошибка скачивания: {response.status_code}")

        except Exception as e:
            print(f"  Ошибка: {e}")

    print(f"\n{'='*60}")
    print("Готово!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
