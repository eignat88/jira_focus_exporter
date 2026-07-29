"""
Project Scanner — сканирует проект и генерирует отчёт о структуре, метриках и статусе.

Использование:
    python project_scanner.py [--output report.json] [--markdown REPORT.md]
"""

import os
import json
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import subprocess


class ProjectScanner:
    """Сканер проекта Anomaly Detection."""

    def __init__(self, root: str = "."):
        self.root = Path(root).resolve()
        self.stats = defaultdict(int)
        self.files_by_type = defaultdict(list)
        self.dirs = []
        self.errors = []

    def scan(self):
        """Запуск полного сканирования проекта."""
        print(f"[SCAN] Сканирование: {self.root}")
        self._scan_directory(self.root)
        self._count_lines_of_code()
        self._check_tests()
        self._check_git_status()
        self._check_dependencies()
        return self.get_report()

    def _scan_directory(self, path: Path, depth: int = 0):
        """Рекурсивное сканирование директории."""
        try:
            for item in sorted(path.iterdir()):
                # Пропускаем скрытые и архивные директории
                if item.name.startswith('.') or item.name == '__pycache__':
                    continue
                if item.name == 'archive' and depth == 1:
                    continue

                if item.is_dir():
                    self.dirs.append({
                        "path": str(item.relative_to(self.root)),
                        "depth": depth
                    })
                    self._scan_directory(item, depth + 1)
                elif item.is_file():
                    self._process_file(item)
        except PermissionError:
            self.errors.append(f"Permission denied: {path}")

    def _process_file(self, filepath: Path):
        """Обработка отдельного файла."""
        rel_path = str(filepath.relative_to(self.root))
        ext = filepath.suffix.lower()
        size = filepath.stat().st_size

        self.stats["total_files"] += 1
        self.stats["total_size"] += size
        self.stats[f"ext_{ext}"] += 1

        self.files_by_type[ext].append({
            "path": rel_path,
            "size": size,
            "modified": datetime.fromtimestamp(filepath.stat().st_mtime).isoformat()
        })

    def _count_lines_of_code(self):
        """Подсчёт строк кода по типам файлов."""
        code_extensions = {'.py', '.sql', '.ps1', '.bat', '.cmd', '.sh'}
        for ext in code_extensions:
            count = 0
            for file_info in self.files_by_type.get(ext, []):
                try:
                    filepath = self.root / file_info["path"]
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        count += sum(1 for _ in f)
                except Exception:
                    pass
            if count > 0:
                self.stats[f"lines_{ext}"] = count

    def _check_tests(self):
        """Проверка тестов."""
        test_dir = self.root / "tests"
        if test_dir.exists():
            test_files = list(test_dir.glob("test_*.py"))
            self.stats["test_files"] = len(test_files)
            self.stats["test_modules"] = [f.name for f in test_files]

    def _check_git_status(self):
        """Проверка git-статуса."""
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                self.stats["git_status"] = "clean" if not lines[0] else "dirty"
                self.stats["git_changes"] = len([l for l in lines if l.strip()])
        except Exception:
            self.stats["git_status"] = "not_a_repo"

    def _check_dependencies(self):
        """Проверка зависимостей."""
        req_file = self.root / "requirements.txt"
        if req_file.exists():
            with open(req_file, 'r') as f:
                deps = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            self.stats["dependencies"] = deps
            self.stats["dependency_count"] = len(deps)

    def get_report(self) -> dict:
        """Генерация отчёта."""
        return {
            "project": "Anomaly Detection",
            "scan_date": datetime.now().isoformat(),
            "root": str(self.root),
            "summary": {
                "total_files": self.stats["total_files"],
                "total_size_kb": round(self.stats["total_size"] / 1024, 2),
                "directories": len(self.dirs),
                "errors": len(self.errors)
            },
            "code_stats": {
                "python_lines": self.stats.get("lines_.py", 0),
                "sql_lines": self.stats.get("lines_.sql", 0),
                "powershell_lines": self.stats.get("lines_.ps1", 0),
                "total_code_lines": sum(
                    self.stats.get(f"lines_{ext}", 0)
                    for ext in ['.py', '.sql', '.ps1', '.bat', '.cmd', '.sh']
                )
            },
            "file_types": {
                ext: len(files) for ext, files in self.files_by_type.items()
            },
            "tests": {
                "count": self.stats.get("test_files", 0),
                "modules": self.stats.get("test_modules", [])
            },
            "git": {
                "status": self.stats.get("git_status", "unknown"),
                "changes": self.stats.get("git_changes", 0)
            },
            "dependencies": {
                "count": self.stats.get("dependency_count", 0),
                "list": self.stats.get("dependencies", [])
            },
            "errors": self.errors
        }

    def generate_markdown_report(self) -> str:
        """Генерация Markdown-отчёта."""
        report = self.get_report()
        s = report["summary"]
        c = report["code_stats"]

        md = f"""# Project Scanner Report

**Дата сканирования:** {report['scan_date'][:19]}
**Корневая директория:** `{report['root']}`

---

## Сводка

| Метрика | Значение |
|---------|----------|
| Всего файлов | {s['total_files']} |
| Размер | {s['total_size_kb']} KB |
| Директорий | {s['directories']} |
| Ошибок | {s['errors']} |

---

## Код

| Тип | Строк |
|-----|-------|
| Python (.py) | {c['python_lines']:,} |
| SQL (.sql) | {c['sql_lines']:,} |
| PowerShell (.ps1) | {c['powershell_lines']:,} |
| **Итого** | **{c['total_code_lines']:,}** |

---

## Файлы по типам

| Расширение | Количество |
|------------|------------|
"""
        for ext, count in sorted(report["file_types"].items(), key=lambda x: -x[1]):
            md += f"| {ext or '(нет)'} | {count} |\n"

        md += f"""
---

## Тесты

| Метрика | Значение |
|---------|----------|
| Тестовых файлов | {report['tests']['count']} |
| Модули | {', '.join(report['tests']['modules'][:10])}{'...' if len(report['tests']['modules']) > 10 else ''} |

---

## Git

| Метрика | Значение |
|---------|----------|
| Статус | {report['git']['status']} |
| Изменений | {report['git']['changes']} |

---

## Зависимости

| Метрика | Значение |
|---------|----------|
| Пакетов | {report['dependencies']['count']} |

"""
        if report['dependencies']['list']:
            md += "### Список зависимостей\n\n"
            for dep in report['dependencies']['list'][:20]:
                md += f"- {dep}\n"
            if len(report['dependencies']['list']) > 20:
                md += f"- ... и ещё {len(report['dependencies']['list']) - 20}\n"

        if report['errors']:
            md += "\n---\n\n## Ошибки\n\n"
            for err in report['errors']:
                md += f"- {err}\n"

        return md


def main():
    parser = argparse.ArgumentParser(description="Project Scanner")
    parser.add_argument("--root", default=".", help="Root directory")
    parser.add_argument("--output", default="project_scan.json", help="JSON output file")
    parser.add_argument("--markdown", default="project_scan.md", help="Markdown output file")
    args = parser.parse_args()

    scanner = ProjectScanner(args.root)
    report = scanner.scan()

    # JSON
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"[OK] JSON отчёт: {args.output}")

    # Markdown
    md = scanner.generate_markdown_report()
    with open(args.markdown, 'w', encoding='utf-8') as f:
        f.write(md)
    print(f"[OK] Markdown отчёт: {args.markdown}")

    # Вывод сводки
    print(f"\n{'='*50}")
    print(f"Файлов: {report['summary']['total_files']}")
    print(f"Размер: {report['summary']['total_size_kb']} KB")
    print(f"Python строк: {report['code_stats']['python_lines']:,}")
    print(f"SQL строк: {report['code_stats']['sql_lines']:,}")
    print(f"Тестов: {report['tests']['count']}")
    print(f"Git: {report['git']['status']}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
