from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DevGroupUser:
    account_id: str = ""
    name: str = ""
    key: str = ""
    display_name: str = ""
    email: str = ""
    active: bool = True

    @property
    def identities(self) -> set[str]:
        return {
            str(value).strip().lower()
            for value in (
                self.account_id,
                self.name,
                self.key,
                self.display_name,
                self.email,
            )
            if str(value).strip()
        }


@dataclass
class ActivityEvent:
    issue_key: str
    event_type: str
    author: str = ""
    created: str = ""
    field: str = ""
    from_value: str = ""
    to_value: str = ""

    def as_dict(self) -> dict:
        return {
            "issue_key": self.issue_key,
            "event_type": self.event_type,
            "author": self.author,
            "created": self.created,
            "field": self.field,
            "from": self.from_value,
            "to": self.to_value,
        }


@dataclass
class ActualTask:
    issue_key: str
    summary: str = ""
    status: str = ""
    status_category: str = ""
    assignee: str = ""
    reporter: str = ""
    priority: str = ""
    created: str = ""
    updated: str = ""
    due_date: str = ""
    actual_score: int = 0
    categories: list[str] = field(default_factory=list)
    active_users: list[str] = field(default_factory=list)
    activity_events: list[ActivityEvent] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    raw_issue: dict = field(default_factory=dict)
    days_without_activity: int | None = None
    days_overdue: int | None = None
    last_activity: str = ""

    def as_dict(self) -> dict:
        return {
            "issue_key": self.issue_key,
            "summary": self.summary,
            "status": self.status,
            "status_category": self.status_category,
            "assignee": self.assignee,
            "reporter": self.reporter,
            "priority": self.priority,
            "created": self.created,
            "updated": self.updated,
            "due_date": self.due_date,
            "actual_score": self.actual_score,
            "categories": self.categories,
            "active_users": self.active_users,
            "activity_events": [event.as_dict() for event in self.activity_events],
            "reasons": self.reasons,
        }


@dataclass
class ReleaseIssue:
    issue_key: str
    summary: str
    status: str
    assignee: str
    priority: str
    updated: str
    due_date: str


@dataclass
class ReleaseInfo:
    version_name: str
    version_description: str
    issues: list[ReleaseIssue] = field(default_factory=list)


@dataclass
class ActualTasksResult:
    tasks: list[ActualTask]
    users_count: int
    days: int
    stale_days: int
    generated_at: datetime
    report_path: str | None = None
    releases: list[ReleaseInfo] = field(default_factory=list)

    @property
    def events(self) -> list[ActivityEvent]:
        return [event for task in self.tasks for event in task.activity_events]
