# Documentation Maintenance Procedure

> Last updated: 2025-01-07

## Overview

This document defines the maintenance procedure for project documentation, including agent roles, triggers, update checklists, validation steps, and schedules.

---

## 1. Agent Roles

| Role | Responsibilities | Access |
|------|------------------|--------|
| **Docs Maintainer** | Primary keeper of documentation; reviews and merges all doc changes | Full access to `docs/`, `*.md` root files |
| **Data Engineer** | Maintains data-related docs (`data_engineering/`, `data_quality/`, `data_strategy/`) | `docs/data_engineering/`, `docs/data_quality/`, `docs/data_strategy/` |
| **QA Lead** | Maintains QA docs and test reports | `docs/qa/`, `reports/` |
| **Project Tracker** | Keeps task registers, Gantt, status, and admin logs current | `task*.md`, `task_register.md`, `gantt.md`, `project_status.md`, `admin_log.md` |
| **Architecture Owner** | Maintains architecture docs and technical plans | `docs/ARCHITECTURE.md`, `plan.md`, `ROADMAP.md` |

### Role Assignment

- Roles are assigned at project start and updated in `AGENTS.md`.
- A role may be held by a human or an AI agent.
- When a role is reassigned, update `AGENTS.md` and notify the team.

---

## 2. Triggers

Documentation must be updated when any of the following occurs:

| Trigger | Affected Docs | Priority |
|---------|---------------|----------|
| New task created | `task*.md`, `task_register.md`, `gantt.md` | High |
| Task status changed | `task*.md`, `task_register.md`, `project_status.md` | High |
| Architecture decision made | `docs/ARCHITECTURE.md`, `plan.md` | High |
| Data pipeline change | `docs/data_engineering/current_state.md` | Medium |
| Data quality issue found | `docs/data_quality/raw_validation.md` | Medium |
| QA test results | `reports/`, `docs/qa/` | Medium |
| Sprint/milestone completed | `ROADMAP.md`, `project_status.md` | High |
| Admin action taken | `admin_log.md` | Low |
| Agent role changed | `AGENTS.md` | High |
| Project summary update | `docs/PROJECT_SUMMARY.md` | Low |

---

## 3. Update Checklist

### Before Any Documentation Change

- [ ] Identify the trigger (use the table above)
- [ ] Determine the affected document(s)
- [ ] Check for existing content that may conflict
- [ ] Read the document fully before editing

### During Update

- [ ] Follow the document's internal format/structure
- [ ] Use consistent terminology (cross-reference `table_registry.md` if data-related)
- [ ] Add/update timestamps where present
- [ ] Keep tables aligned and markdown well-formatted
- [ ] Don't break links between documents

### After Update

- [ ] Verify the file renders correctly (no broken markdown)
- [ ] Update `docs/DOC_INDEX.md` if adding a new document
- [ ] Update `task_register.md` if task-related
- [ ] Log the change in `admin_log.md` if significant

### Category-Specific Checklists

#### Status Documents (`project_status.md`, `task_register.md`, `gantt.md`, `admin_log.md`)

- [ ] Task counts match actual `task*.md` files
- [ ] Gantt dates are realistic and current
- [ ] Status reflects actual project state

#### Architecture Documents (`docs/ARCHITECTURE.md`, `docs/PROJECT_SUMMARY.md`, `AGENTS.md`)

- [ ] Diagrams match current system design
- [ ] Component descriptions are accurate
- [ ] Agent roles reflect current assignments

#### Technical Documents (`plan.md`, `ROADMAP.md`, `DDS_RUN_MEMO.md`)

- [ ] Milestones are current
- [ ] Dependencies are documented
- [ ] Technical decisions have rationale

#### Data Documents (`docs/data_engineering/`, `docs/data_quality/`, `docs/data_strategy/`)

- [ ] Table registry matches actual database schema
- [ ] Data quality thresholds are documented
- [ ] ETL pipeline steps are current

#### QA Documents (`QA_REQUIREMENTS.md`, `reports/`)

- [ ] Test results are from the latest run
- [ ] Requirements traceability is maintained
- [ ] Issue tracking is current

---

## 4. Validation

### Automated Validation

Run the update script to check documentation consistency:

```bash
bash scripts/update_docs.sh --validate
```

This checks:
- All referenced files exist
- Cross-document links are valid
- Required sections are present in each document type
- Timestamps are current (within 30 days for active docs)

### Manual Validation

| Check | Frequency | Owner |
|-------|-----------|-------|
| Content accuracy | Every update | Document owner |
| Cross-reference consistency | Weekly | Docs Maintainer |
| Archive/stale content review | Monthly | Docs Maintainer |
| Full documentation audit | Quarterly | All agents |

### Validation Rules

1. Every `task*.md` file must have a corresponding entry in `task_register.md`
2. Every document in `docs/` must appear in `docs/DOC_INDEX.md`
3. Every document must have a "Last updated" field
4. No orphaned cross-references (if doc A links to doc B, doc B must exist)
5. Data docs must reference the correct table names from `table_registry.md`

---

## 5. Schedule

### Daily

- Update `task*.md` files as tasks change
- Update `project_status.md` with current blockers

### Weekly (every Monday)

- Update `gantt.md` with actual progress vs planned
- Review `admin_log.md` for completeness
- Run `scripts/update_docs.sh --validate`

### Biweekly (end of sprint)

- Update `ROADMAP.md` milestones
- Update `docs/PROJECT_SUMMARY.md` with current status
- Archive completed task files

### Monthly

- Full documentation audit
- Review and update `docs/ARCHITECTURE.md`
- Update `docs/data_engineering/current_state.md`
- Review `docs/data_strategy/load_plan.md` for relevance
- Update `AGENTS.md` role assignments if needed

### Quarterly

- Complete documentation overhaul
- Archive outdated documents to `docs/archive/`
- Review and update all cross-references
- Generate documentation coverage report

---

## 6. Document Lifecycle

```
Created → Active → Reviewed → Updated → (archived | deprecated)
```

- **Created**: Document is first written
- **Active**: Document is current and maintained
- **Reviewed**: Document has been validated for accuracy
- **Updated**: Document has been modified (returns to Active)
- **Archived**: Document is no longer current but kept for reference
- **Deprecated**: Document is superseded by another

### Archival Process

1. Move document to `docs/archive/`
2. Update `docs/DOC_INDEX.md` to reflect archival
3. Add archival note to document header
4. Log archival in `admin_log.md`

---

## 7. Conflict Resolution

When multiple agents need to update the same document:

1. Check `admin_log.md` for the most recent edit
2. The **last editor** has priority for the next 24 hours
3. If concurrent edits are needed, coordinate via the task system
4. For critical conflicts, the **Docs Maintainer** makes the final call

---

## Appendix: Quick Reference

| What to do | Where to look |
|------------|---------------|
| Update a task | Edit `task*.md`, then update `task_register.md` |
| Add a new document | Create it, then add to `docs/DOC_INDEX.md` |
| Check all docs are current | Run `bash scripts/update_docs.sh --validate` |
| Find a document | Check `docs/DOC_INDEX.md` |
| Report a doc issue | Create a task with summary "Doc issue: [description]" |
