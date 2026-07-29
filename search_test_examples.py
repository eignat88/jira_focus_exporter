"""Поиск тестовых примеров для DAX-12516 в Jira."""
import sys
from config import load_config, validate_config
from jira_client import JiraClient


def main():
    config = load_config()
    validate_config(config)
    client = JiraClient(config)

    # Проверка подключения
    user = client.check_connection()
    print(f"Подключено как: {user.get('displayName')}\n")

    # Поиск заявок с NoScanMC = ДА и маркированной/объемносортовой номенклатурой
    queries = [
        # Заявки с NoScanMC = ДА
        'summary ~ "приемка" AND summary ~ "магазин" AND summary ~ "КМ"',
        'summary ~ "NoScanMC" OR text ~ "NoScanMC"',
        # Заявки с инфологом "Требуется сканировать КМ"
        'text ~ "Требуется сканировать КМ" OR text ~ "Требуется сканирование КМ"',
        # Заявки связанные с формой контроля
        'summary ~ "форма контроля" AND project = DEVAX12',
        'summary ~ "инфолог" AND summary ~ "контрол"',
        # Заявки с ОСУ и маркировкой
        'summary ~ "ОСУ" AND summary ~ "маркировка"',
        'summary ~ "маркировка" AND summary ~ "контрол"',
        # Заявки связанные с DAX-12516
        'issue = DAX-12516',
        'issue in linkedIssues("DAX-12516")',
    ]

    all_issues = {}
    for jql in queries:
        print(f"--- JQL: {jql} ---")
        try:
            issues = client.search_issues(jql, max_results=10)
            for issue in issues:
                key = issue["key"]
                if key not in all_issues:
                    fields = issue.get("fields", {})
                    all_issues[key] = {
                        "key": key,
                        "summary": fields.get("summary", ""),
                        "status": (fields.get("status") or {}).get("name", ""),
                        "assignee": (fields.get("assignee") or {}).get("displayName", "не назначен"),
                        "priority": (fields.get("priority") or {}).get("name", ""),
                        "updated": fields.get("updated", ""),
                        "description": (fields.get("description") or "")[:200],
                        "labels": fields.get("labels", []),
                        "components": [c.get("name", "") for c in fields.get("components", [])],
                        "url": f"{config.jira_url}/browse/{key}",
                    }
            print(f"  Найдено: {len(issues)}")
        except Exception as e:
            print(f"  Ошибка: {e}")
        print()

    # Фильтрация наиболее релевантных
    relevant = []
    for key, info in all_issues.items():
        summary_lower = info["summary"].lower()
        desc_lower = info["description"].lower()

        # Ищем заявки связанные с NoScanMC, маркировкой, ОСУ, инфологом
        if any(kw in summary_lower or kw in desc_lower for kw in [
            "noscanmc", "номск", "номск", "маркировк", "осу", "инфолог",
            "форма контроля", "требуется сканиров", "приемка", "магазин"
        ]):
            relevant.append(info)

    # Вывод результатов
    print("=" * 100)
    print(f"ИТОГО: найдено уникальных задач: {len(all_issues)}")
    print(f"Релевантных для тестирования: {len(relevant)}")
    print("=" * 100)

    for info in sorted(relevant, key=lambda x: x["updated"], reverse=True):
        print(f"\n{info['key']}: {info['summary']}")
        print(f"  Статус: {info['status']}")
        print(f"  Назначен: {info['assignee']}")
        print(f"  Приоритет: {info['priority']}")
        print(f"  Обновлено: {info['updated']}")
        print(f"  Метки: {', '.join(info['labels']) if info['labels'] else '-'}")
        print(f"  Компоненты: {', '.join(info['components']) if info['components'] else '-'}")
        if info['description']:
            print(f"  Описание: {info['description'][:150]}...")
        print(f"  Ссылка: {info['url']}")


if __name__ == "__main__":
    main()
