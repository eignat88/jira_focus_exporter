import logging
import re
from dataclasses import dataclass, field

from jira_client import JiraClient
from .registry_parser import SearchEntry
from .jira_searcher import get_linked_issue


@dataclass
class AnalyzedMatch:
    mode: str
    description: str
    issue_key: str
    issue_url: str
    found_in: str
    fragment: str
    status: str  # Найдено / Частично найдено / Не найдено / Требует ручной проверки / Противоречие
    confidence: str  # Высокая / Средняя / Низкая
    category: str = ""


CONFIDENCE_HIGH = "Высокая"
CONFIDENCE_MEDIUM = "Средняя"
CONFIDENCE_LOW = "Низкая"

STATUS_FOUND = "Найдено"
STATUS_PARTIAL = "Частично найдено"
STATUS_NOT_FOUND = "Не найдено"
STATUS_MANUAL = "Требует ручной проверки"
STATUS_CONTRADICTION = "Противоречие"

# Типы вложений → категория
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
PDF_EXTENSIONS = {".pdf"}
DOC_EXTENSIONS = {".doc", ".docx"}
XLS_EXTENSIONS = {".xls", ".xlsx"}
XML_EXTENSIONS = {".xml"}
JSON_EXTENSIONS = {".json"}


def _text_matches_keywords(text: str, keywords: list[str]) -> tuple[bool, list[str]]:
    if not text or not keywords:
        return False, []
    text_lower = text.lower()
    matched = [kw for kw in keywords if kw.lower() in text_lower]
    return bool(matched), matched


