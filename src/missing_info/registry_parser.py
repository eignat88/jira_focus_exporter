import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SearchEntry:
    mode: str
    description: str
    category: str
    keywords: list[str] = field(default_factory=list)


MODES = [
    {
        "mode": "Режим 51 / Выборочная инвентаризация",
        "aliases": ["inventory", "selective inventory", "inventory_selection"],
        "keywords": [
            "CommitTaskLine",
            "GoToNextLine",
            "GetLocation",
            "AutoTaskExecutionForm",
            "SelectionWoTaskForm",
            "InventorySubMenu",
            "BusinessLogicService",
            "InventoryService",
        ],
    },
    {
        "mode": "Принудительная инвентаризация",
        "aliases": ["forced inventory", "GetLocationInfoInventory", "CreateNewTaskInventory"],
        "keywords": [
            "GetLocationInfoInventory",
            "CreateNewTaskInventory",
            "AutoTaskExecutionForm",
            "InventoryService",
        ],
    },
    {
        "mode": "Остатки",
        "aliases": ["remains", "GetRemainsByWmsLocation", "RemainsInfoDto"],
        "keywords": [
            "GetRemainsByWmsLocation",
            "NomenclatureRemainsDto",
            "RemainsInfoDto",
            "RemainsSelectionForm",
            "RemainsInfoForm",
        ],
    },
    {
        "mode": "Неопознанный товар",
        "aliases": ["unknown item", "GetLocationInventory"],
        "keywords": [
            "GetLocationInventory",
            "UnknownItemExecutionForm",
        ],
    },
    {
        "mode": "Проверить ячейку",
        "aliases": [
            "location check",
            "check location",
            "LoadWMSLocationItems",
            "DeleteWMSLocationItems",
        ],
        "keywords": [
            "LoadWMSLocationItems",
            "DeleteWMSLocationItems",
        ],
    },
]

CATEGORIES = [
    "последовательность экранов Android",
    "описание ошибок Android",
    "REST API-схемы",
    "SQL-процедуры",
    "бизнес-правила валидации",
    "альтернативные ветви",
    "обработка ошибок",
    "ограничения производительности",
    "макеты экранов",
    "тестовые сценарии",
    "BPMN-схемы",
    "бизнес-правила",
    "WCF-методы",
    "DTO",
    "таблицы AX/WMS",
]

# Дополнительные ключевые слова для JQL поиска по каждой категории
CATEGORY_JQL_KEYWORDS: dict[str, list[str]] = {
    "последовательность экранов Android": [
        "screen", "Activity", "Fragment", "ViewModel", "navigation", "UI", "layout",
    ],
    "описание ошибок Android": [
        "error", "exception", "crash", "bug", "StackTrace", "Logcat",
    ],
    "REST API-схемы": [
        "endpoint", "API", "request", "response", "JSON", "schema", "swagger",
    ],
    "SQL-процедуры": [
        "SQL", "procedure", "stored procedure", "tsd_", "WMS_",
    ],
    "бизнес-правила валидации": [
        "validation", "rule", "business rule", "validate", "check",
    ],
    "альтернативные ветви": [
        "alternative", "branch", "else", "fallback", "retry",
    ],
    "обработка ошибок": [
        "error handling", "exception", "try", "catch", "retry", "recover",
    ],
    "ограничения производительности": [
        "performance", "timeout", "slow", "optimize", "cache", "memory",
    ],
    "макеты экранов": [
        "mockup", "wireframe", "design", "UI", "layout", "screen", "mock",
    ],
    "тестовые сценарии": [
        "test case", "test scenario", "QA", "тест", "тестирование", "pytest",
    ],
    "BPMN-схемы": [
        "BPMN", "process", "workflow", "diagram", "схема", "блок-схема",
    ],
    "бизнес-правила": [
        "business rule", "BR", "правило", "условие", "логика",
    ],
    "WCF-методы": [
        "WCF", "SOAP", ".svc", "service contract", "DataContract",
    ],
    "DTO": [
        "DTO", "Data Transfer Object", "model", "data class",
    ],
    "таблицы AX/WMS": [
        "table", "tsd_", "WMS_", "AX", "InventSum", "InventDim",
    ],
}


def _detect_mode(text: str) -> str | None:
    text_lower = text.lower()
    for mode_info in MODES:
        mode_lower = mode_info["mode"].lower()
        # Exact match or text is a substring of mode
        # (e.g. "режим 51" matches "режим 51 / выборочная инвентаризация")
        if mode_lower in text_lower or text_lower in mode_lower:
            return mode_info["mode"]
        for alias in mode_info["aliases"]:
            if alias.lower() in text_lower:
                return mode_info["mode"]
    return None


def _detect_category(text: str) -> str:
    text_lower = text.lower()
    for cat in CATEGORIES:
        if cat.lower() in text_lower:
            return cat
    return "бизнес-правила"


def get_category_keywords(category: str) -> list[str]:
    """Return additional JQL search keywords for a given category."""
    return list(CATEGORY_JQL_KEYWORDS.get(category, []))


def _get_keywords_for_mode(mode: str) -> list[str]:
    for mode_info in MODES:
        if mode_info["mode"] == mode:
            return list(mode_info["keywords"])
    return []


def _extract_keywords_from_row(cells: list[str]) -> list[str]:
    keywords = []
    for cell in cells:
        # Extract backtick-quoted terms
        for match in re.finditer(r"`([^`]+)`", cell):
            keywords.append(match.group(1))
        # Extract CamelCase identifiers
        for match in re.finditer(r"\b([A-Z][a-zA-Z]+(?:Dto|Form|Service|Inventory|Line|Task))\b", cell):
            term = match.group(1)
            if term not in keywords:
                keywords.append(term)
    return keywords


def parse_registry(path: Path) -> list[SearchEntry]:
    """Parse a markdown table registry file into SearchEntry objects.

    Expected format:
    | Режим | Что ищем | Категория |
    |-------|----------|-----------|
    | ...   | ...      | ...       |
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    entries: list[SearchEntry] = []
    header_found = False
    separator_found = False

    for line in lines:
        stripped = line.strip()

        if not stripped.startswith("|"):
            if header_found and separator_found:
                break
            continue

        cells = [c.strip() for c in stripped.split("|")[1:-1]]

        if not header_found:
            header_found = True
            continue

        if not separator_found:
            if all(set(c) <= {"-", ":", " "} for c in cells):
                separator_found = True
            continue

        if len(cells) < 2:
            continue

        mode_text = cells[0] if len(cells) > 0 else ""
        description = cells[1] if len(cells) > 1 else ""
        category = cells[2] if len(cells) > 2 else ""

        mode = _detect_mode(mode_text) or _detect_mode(description)
        if not mode:
            # Fallback: use raw text as mode
            mode = mode_text or "Неизвестный режим"

        if not category:
            category = _detect_category(description)

        keywords = _get_keywords_for_mode(mode) + _extract_keywords_from_row(cells)
        # Deduplicate
        seen = set()
        unique_keywords = []
        for kw in keywords:
            if kw.lower() not in seen:
                seen.add(kw.lower())
                unique_keywords.append(kw)

        entries.append(SearchEntry(
            mode=mode,
            description=description,
            category=category,
            keywords=unique_keywords,
        ))

    return entries
