import logging
import re
import sys
from pathlib import Path

from config import load_config, validate_config
from jira_client import JiraClient

# Регулярка для ключей задач: БУКВЫ_ЦИФРЫ-ЦИФРЫ (DAX-12345, MYL-3598, RFC-4378 и т.д.)
ISSUE_KEY_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def parse_issue_key(url_or_key: str) -> str:
    match = re.search(r"/browse/([A-Z][A-Z0-9]+-\d+)", url_or_key)
    if match:
        return match.group(1)
    match = re.match(r"^([A-Z][A-Z0-9]+-\d+)$", url_or_key.strip())
    if match:
        return match.group(1)
    raise ValueError(
        f"Не удалось извлечь ключ задачи из '{url_or_key}'. "
        "Ожидается URL вида https://jira.letoile.tech/browse/RFC-4378 или ключ RFC-4378"
    )


def extract_keys_from_description(description: str, exclude_key: str | None = None) -> list[str]:
    """Извлекает ключи задач из текста описания (таблицы Jira и пр.)."""
    if not description:
        return []
    keys = ISSUE_KEY_PATTERN.findall(description)
    # Убираем дубликаты, сохраняя порядок
    seen = set()
    unique = []
    for key in keys:
        if key not in seen and key != exclude_key:
            seen.add(key)
            unique.append(key)
    return unique


def get_issue_full(client: JiraClient, issue_key: str) -> dict:
    """Получает задачу с полями summary, description, attachment, issuelinks, subtasks."""
    url = f"{client.jira_url}/rest/api/2/issue/{issue_key}"
    params = {
        "fields": "summary,status,description,attachment,issuelinks,subtasks",
    }
    response = client._request("GET", url, params=params)
    if response.status_code != 200:
        logging.error("Не удалось получить задачу %s: HTTP %s", issue_key, response.status_code)
        logging.error("Response: %s", response.text)
        response.raise_for_status()
    return response.json()


def download_file(client: JiraClient, url: str, dest: Path) -> bool:
    """Скачивает файл. Возвращает True если файл был скачан, False если уже существует."""
    if dest.exists():
        logging.info("  Файл уже существует, пропуск: %s", dest.name)
        return False
    response = client._request("GET", url, timeout=120)
    if response.status_code != 200:
        logging.error("  Ошибка скачивания %s: HTTP %s", url, response.status_code)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(response.content)
    logging.info("  Сохранён: %s (%s байт)", dest.name, len(response.content))
    return True


def download_issue_attachments(client: JiraClient, issue_key: str, issue_dir: Path, stats: dict):
    """Скачивает все вложения одной задачи."""
    try:
        issue = get_issue_full(client, issue_key)
    except Exception as exc:
        logging.error("Ошибка получения задачи %s: %s", issue_key, exc)
        stats["errors"] += 1
        return

    fields = issue.get("fields", {})
    summary = fields.get("summary") or issue_key
    attachments = fields.get("attachment") or []

    stats["issues"] += 1
    safe_name = re.sub(r'_+', '_', re.sub(r'[<>:"/\\|?*]', "_", summary))[:80].rstrip('_ ')
    target_dir = issue_dir / f"{issue_key}_{safe_name}"

    if attachments:
        target_dir.mkdir(parents=True, exist_ok=True)
        logging.info("[%s] %s — %d вложений", issue_key, summary, len(attachments))
        for att in attachments:
            filename = att.get("filename") or "unnamed"
            content_url = att.get("content")
            if not content_url:
                logging.warning("  Вложение без URL: %s", filename)
                stats["errors"] += 1
                continue
            dest = target_dir / filename
            stats["attachments"] += 1
            try:
                was_downloaded = download_file(client, content_url, dest)
                if was_downloaded:
                    stats["downloaded"] += 1
                else:
                    stats["skipped"] += 1
            except Exception as exc:
                logging.error("  Ошибка скачивания %s: %s", filename, exc)
                stats["errors"] += 1
    else:
        logging.info("[%s] %s — без вложений", issue_key, summary)


