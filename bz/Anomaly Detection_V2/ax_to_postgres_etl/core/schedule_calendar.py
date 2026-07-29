"""Load scheduling calendar for ETL operations."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import calendar


@dataclass
class ScheduleEntry:
    """A scheduled load entry."""
    table_name: str
    load_mode: str
    day_of_week: int  # 0=Monday, 6=Sunday
    hour: int
    minute: int = 0
    enabled: bool = True
    estimated_duration_minutes: int = 60


class ScheduleCalendar:
    """
    Calendar-based scheduling for ETL loads.

    Features:
    - Weekly schedule management
    - Conflict detection
    - Duration estimation
    - Next run calculation
    """

    def __init__(self):
        self._entries: List[ScheduleEntry] = []

    def add_entry(self, entry: ScheduleEntry):
        """Add a schedule entry."""
        self._entries.append(entry)

    def add_entries(self, entries: List[ScheduleEntry]):
        """Add multiple entries."""
        for entry in entries:
            self.add_entry(entry)

    def get_next_run(self, table_name: Optional[str] = None) -> Optional[datetime]:
        """Get next scheduled run time."""
        now = datetime.now()
        candidates = []

        for entry in self._entries:
            if table_name and entry.table_name != table_name:
                continue
            if not entry.enabled:
                continue

            # Calculate next occurrence
            days_ahead = entry.day_of_week - now.weekday()
            if days_ahead < 0:
                days_ahead += 7

            next_run = now.replace(
                hour=entry.hour,
                minute=entry.minute,
                second=0,
                microsecond=0
            ) + timedelta(days=days_ahead)

            if next_run <= now:
                next_run += timedelta(days=7)

            candidates.append((next_run, entry))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0])
        return candidates[0][0]

    def get_weekly_schedule(self) -> Dict[str, List[str]]:
        """Get weekly schedule as day -> entries."""
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        schedule = {day: [] for day in days}

        for entry in self._entries:
            if not entry.enabled:
                continue
            day_name = days[entry.day_of_week]
            time_str = f"{entry.hour:02d}:{entry.minute:02d}"
            schedule[day_name].append(
                f"{time_str} - {entry.table_name} ({entry.load_mode}, ~{entry.estimated_duration_minutes}min)"
            )

        # Sort each day's entries
        for day in schedule:
            schedule[day].sort()

        return schedule

    def detect_conflicts(self) -> List[Dict]:
        """Detect scheduling conflicts (overlapping runs)."""
        conflicts = []

        for i, entry1 in enumerate(self._entries):
            for entry2 in self._entries[i+1:]:
                if entry1.day_of_week != entry2.day_of_week:
                    continue
                if not entry1.enabled or not entry2.enabled:
                    continue

                # Check time overlap
                start1 = entry1.hour * 60 + entry1.minute
                end1 = start1 + entry1.estimated_duration_minutes
                start2 = entry2.hour * 60 + entry2.minute
                end2 = start2 + entry2.estimated_duration_minutes

                if start1 < end2 and start2 < end1:
                    conflicts.append({
                        "entry1": entry1.table_name,
                        "entry2": entry2.table_name,
                        "day": ["Monday", "Tuesday", "Wednesday", "Thursday",
                                "Friday", "Saturday", "Sunday"][entry1.day_of_week],
                        "overlap_minutes": min(end1, end2) - max(start1, start2),
                    })

        return conflicts

    def summary(self) -> str:
        """Generate calendar summary."""
        schedule = self.get_weekly_schedule()

        lines = []
        lines.append(f"{'='*70}")
        lines.append(f"LOAD SCHEDULE CALENDAR")
        lines.append(f"{'='*70}")

        for day, entries in schedule.items():
            if entries:
                lines.append(f"\n  {day}:")
                for entry in entries:
                    lines.append(f"    {entry}")

        conflicts = self.detect_conflicts()
        if conflicts:
            lines.append(f"\n  ⚠ Conflicts detected:")
            for c in conflicts:
                lines.append(
                    f"    - {c['entry1']} & {c['entry2']} on {c['day']} "
                    f"({c['overlap_minutes']}min overlap)"
                )
        else:
            lines.append(f"\n  ✓ No scheduling conflicts")

        next_run = self.get_next_run()
        if next_run:
            lines.append(f"\n  Next run: {next_run.strftime('%Y-%m-%d %H:%M')}")

        lines.append(f"{'='*70}")
        return "\n".join(lines)
