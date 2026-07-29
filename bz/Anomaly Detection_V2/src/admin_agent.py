#!/usr/bin/env python3
"""
Агент-администратор проекта (Task 7)
Сканирует папку проекта, анализирует структуру и формирует управленческие файлы:
- project_status.md
- task_register.md
- gantt.md
- admin_log.md
"""

import os
import re
import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def scan_project(root: Path) -> dict:
    """Сканирует папку проекта и возвращает структурированное описание."""
    result = {
        "root": root,
        "dirs": {},
        "task_files": [],
        "md_files": [],
        "py_files": [],
        "data_files": [],
        "config_files": [],
        "doc_files": [],
        "other_files": [],
        "empty_dirs": [],
        "has_readme": False,
        "has_requirements": False,
        "has_gitignore": False,
        "total_files": 0,
        "total_dirs": 0,
    }

    for item in sorted(root.rglob("*")):
        rel = item.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue

        if item.is_dir():
            result["total_dirs"] += 1
            contents = [f for f in item.iterdir() if not f.name.startswith(".")]
            if not contents:
                result["empty_dirs"].append(str(rel))
            continue

        result["total_files"] += 1
        ext = item.suffix.lower()
        name = item.name

        if name == "README.md":
            result["has_readme"] = True
        elif name == "requirements.txt":
            result["has_requirements"] = True
        elif name == ".gitignore":
            result["has_gitignore"] = True

        if re.match(r"^task\d+\.md$", name):
            result["task_files"].append(item)
        elif ext == ".md":
            result["md_files"].append(item)
        elif ext == ".py":
            result["py_files"].append(item)
        elif ext in (".csv", ".tsv", ".json", ".parquet"):
            result["data_files"].append(item)
        elif ext in (".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf"):
            result["config_files"].append(item)
        elif ext in (".docx", ".xlsx", ".pdf", ".sql", ".xpo", ".xpp", ".drawio"):
            result["doc_files"].append(item)
        else:
            result["other_files"].append(item)

    return result


def read_task_file(path: Path) -> dict:
    """Читает task-файл и извлекает заголовок и краткое описание."""
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.strip().splitlines()
    title = ""
    summary_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# ") and not title:
            title = stripped[2:]
        elif stripped and not title:
            continue
        elif stripped.startswith("##"):
            break
        elif stripped and title:
            summary_lines.append(stripped)

    return {
        "path": path,
        "name": path.stem,
        "title": title or path.stem,
        "summary": " ".join(summary_lines[:3])[:200],
        "content": content,
    }


def detect_task_status(task_info: dict, scan: dict) -> str:
    """Определяет статус задачи на основе наличия кода/результатов."""
    name = task_info["name"].lower()
    content = task_info["content"].lower()

    has_code = len(scan["py_files"]) > 4  # больше чем просто __init__.py
    has_data = len(scan["data_files"]) > 0
    has_tests = any("test" in str(f).lower() for f in scan["py_files"])

    if "task7" in name:
        return "В работе"
    elif has_code and has_tests:
        return "Готово"
    elif has_code:
        return "В работе"
    elif "нужно выяснить" in content or "нужны исходные" in content:
        return "Заблокировано"
    else:
        return "Не начато"


def build_task_register(scan: dict) -> str:
    """Формирует реестр задач."""
    tasks = []
    for tf in sorted(scan["task_files"]):
        info = read_task_file(tf)
        status = detect_task_status(info, scan)
        priority = "Высокий" if "task5" in info["name"] else ("Средний" if "task6" in info["name"] else "Низкий")
        deps = ""
        if "task6" in info["name"]:
            deps = "—"
        elif "task7" in info["name"]:
            deps = "—"
        elif "task5" in info["name"]:
            deps = "T6 (данные)"

        tasks.append({
            "id": info["name"].upper().replace("TASK", "T"),
            "name": info["title"],
            "status": status,
            "agent": "—",
            "priority": priority,
            "deps": deps,
            "comment": info["summary"][:100] if info["summary"] else "—",
        })

    header = """# Реестр задач проекта

| ID | Задача | Сатус | Исполнитель/агент | Приоритет | Зависимости | Комментарий |
|---|---|---|---|---|---|---|
"""
    rows = []
    for t in tasks:
        row = f"| {t['id']} | {t['name']} | {t['status']} | {t['agent']} | {t['priority']} | {t['deps']} | {t['comment']} |"
        rows.append(row)

    return header + "\n".join(rows) + "\n"