def collect_linked_rfc_keys(client: JiraClient, issue_key: str, visited: set) -> list[str]:
    """Собирает ключи RFC задач, связанных через Cloners (рекурсивно)."""
    if issue_key in visited:
        return []
    visited.add(issue_key)

    try:
        issue = get_issue_full(client, issue_key)
    except Exception:
        return []

    fields = issue.get("fields", {})
    linked_keys = []

    for link in fields.get("issuelinks") or []:
        link_type = link.get("type", {}).get("name", "")
        if link_type != "Cloners":
            continue
        inward = link.get("inwardIssue") or {}
        outward = link.get("outwardIssue") or {}
        target = inward if inward.get("key") else outward
        target_key = target.get("key")
        if target_key and target_key not in visited:
            linked_keys.append(target_key)
            # Рекурсивно обходим цепочку клонов
            linked_keys.extend(collect_linked_rfc_keys(client, target_key, visited))

    return linked_keys


def run(client: JiraClient, start_key: str, output_dir: Path, recursive: bool, max_depth: int):
    """Основной процесс: обходит RFC задачи, извлекает ключи из описаний, скачивает вложения."""
    stats = {"issues": 0, "attachments": 0, "downloaded": 0, "skipped": 0, "errors": 0}
    visited = set()

    # 1. Собираем все связанные RFC задачи
    logging.info("=== Сбор связанных задач ===")
    rfc_keys = [start_key]
    if recursive:
        linked = collect_linked_rfc_keys(client, start_key, set())
        rfc_keys.extend(linked)

    logging.info("Найдено RFC задач: %d — %s", len(rfc_keys), ", ".join(rfc_keys))

    # 2. Для каждого RFC — скачиваем вложения и извлекаем ключи задач из описания
    all_task_keys = []
    for rfc_key in rfc_keys:
        logging.info("")
        logging.info("--- RFC: %s ---", rfc_key)

        # Скачиваем вложения самого RFC
        download_issue_attachments(client, rfc_key, output_dir, stats)

        # Извлекаем ключи задач из описания
        if recursive:
            try:
                issue = get_issue_full(client, rfc_key)
                description = issue.get("fields", {}).get("description") or ""
                keys = extract_keys_from_description(description, exclude_key=rfc_key)
                if keys:
                    logging.info("[%s] Из описания извлечены задачи: %s", rfc_key, ", ".join(keys))
                    all_task_keys.extend(keys)
                else:
                    logging.info("[%s] Задачи в описании не найдены", rfc_key)
            except Exception as exc:
                logging.error("Ошибка чтения описания %s: %s", rfc_key, exc)

    # 3. Убираем дубликаты, скачиваем вложения найденных задач
    unique_task_keys = list(dict.fromkeys(all_task_keys))
    if unique_task_keys:
        logging.info("")
        logging.info("=== Вложенные задачи: %d шт. ===", len(unique_task_keys))
        for task_key in unique_task_keys:
            if task_key in visited:
                continue
            visited.add(task_key)
            logging.info("")
            download_issue_attachments(client, task_key, output_dir, stats)

    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Скачивание вложений из задач Jira")
    parser.add_argument(
        "issue",
        help="URL или ключ задачи (например, https://jira.letoile.tech/browse/RFC-4378 или RFC-4378)",
    )
    parser.add_argument(
        "-o", "--output",
        default="attachments",
        help="Папка для сохранения вложений (по умолчанию: attachments)",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Не обходить связанные RFC и не извлекать задачи из описания",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=3,
        help="Максимальная глубина обхода (по умолчанию: 3)",
    )
    args = parser.parse_args()

    config = load_config()
    validate_config(config)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    client = JiraClient(config)
    client.check_connection()

    issue_key = parse_issue_key(args.issue)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    logging.info("Стартовая задача: %s", issue_key)
    logging.info("Папка: %s", output_dir.resolve())
    logging.info("Рекурсивно: %s", not args.no_recursive)

    stats = run(client, issue_key, output_dir, not args.no_recursive, args.max_depth)

    print()
    print("Готово.")
    print(f"Обработано задач: {stats['issues']}")
    print(f"Вложений найдено: {stats['attachments']}")
    print(f"Скачано: {stats['downloaded']}")
    print(f"Пропущено (уже есть): {stats['skipped']}")
    print(f"Ошибок: {stats['errors']}")
    print(f"Папка: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
