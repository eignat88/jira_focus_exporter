from config import AppConfig, JiraFilterConfig


def escape_jql_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def quote_values(values: list[str]) -> str:
    return ", ".join(f'"{escape_jql_value(value)}"' for value in values)


def unquoted_values(values: list[str]) -> str:
    return ", ".join(escape_jql_value(value) for value in values)


def clean_jql(jql: str) -> str:
    return " ".join(jql.split())


def build_assignee_jql(config: JiraFilterConfig) -> str:
    if config.assignee:
        return f'assignee = "{escape_jql_value(config.assignee)}"'
    return "assignee = currentUser()"


def build_status_jql(config: JiraFilterConfig) -> str:
    clauses = []
    if config.excluded_status_categories:
        clauses.append(
            f"statusCategory not in ({quote_values(config.excluded_status_categories)})"
        )
    if config.excluded_statuses:
        clauses.append(f"status not in ({quote_values(config.excluded_statuses)})")
    if config.included_statuses:
        clauses.append(f"status in ({quote_values(config.included_statuses)})")
    return " AND ".join(clauses) if clauses else ""


def build_project_jql(config: JiraFilterConfig) -> str | None:
    if not config.projects:
        return None
    return f"project in ({quote_values(config.projects)})"


def build_issue_type_jql(config: JiraFilterConfig) -> str | None:
    if not config.issue_types:
        return None
    return f"issuetype in ({quote_values(config.issue_types)})"


def build_priority_jql(priority_names: list[str]) -> str | None:
    if not priority_names:
        return None
    return f"priority in ({quote_values(priority_names)})"


def build_labels_jql(labels: list[str]) -> str | None:
    if not labels:
        return None
    return f"labels in ({unquoted_values(labels)})"


def base_clauses(config: JiraFilterConfig) -> list[str]:
    clauses = []
    for clause in (
        build_project_jql(config),
        build_issue_type_jql(config),
        build_assignee_jql(config),
        build_status_jql(config),
    ):
        if clause:
            clauses.append(clause)
    return clauses


def append_extra_clause(clauses: list[str], extra_jql: str) -> list[str]:
    if extra_jql:
        clauses.append(f"({extra_jql})")
    return clauses


def build_focus_conditions(config: JiraFilterConfig, existing_priorities: list[str]) -> list[str]:
    conditions = []
    priority_jql = build_priority_jql(existing_priorities)
    label_jql = build_labels_jql(config.focus_labels)
    if priority_jql:
        conditions.append(priority_jql)
    conditions.append(f"due <= {config.due_soon_days}d")
    conditions.append("due < now()")
    if config.include_empty_due_in_focus:
        conditions.append("due is EMPTY")
    if label_jql:
        conditions.append(label_jql)
    conditions.append(f"updated <= -{config.stale_days}d")
    return conditions


def build_focus_jql(config: JiraFilterConfig, existing_priorities: list[str]) -> str:
    clauses = base_clauses(config)
    focus_conditions = build_focus_conditions(config, existing_priorities)
    clauses.append(f"({' OR '.join(focus_conditions)})")
    append_extra_clause(clauses, config.focus_extra_jql)
    return clean_jql(
        " AND ".join(clauses) + " ORDER BY priority DESC, due ASC, updated ASC"
    )


def build_assigned_jql(config: JiraFilterConfig) -> str:
    clauses = append_extra_clause(base_clauses(config), config.assigned_extra_jql)
    return clean_jql(" AND ".join(clauses) + " ORDER BY updated DESC")


def build_wms_activity_jql(config: AppConfig) -> str:
    clauses = ["updated >= startOfDay()"]
    for clause in (build_project_jql(config.filters), build_issue_type_jql(config.filters)):
        if clause:
            clauses.append(clause)
    if config.wms.activity_extra_jql:
        clauses.append(f"({config.wms.activity_extra_jql})")
    return clean_jql(" AND ".join(clauses) + " ORDER BY updated DESC")