def build_project_status(scan: dict) -> str:
    """Формирует файл текущего состояния проекта."""
    task_count = len(scan["task_files"])
    py_count = len(scan["py_files"])
    real_py = sum(1 for f in scan["py_files"] if f.stat().st_size > 0)
    empty_py = py_count - real_py

    completed_tasks = 0
    in_progress = 0
    blocked = 0
    not_started = 0
    for tf in scan["task_files"]:
        info = read_task_file(tf)
        s = detect_task_status(info, scan)
        if s == "Готово":
            completed_tasks += 1
        elif s == "В работе":
            in_progress += 1
        elif s == "Заблокировано":
            blocked += 1
        else:
            not_started += 1

    has_data = len(scan["data_files"]) > 0
    has_models = any("model" in str(f).lower() for f in scan["py_files"])
    has_notebooks = len(list(PROJECT_ROOT.glob("notebooks/*.ipynb"))) > 0

    summary_lines = []
    if not_started == task_count:
        summary_lines.append("Проект в фазе инициализации — структура создана, код не написан")
    elif completed_tasks == task_count:
        summary_lines.append("Все задачи выполнены")
    else:
        summary_lines.append(f"Выполнено {completed_tasks}/{task_count} задач")

    if not has_data:
        summary_lines.append("Данные отсутствуют (папки data/raw, data/processed, data/external пусты)")
    if not has_models:
        summary_lines.append("Модели не обучены")

    risks = []
    if not has_data:
        risks.append("Нет данных для обучения моделей — задача T6 (исходные данные) заблокирована")
    if empty_py == py_count and py_count > 0:
        risks.append("Все Python-файлы пусты — код не реализован")
    if not scan["has_requirements"]:
        risks.append("Отсутствует requirements.txt")
    if blocked > 0:
        risks.append(f"{blocked} задач заблокировано")

    sections = []
    sections.append("# Текущее состояние проекта\n")
    sections.append("## Краткое резюме")
    for s in summary_lines:
        sections.append(f"- {s}")
    sections.append(f"- Файлов в проекте: {scan['total_files']}")
    sections.append(f"- Python-файлов: {py_count} (из них пустых: {empty_py})")
    sections.append(f"- Задач: {task_count} (выполнено: {completed_tasks}, в работе: {in_progress}, заблокировано: {blocked}, не начато: {not_started})")
    if risks:
        sections.append("- **Основные риски:**")
        for r in risks:
            sections.append(f"  - {r}")

    sections.append("\n## Что уже сделано")
    if scan["has_readme"]:
        sections.append("- README.md с описанием проекта")
    if scan["has_requirements"]:
        sections.append("- requirements.txt с зависимостями")
    if scan["has_gitignore"]:
        sections.append("- .gitignore")
    sections.append(f"- Структура папок: src/ (features, models, visualization), data/, configs/, notebooks/, tests/, logs/, reports/")
    sections.append("- План работ (plan.md)")
    sections.append("- Спецификация обнаружения аномалий (task5.md)")
    sections.append("- Задача на выяснение исходных данных (task6.md)")
    sections.append("- Справочная документация в папке МАТРИЦА_ПРИЗНАКОВ_И_ПОВЕДЕНИЕ_СИСТЕМЫ (12 XPO-файлов, SQL-метрики, рекомендации, ML-проекты)")

    sections.append("\n## Что выполняется сейчас")
    sections.append("- Задача T7: агент-администратор (текущий запуск)")

    sections.append("\n## Что осталось сделать")
    sections.append("- T6: Получить/сформировать исходные данные (логи сканирований, сотрудники, конкурентные КМ, SSCC)")
    sections.append("- T5: Реализовать pipeline обнаружения аномалий (признаки → модели → алерты)")
    sections.append("- Развернуть ML-модели (Isolation Forest → LOF → Autoencoder → LSTM)")
    sections.append("- Создать Jupyter-ноутбуки для EDA и визуализации")
    sections.append("- Настроить мониторинг и алерты")

    sections.append("\n## Блокеры")
    if blocked > 0:
        sections.append("- T6 (Исходные данные): нет реальных данных WMS для обучения моделей. Требуется подключение к БД или получение выгрузки")
    else:
        sections.append("(нет)")

    sections.append("\n## Что требует решения человека")
    sections.append("1. Где взять реальные данные? (SQL Server, выгрузка, API)")
    sections.append("2. Какой формат данных ожидается? (CSV, Parquet, прямое подключение)")
    sections.append("3. Есть ли размеченные данные об аномалиях?")
    sections.append("4. Нужен ли реалтайм-мониторинг или батч-анализ?")

    sections.append("\n## Недостающая информация")
    sections.append("")
    sections.append("| Что неизвестно | Где требуется уточнение | Почему важно |")
    sections.append("|---|---|---|")
    sections.append("| Формат и структура логов сканирований | Task6, база данных WMS | Определение входных признаков моделей |")
    sections.append("| Объём данных (сколько записей) | База данных WMS | Выбор модели и оптимизация |")
    sections.append("| Наличие разметки аномалий | Руководство проекта | Supervised vs unsupervised подход |")
    sections.append("| Расписание работы сотрудников | HR / бэк-офис | Определение «рабочего времени» для аномалии типа 4 |")

    sections.append("\n## Рекомендации по следующим шагам")
    sections.append("1. **Приоритет 1:** Разблокировать T6 — получить доступ к данным WMS (SQL Server или выгрузка)")
    sections.append("2. **Приоритет 2:** EDA на полученных данных — Jupyter-ноутбук с анализом распределений")
    sections.append("3. **Приоритет 3:** Реализовать Feature Engineering (6 признаков из task5.md)")
    sections.append("4. **Приоритет 4:** Baseline-модель (Isolation Forest)")

    return "\n".join(sections) + "\n"


