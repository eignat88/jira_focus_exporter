import logging
import time
from dataclasses import dataclass
from pathlib import Path

from jira_client import JiraClient
from config import AppConfig

from .registry_parser import parse_registry, SearchEntry, get_category_keywords
from .jira_searcher import search_jira
from .analyzer import analyze_results, AnalyzedMatch
from .reporter import write_report, write_log


@dataclass
class SearchResult:
    matches: list[AnalyzedMatch]
    report_path: str | None
    log_path: str | None


def run_search(
    config: AppConfig,
    client: JiraClient,
    args,
) -> SearchResult:
    registry_path = Path(args.registry)
    output_dir = Path(args.output) if args.output else config.export_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Поддержка нескольких проектов через запятую
    jira_projects = _parse_projects(args.jira_project)
    extra_keywords = [k.strip() for k in args.keywords.split(",")] if args.keywords else []
    max_depth = args.max_depth
    include_attachments = args.include_attachments
    include_comments = args.include_comments
    include_linked = args.include_linked
    jira_components = _parse_comma_separated(args.jira_components)
    jira_labels = _parse_comma_separated(args.jira_labels)

    logging.info("Режим: search-missing-info")
    logging.info("Реестр: %s", registry_path)
    logging.info("Проекты Jira: %s", ", ".join(jira_projects) if jira_projects else "<все>")
    logging.info("Компоненты Jira: %s", ", ".join(jira_components) if jira_components else "<все>")
    logging.info("Метки Jira: %s", ", ".join(jira_labels) if jira_labels else "<все>")
    logging.info("Глубина обхода связей: %s", max_depth)
    logging.info("Анализ вложений: %s", include_attachments)
    logging.info("Анализ комментариев: %s", include_comments)
    logging.info("Анализ связанных задач: %s", include_linked)

    entries = parse_registry(registry_path)
    logging.info("Запросов из реестра: %d", len(entries))

    all_matches: list[AnalyzedMatch] = []
    log_lines: list[str] = []

    for entry in entries:
        logging.info(
            "Поиск: [%s] %s (%s)", entry.mode, entry.description, entry.category
        )
        log_lines.append(f"## Запрос: {entry.description}")
        log_lines.append(f"- Режим: {entry.mode}")
        log_lines.append(f"- Категория: {entry.category}")

        # Ключевые слова: из реестра + доп. от пользователя + категориальные
        cat_keywords = get_category_keywords(entry.category)
        keywords = entry.keywords + extra_keywords + cat_keywords
        # Дедупликация ключевых слов
        keywords = list(dict.fromkeys(keywords))

        jql = _build_search_jql(jira_projects, keywords, jira_components, jira_labels)
        log_lines.append(f"- JQL: {jql}")

        issues = search_jira(client, jql)
        log_lines.append(f"- Найдено задач: {len(issues)}")
        log_lines.append("")

        for issue in issues:
            matches = analyze_results(
                issue=issue,
                entry=entry,
                client=client,
                jira_url=config.jira_url,
                include_attachments=include_attachments,
                include_comments=include_comments,
                include_linked=include_linked,
                max_depth=max_depth,
            )
            all_matches.extend(matches)

        # Rate limiting between search queries
        time.sleep(0.5)

    # Дедупликация результатов
    all_matches = _deduplicate_matches(all_matches)

    report_path = write_report(all_matches, output_dir)
    log_path = write_log(log_lines, output_dir)

    logging.info("Найдено совпадений (после дедупликации): %d", len(all_matches))
    logging.info("Отчёт: %s", report_path)
    logging.info("Лог: %s", log_path)

    return SearchResult(
        matches=all_matches,
        report_path=str(report_path),
        log_path=str(log_path),
    )


def _parse_projects(raw: str | None) -> list[str]:
    """Parse comma-separated project keys, stripping whitespace."""
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _parse_comma_separated(raw: str | None) -> list[str]:
    """Parse comma-separated values, stripping whitespace. Returns [] if empty/None."""
    if not raw:
        return []
    return [v.strip() for v in raw.split(",") if v.strip()]


def _build_search_jql(
    projects: list[str],
    keywords: list[str],
    components: list[str] | None = None,
    labels: list[str] | None = None,
) -> str:
    clauses: list[str] = []

    # Project filter
    if len(projects) == 1:
        clauses.append(f'project = "{projects[0]}"')
    elif projects:
        project_list = ", ".join(f'"{p}"' for p in projects)
        clauses.append(f"project in ({project_list})")

    # Component filter
    if components:
        component_list = ", ".join(f'"{c}"' for c in components)
        clauses.append(f"component in ({component_list})")

    # Label filter
    if labels:
        label_list = ", ".join(f'"{lb}"' for lb in labels)
        clauses.append(f"labels in ({label_list})")

    # Keyword (text) filter
    if keywords:
        text_clauses = " OR ".join(f'text ~ "{kw}"' for kw in keywords if kw)
        clauses.append(f"({text_clauses})")

    if clauses:
        return " AND ".join(clauses)
    return "ORDER BY updated DESC"


def _deduplicate_matches(matches: list[AnalyzedMatch]) -> list[AnalyzedMatch]:
    """Merge matches that refer to the same Jira issue + mode into one entry.

    The merged entry lists all categories found and picks the highest confidence.
    """
    from .analyzer import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW

    type_order = {CONFIDENCE_HIGH: 3, CONFIDENCE_MEDIUM: 2, CONFIDENCE_LOW: 1}

    # Group by (issue_key, mode)
    grouped: dict[tuple[str, str], list[AnalyzedMatch]] = {}
    for m in matches:
        key = (m.issue_key, m.mode)
        grouped.setdefault(key, []).append(m)

    deduplicated: list[AnalyzedMatch] = []
    for (_key, group) in grouped.items():
        if len(group) == 1:
            deduplicated.append(group[0])
            continue

        # Merge: combine found_in, pick highest confidence, combine categories
        first = group[0]
        all_found_in = list(dict.fromkeys(m.found_in for m in group))
        all_categories = list(dict.fromkeys(m.category for m in group if m.category))

        # Pick highest confidence
        best = max(group, key=lambda m: type_order.get(m.confidence, 0))

        # Combine fragments (up to 3 most distinct)
        seen_fragments: list[str] = []
        for m in group:
            if m.fragment and m.fragment not in seen_fragments:
                seen_fragments.append(m.fragment)
        combined_fragment = " | ".join(seen_fragments[:3])

        merged = AnalyzedMatch(
            mode=first.mode,
            description=first.description,
            issue_key=first.issue_key,
            issue_url=first.issue_url,
            found_in="; ".join(all_found_in),
            fragment=combined_fragment,
            status=first.status,
            confidence=best.confidence,
            category=", ".join(all_categories) if all_categories else first.category,
        )
        deduplicated.append(merged)

    return deduplicated