def _extract_fragment(text: str, keyword: str, context_len: int = 120) -> str:
    if not text or not keyword:
        return ""
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        return text[:context_len]
    start = max(0, idx - context_len // 2)
    end = min(len(text), idx + len(keyword) + context_len // 2)
    fragment = text[start:end].replace("\n", " ").strip()
    if start > 0:
        fragment = "..." + fragment
    if end < len(text):
        fragment = fragment + "..."
    return fragment


def _determine_confidence(
    matched_count: int,
    total_keywords: int,
    found_in: str = "",
    has_attachment: bool = False,
) -> str:
    """Evaluate confidence based on keyword match quality and context.

    Rules (in priority order):
    - Exact keyword match in summary → Высокая
    - Match in description + has attachment (screenshot/mockup) → Высокая
    - Match in description alone (high ratio) → Высокая
    - Match in comment → Средняя
    - Match in linked issue → Средняя
    - Otherwise → Низкая
    """
    if total_keywords == 0:
        return CONFIDENCE_LOW

    ratio = matched_count / total_keywords

    # Check if found in summary (highest signal)
    found_in_lower = (found_in or "").lower()
    in_summary = "summary" in found_in_lower or found_in_lower.startswith("summary")

    if in_summary and ratio >= 0.3:
        return CONFIDENCE_HIGH

    # Match in description + attachment evidence
    in_description = "описание" in found_in_lower or "description" in found_in_lower
    if in_description and has_attachment and ratio >= 0.3:
        return CONFIDENCE_HIGH

    # High keyword ratio in description
    if in_description and ratio >= 0.5:
        return CONFIDENCE_HIGH

    # Comment-based matches
    if "комментарий" in found_in_lower or "comment" in found_in_lower:
        if ratio >= 0.5:
            return CONFIDENCE_MEDIUM
        return CONFIDENCE_LOW

    # Linked issue matches
    if "связанн" in found_in_lower or "linked" in found_in_lower:
        if ratio >= 0.5:
            return CONFIDENCE_MEDIUM
        return CONFIDENCE_LOW

    # Attachment-based matches
    if "вложение" in found_in_lower or "attachment" in found_in_lower:
        return CONFIDENCE_MEDIUM

    # Generic fallback by ratio
    if ratio >= 0.7:
        return CONFIDENCE_HIGH
    if ratio >= 0.3:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def _analyze_description(
    description: str, entry: SearchEntry, issue_key: str, issue_url: str,
    has_attachment: bool = False,
) -> AnalyzedMatch | None:
    if not description:
        return None
    found, matched = _text_matches_keywords(description, entry.keywords)
    if not found:
        return None
    fragment = _extract_fragment(description, matched[0])
    confidence = _determine_confidence(
        len(matched), len(entry.keywords),
        found_in="Описание", has_attachment=has_attachment,
    )
    return AnalyzedMatch(
        mode=entry.mode,
        description=entry.description,
        issue_key=issue_key,
        issue_url=issue_url,
        found_in="Описание",
        fragment=fragment,
        status=STATUS_FOUND,
        confidence=confidence,
        category=entry.category,
    )


def _analyze_comments(
    comments: list[dict], entry: SearchEntry, issue_key: str, issue_url: str
) -> list[AnalyzedMatch]:
    matches = []
    for comment in comments:
        body = comment.get("body") or ""
        if not body:
            continue
        found, matched = _text_matches_keywords(body, entry.keywords)
        if not found:
            continue
        fragment = _extract_fragment(body, matched[0])
        author = (comment.get("author") or {}).get("displayName", "неизвестно")
        found_in = f"Комментарий ({author})"
        confidence = _determine_confidence(
            len(matched), len(entry.keywords), found_in=found_in,
        )
        matches.append(AnalyzedMatch(
            mode=entry.mode,
            description=entry.description,
            issue_key=issue_key,
            issue_url=issue_url,
            found_in=found_in,
            fragment=fragment,
            status=STATUS_FOUND,
            confidence=confidence,
            category=entry.category,
        ))
    return matches


def _classify_attachment(filename: str, mime: str) -> str:
    """Classify attachment by file extension and mime type."""
    name_lower = filename.lower()
    ext = ""
    if "." in name_lower:
        ext = "." + name_lower.rsplit(".", 1)[-1]

    if ext in IMAGE_EXTENSIONS or mime.startswith("image/"):
        return "скриншот/макет"
    if ext in PDF_EXTENSIONS or "pdf" in mime:
        return "PDF-документ"
    if ext in DOC_EXTENSIONS or "word" in mime or "document" in mime:
        return "Word-документ"
    if ext in XLS_EXTENSIONS or "excel" in mime or "spreadsheet" in mime:
        return "Excel-таблица"
    if ext in XML_EXTENSIONS or "xml" in mime:
        return "XML-схема"
    if ext in JSON_EXTENSIONS or "json" in mime:
        return "JSON-схема"
    return "другое"


def _analyze_attachments(
    attachments: list[dict], entry: SearchEntry, issue_key: str, issue_url: str
) -> list[AnalyzedMatch]:
    matches = []
    for att in attachments:
        filename = att.get("filename") or ""
        mime = att.get("mimeType") or ""
        size = att.get("size") or 0

        # Check filename for keywords
        found, matched = _text_matches_keywords(filename, entry.keywords)
        if found:
            attachment_type = _classify_attachment(filename, mime)
            matches.append(AnalyzedMatch(
                mode=entry.mode,
                description=entry.description,
                issue_key=issue_key,
                issue_url=issue_url,
                found_in=f"Вложение: {filename}",
                fragment=f"Тип: {attachment_type}, MIME: {mime}, размер: {size} байт",
                status=STATUS_FOUND,
                confidence=CONFIDENCE_MEDIUM,
                category=entry.category,
            ))
            continue

        # Classify by extension for contextual evidence
        attachment_type = _classify_attachment(filename, mime)
        if attachment_type == "скриншот/макет":
            matches.append(AnalyzedMatch(
                mode=entry.mode,
                description=entry.description,
                issue_key=issue_key,
                issue_url=issue_url,
                found_in=f"Вложение (скриншот/макет): {filename}",
                fragment=f"Тип: {attachment_type}, MIME: {mime}, размер: {size} байт",
                status=STATUS_MANUAL,
                confidence=CONFIDENCE_LOW,
                category=entry.category,
            ))
        elif attachment_type in ("PDF-документ", "Word-документ"):
            matches.append(AnalyzedMatch(
                mode=entry.mode,
                description=entry.description,
                issue_key=issue_key,
                issue_url=issue_url,
                found_in=f"Вложение (документ): {filename}",
                fragment=f"Тип: {attachment_type}, MIME: {mime}, размер: {size} байт",
                status=STATUS_MANUAL,
                confidence=CONFIDENCE_LOW,
                category=entry.category,
            ))
        elif attachment_type in ("XML-схема", "JSON-схема"):
            matches.append(AnalyzedMatch(
                mode=entry.mode,
                description=entry.description,
                issue_key=issue_key,
                issue_url=issue_url,
                found_in=f"Вложение (схема): {filename}",
                fragment=f"Тип: {attachment_type}, MIME: {mime}, размер: {size} байт",
                status=STATUS_MANUAL,
                confidence=CONFIDENCE_MEDIUM,
                category=entry.category,
            ))
        elif attachment_type == "Excel-таблица":
            matches.append(AnalyzedMatch(
                mode=entry.mode,
                description=entry.description,
                issue_key=issue_key,
                issue_url=issue_url,
                found_in=f"Вложение (таблица): {filename}",
                fragment=f"Тип: {attachment_type}, MIME: {mime}, размер: {size} байт",
                status=STATUS_MANUAL,
                confidence=CONFIDENCE_LOW,
                category=entry.category,
            ))
    return matches


def _analyze_linked_issues(
    linked_issues: list[dict],
    entry: SearchEntry,
    issue_key: str,
    issue_url: str,
    client: JiraClient,
    max_depth: int,
    current_depth: int,
    visited: set[str],
) -> list[AnalyzedMatch]:
    matches = []
    for link in linked_issues:
        link_type = (link.get("type") or {}).get("name", "")
        linked = link.get("outwardIssue") or link.get("inwardIssue") or {}
        linked_key = linked.get("key")
        if not linked_key or linked_key in visited:
            continue
        if current_depth >= max_depth:
            continue

        visited.add(linked_key)
        linked_summary = (linked.get("fields") or {}).get("summary", "")

        # Check summary for keywords
        found, matched = _text_matches_keywords(linked_summary, entry.keywords)
        if found:
            fragment = _extract_fragment(linked_summary, matched[0])
            found_in = f"Связанная задача {linked_key} ({link_type})"
            confidence = _determine_confidence(
                len(matched), len(entry.keywords), found_in=found_in,
            )
            matches.append(AnalyzedMatch(
                mode=entry.mode,
                description=entry.description,
                issue_key=issue_key,
                issue_url=issue_url,
                found_in=found_in,
                fragment=fragment,
                status=STATUS_FOUND,
                confidence=confidence,
                category=entry.category,
            ))

        # Recurse into linked issue if depth allows
        if current_depth < max_depth - 1:
            linked_issue = get_linked_issue(client, linked_key)
            if linked_issue:
                linked_fields = linked_issue.get("fields", {})
                linked_desc = linked_fields.get("description") or ""
                linked_comments = (linked_fields.get("comment") or {}).get("comments") or []
                linked_attachments = linked_fields.get("attachment") or []
                linked_issue_links = (linked_fields.get("issuelinks") or [])
                linked_subtasks = (linked_fields.get("subtasks") or [])

                has_attachment = bool(linked_attachments)
                desc_match = _analyze_description(
                    linked_desc, entry, issue_key, issue_url,
                    has_attachment=has_attachment,
                )
                if desc_match:
                    desc_match.found_in = f"Описание связанной задачи {linked_key}"
                    matches.append(desc_match)

                matches.extend(
                    _analyze_comments(linked_comments, entry, issue_key, issue_url)
                )
                for m in matches[-len(linked_comments):]:
                    if m.found_in.startswith("Комментарий"):
                        m.found_in = f"Комментарий в {linked_key}: {m.found_in}"

                matches.extend(
                    _analyze_attachments(linked_attachments, entry, issue_key, issue_url)
                )

                # Analyze subtasks of the linked issue
                for subtask in linked_subtasks:
                    subtask_key = subtask.get("key")
                    if subtask_key and subtask_key not in visited:
                        visited.add(subtask_key)
                        subtask_summary = (subtask.get("fields") or {}).get("summary", "")
                        found_st, matched_st = _text_matches_keywords(subtask_summary, entry.keywords)
                        if found_st:
                            fragment_st = _extract_fragment(subtask_summary, matched_st[0])
                            found_in_st = f"Подзадача {subtask_key} связанной задачи {linked_key}"
                            confidence_st = _determine_confidence(
                                len(matched_st), len(entry.keywords), found_in=found_in_st,
                            )
                            matches.append(AnalyzedMatch(
                                mode=entry.mode,
                                description=entry.description,
                                issue_key=issue_key,
                                issue_url=issue_url,
                                found_in=found_in_st,
                                fragment=fragment_st,
                                status=STATUS_FOUND,
                                confidence=confidence_st,
                                category=entry.category,
                            ))

    return matches


def analyze_results(
    issue: dict,
    entry: SearchEntry,
    client: JiraClient,
    jira_url: str,
    include_attachments: bool,
    include_comments: bool,
    include_linked: bool,
    max_depth: int,
) -> list[AnalyzedMatch]:
    """Analyze a single Jira issue against a search entry."""
    issue_key = issue.get("key", "")
    fields = issue.get("fields", {})
    issue_url = f"{jira_url}/browse/{issue_key}"

    description = fields.get("description") or ""
    summary = fields.get("summary") or ""
    attachments = fields.get("attachment") or []
    has_attachment = bool(attachments)

    # Check summary separately for high-confidence keyword match
    summary_found, summary_matched = _text_matches_keywords(summary, entry.keywords)

    # Combine summary + description for keyword search
    full_text = f"{summary}\n{description}"
    found, matched = _text_matches_keywords(full_text, entry.keywords)

    if not found:
        return []

    matches: list[AnalyzedMatch] = []

    # If summary matches, add a dedicated summary match with high confidence
    if summary_found and summary_matched:
        fragment = _extract_fragment(summary, summary_matched[0])
        summary_confidence = _determine_confidence(
            len(summary_matched), len(entry.keywords),
            found_in="Summary", has_attachment=has_attachment,
        )
        matches.append(AnalyzedMatch(
            mode=entry.mode,
            description=entry.description,
            issue_key=issue_key,
            issue_url=issue_url,
            found_in="Summary",
            fragment=fragment,
            status=STATUS_FOUND,
            confidence=summary_confidence,
            category=entry.category,
        ))

    # Analyze description
    desc_match = _analyze_description(
        full_text, entry, issue_key, issue_url, has_attachment=has_attachment,
    )
    if desc_match:
        # Include summary in fragment
        if summary:
            desc_match.fragment = f"[{summary}] {desc_match.fragment}"
        # Avoid duplicate if summary already captured
        if not summary_found:
            matches.append(desc_match)
        else:
            # Still add description match if it has different keywords
            desc_found, desc_matched = _text_matches_keywords(description, entry.keywords)
            if desc_found and set(desc_matched) - set(summary_matched):
                matches.append(desc_match)

    # Analyze comments
    if include_comments:
        comments = (fields.get("comment") or {}).get("comments") or []
        matches.extend(_analyze_comments(comments, entry, issue_key, issue_url))

    # Analyze attachments
    if include_attachments:
        matches.extend(_analyze_attachments(attachments, entry, issue_key, issue_url))

    # Analyze linked issues (also searches subtasks of linked issues)
    if include_linked:
        linked_issues = fields.get("issuelinks") or []
        matches.extend(
            _analyze_linked_issues(
                linked_issues, entry, issue_key, issue_url,
                client, max_depth, current_depth=0, visited={issue_key},
            )
        )
        # Also search subtasks of the main issue
        subtasks = fields.get("subtasks") or []
        for subtask in subtasks:
            subtask_key = subtask.get("key")
            if not subtask_key:
                continue
            subtask_issue = get_linked_issue(client, subtask_key)
            if subtask_issue:
                st_fields = subtask_issue.get("fields", {})
                st_desc = st_fields.get("description") or ""
                st_summary = st_fields.get("summary") or ""
                st_full = f"{st_summary}\n{st_desc}"
                st_found, st_matched = _text_matches_keywords(st_full, entry.keywords)
                if st_found:
                    st_fragment = _extract_fragment(st_full, st_matched[0])
                    st_confidence = _determine_confidence(
                        len(st_matched), len(entry.keywords),
                        found_in=f"Подзадача {subtask_key}",
                    )
                    matches.append(AnalyzedMatch(
                        mode=entry.mode,
                        description=entry.description,
                        issue_key=issue_key,
                        issue_url=issue_url,
                        found_in=f"Подзадача {subtask_key}",
                        fragment=st_fragment,
                        status=STATUS_FOUND,
                        confidence=st_confidence,
                        category=entry.category,
                    ))

    return matches