def build_gantt(scan: dict) -> str:
    """Формирует диаграмму Ганта в Mermaid."""
    today = datetime.date.today()
    today_str = today.isoformat()

    tasks = []
    for tf in sorted(scan["task_files"]):
        info = read_task_file(tf)
        status = detect_task_status(info, scan)
        tasks.append((info["name"], status))

    gantt_sections = []

    gantt_sections.append("```mermaid")
    gantt_sections.append("gantt")
    gantt_sections.append("    title План работ по проекту Anomaly Detection")
    gantt_sections.append("    dateFormat  YYYY-MM-DD")
    gantt_sections.append("")

    gantt_sections.append("    section Инициализация")
    for name, status in tasks:
        mermaid_name = name[:40]
        if status == "Готово":
            tag = ":done,"
        elif status == "В работе":
            tag = ":active,"
        elif status == "Заблокировано":
            tag = ":crit,"
        else:
            tag = ":"
        gantt_sections.append(f"    {mermaid_name} {tag}  t_{name.replace(' ', '_')}, {today_str}, 1d")

    gantt_sections.append("")
    gantt_sections.append("    section Данные")
    gantt_sections.append(f"    Получение данных WMS           :crit,  t_data, {today_str}, 7d")
    gantt_sections.append(f"    EDA и анализ                    :       t_eda, after t_data, 3d")
    gantt_sections.append("")
    gantt_sections.append("    section Разработка")
    gantt_sections.append(f"    Feature Engineering             :       t_feat, after t_eda, 3d")
    gantt_sections.append(f"    Baseline модель                 :       t_base, after t_feat, 3d")
    gantt_sections.append(f"    Расширенные модели              :       t_adv, after t_base, 5d")
    gantt_sections.append("")
    gantt_sections.append("    section Тестирование")
    gantt_sections.append(f"    Валидация и метрики             :       t_val, after t_adv, 2d")
    gantt_sections.append(f"    MVP                             :       t_mvp, after t_val, 1d")

    return "\n".join(gantt_sections) + "\n```\n"


