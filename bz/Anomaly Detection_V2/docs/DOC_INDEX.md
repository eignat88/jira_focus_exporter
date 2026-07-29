# Documentation Index

> Last updated: 2025-01-07
> Maintained by: Docs Maintainer

This index provides a comprehensive overview of all documentation in the Anomaly Detection project.

---

## Status Documents

Documents tracking project progress, tasks, and administrative actions.

| File | Location | Description | Last Updated |
|------|----------|-------------|--------------|
| `project_status.md` | Root | Current project status, blockers, and next steps | 2025-01-07 |
| `task_register.md` | Root | Master register of all tasks with status | 2025-01-07 |
| `gantt.md` | Root | Gantt chart of project timeline | 2025-01-07 |
| `admin_log.md` | Root | Administrative actions and decisions log | 2025-01-07 |

---

## Architecture Documents

Documents describing system design, components, and agent roles.

| File | Location | Description | Last Updated |
|------|----------|-------------|--------------|
| `ARCHITECTURE.md` | `docs/` | System architecture and component design | 2025-01-07 |
| `PROJECT_SUMMARY.md` | `docs/` | High-level project summary and objectives | 2025-01-07 |
| `AGENTS.md` | Root | Agent roles and responsibilities | 2025-01-07 |
| `AGENTS_FLOW.md` | Root | Agent interaction flow diagram | 2025-01-07 |
| `PROJECT_MAP.md` | Root | Project directory structure | 2025-01-07 |

---

## Technical Documents

Documents covering implementation plans, roadmaps, and technical specifications.

| File | Location | Description | Last Updated |
|------|----------|-------------|--------------|
| `plan.md` | Root | Implementation plan | 2025-01-07 |
| `ROADMAP.md` | Root | Project roadmap and milestones | 2025-01-07 |
| `requirements.txt` | Root | Python dependencies | 2025-01-07 |
| `DDS_RUN_MEMO.md` | Root | DDS execution memo | 2025-01-07 |

---

## Data Documents

Documents describing data engineering, quality, and strategy.

| File | Location | Description | Last Updated |
|------|----------|-------------|--------------|
| `current_state.md` | `docs/data_engineering/` | Current data pipeline state | 2025-01-07 |
| `raw_validation.md` | `docs/data_quality/` | Raw data validation rules | 2025-01-07 |
| `load_plan.md` | `docs/data_strategy/` | Data loading strategy | 2025-01-07 |
| `table_registry.md` | `docs/data_strategy/` | Database table registry | 2025-01-07 |

---

## Task Documents

Individual task specifications and tracking.

| File | Location | Description | Status |
|------|----------|-------------|--------|
| `task5.md` | Root | Task 5 | See file |
| `task6.md` | Root | Task 6 | See file |
| `task7.md` | Root | Task 7 | See file |
| `task8.md` | Root | Task 8 | See file |
| `task9.md` | Root | Task 9 | See file |
| `task10.md` | Root | Task 10 | See file |
| `task11.md` | Root | Task 11 | See file |
| `task12.md` | Root | Task 12 | See file |
| `task13.md` | Root | Task 13 | See file |
| `task14.md` | Root | Task 14 | See file |
| `task15.md` | Root | Task 15 | See file |
| `task16.md` | Root | Task 16 | See file |
| `task17.md` | Root | Task 17 | See file |
| `task18.md` | Root | Task 18 | See file |
| `task19.md` | Root | Task 19 | See file |
| `task20.md` | Root | Task 20 | See file |
| `task21.md` | Root | Task 21 | See file |

---

## QA Documents

Quality assurance requirements and test reports.

| File | Location | Description | Last Updated |
|------|----------|-------------|--------------|
| `QA_REQUIREMENTS.md` | `docs/qa/` | QA requirements specification | 2025-01-07 |

### Reports

| File | Location | Description |
|------|----------|-------------|
| `data_extraction_plan.md` | `reports/` | Data extraction plan |
| `database_report.md` | `reports/` | Database analysis report |
| `db_analysis.md` | `reports/` | Database analysis |
| `db_table_register.md` | `reports/` | Database table register |
| `isolation_forest_results.md` | `reports/` | Isolation Forest results |
| `missing_db_info.md` | `reports/` | Missing database info |

---

## Analysis Documents

System behavior analysis and pattern recognition.

| File | Location | Description | Last Updated |
|------|----------|-------------|--------------|
| `МАТРИЦА_ПРИЗНАКОВ_И_ПОВЕДЕНИЕ_СИСТЕМЫ/` | Root | Feature matrix and system behavior analysis | 2025-01-07 |

---

## Process Documents

Documentation about documentation and project processes.

| File | Location | Description | Last Updated |
|------|----------|-------------|--------------|
| `MAINTENANCE_PROCEDURE.md` | `docs/` | Documentation maintenance procedure | 2025-01-07 |
| `DOC_INDEX.md` | `docs/` | This index | 2025-01-07 |

---

## Quick Reference

### By Category

| Category | Count | Location |
|----------|-------|----------|
| Status | 4 | Root |
| Architecture | 5 | Root + `docs/` |
| Technical | 4 | Root |
| Data | 4 | `docs/data_*/` |
| Tasks | 17 | Root |
| QA | 7 | `docs/qa/` + `reports/` |
| Analysis | 1+ | Root |
| Process | 2 | `docs/` |
| **Total** | **44+** | |

### Finding a Document

1. Check this index for the document name
2. Navigate to the location column for the path
3. If not found, check `task_register.md` for task-related docs
4. If still not found, search with: `find . -name "*.md" | grep -i "<keyword>"`

### Adding a New Document

1. Create the document in the appropriate location
2. Follow the naming convention (lowercase, underscores)
3. Add a "Last updated" field at the top
4. Add the document to this index
5. Update `scripts/update_docs.sh` if adding a new category

---

*This index is maintained by the Docs Maintainer. Update it whenever documents are added, removed, or relocated.*