def build_admin_log(scan: dict) -> str:
    """Формирует журнал запусков агента."""
    now = datetime.datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M")

    sections = []
    sections.append("# Журнал агента-администратора\n")
    sections.append(f"## Запуск от {ts}\n")

    sections.append("### Что было проверено")
    sections.append("- Структура папок проекта")
    sections.append("- Наличие task-файлов (найдено: " + str(len(scan["task_files"])) + ")")
    sections.append("- Python-файлы (найдено: " + str(len(scan["py_files"])) + ", из них непустых: " + str(sum(1 for f in scan["py_files"] if f.stat().st_size > 0)) + ")")
    sections.append("- Данные (найдено: " + str(len(scan["data_files"])) + ")")
    sections.append("- Конфигурации (найдено: " + str(len(scan["config_files"])) + ")")
    sections.append("- Документы (найдено: " + str(len(scan["doc_files"])) + ")")
    sections.append("- README: " + ("есть" if scan["has_readme"] else "нет"))
    sections.append("- requirements.txt: " + ("есть" if scan["has_requirements"] else "нет"))
    sections.append("- .gitignore: " + ("есть" if scan["has_gitignore"] else "нет"))
    sections.append("- Пустые папки: " + str(len(scan["empty_dirs"])))

    sections.append("\n### Что изменилось с прошлого запуска")
    sections.append("Первый запуск — нет предыдущего состояния для сравнения")

    sections.append("\n### Какие файлы обновлены")
    sections.append("- project_status.md (создан)")
    sections.append("- task_register.md (создан)")
    sections.append("- gantt.md (создан)")
    sections.append("- admin_log.md (обновлён)")

    sections.append("\n### Новые риски")
    if not scan["data_files"]:
        sections.append("- Нет данных для обучения моделей")
    if sum(1 for f in scan["py_files"] if f.stat().st_size == 0) == len(scan["py_files"]) and scan["py_files"]:
        sections.append("- Все Python-файлы пусты — код не реализован")
    sections.append("- Задача T6 (исходные данные) блокирует T5 (реализацию)")

    sections.append("\n### Следующие действия")
    sections.append("1. Разблокировать получение данных WMS")
    sections.append("2. Начать EDA в Jupyter-ноутбуке")
    sections.append("3. Реализовать Feature Engineering")

    return "\n".join(sections) + "\n"


def main():
    print(f"Агент-администратор: сканирование {PROJECT_ROOT}")
    scan = scan_project(PROJECT_ROOT)

    print(f"  Файлов: {scan['total_files']}, Папок: {scan['total_dirs']}")
    print(f"  Task-файлов: {len(scan['task_files'])}")
    print(f"  Python: {len(scan['py_files'])}, Данные: {len(scan['data_files'])}")

    files = {
        "project_status.md": build_project_status(scan),
        "task_register.md": build_task_register(scan),
        "gantt.md": build_gantt(scan),
        "admin_log.md": build_admin_log(scan),
    }

    for name, content in files.items():
        path = PROJECT_ROOT / name
        path.write_text(content, encoding="utf-8")
        print(f"  Обновлён: {name}")

    print("\n# Итог запуска агента-администратора\n")
    print("## Обновлены файлы")
    for name in files:
        print(f"- {name}")
    print(f"\n## Текущий статус проекта")
    print("Проект в фазе инициализации. Структура и документация готовы, код не написан.")
    print("\n## Ближайшие действия")
    print("1. Разблокировать получение данных WMS")
    print("2. EDA на полученных данных")
    print("3. Feature Engineering (6 признаков)")
    print("\n## Что требует решения человека")
    print("1. Где взять реальные данные WMS?")
    print("2. Формат данных (CSV, Parquet, SQL)?")
    print("3. Есть ли разметка аномалий?")


if __name__ == "__main__":
    main()
