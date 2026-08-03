# ДИАГРАММА ГАНТА — UNIFIED ETL

**Обновлено:** 2026-08-03 14:15

```mermaid
gantt
    title Unified ETL AX 2012/WMS — актуальный план 03.08–09.08.2026
    dateFormat YYYY-MM-DD
    axisFormat %d.%m

    section Completed in code
    Merge purchase_order PR #11              :done, po_pr, 2026-08-03, 1d
    True full_table strategy                  :done, full_table, 2026-08-03, 1d
    Composite upsert support                  :done, composite, 2026-08-03, 1d
    Mandatory preflight for full/resume       :done, mandatory_pre, 2026-08-03, 1d
    Add sales_order run45 diagnostics         :done, so_diag_code, 2026-08-03, 1d

    section 03.08 Baseline and sales_order
    Run current pytest baseline               :crit, tests_now, 2026-08-03, 1d
    Run read-only run45 diagnostics           :crit, so_diag, 2026-08-03, 1d
    Determine resume or restart strategy      :so_decision, after so_diag, 1d

    section 04.08 purchase_order runtime
    Apply or verify composite unique key      :crit, po_key, 2026-08-04, 1d
    Run purchase_order preflight              :crit, po_pre, 2026-08-04, 1d
    Full load and validate-only               :po_load, after po_pre, 1d
    Verify repeat-run idempotency              :po_repeat, after po_load, 1d

    section 05.08 READY stages
    Validate picking_route                    :pick_val, 2026-08-05, 1d
    Reconcile picking_route RAW and DDS        :pick_rec, 2026-08-05, 1d
    Validate pack_task                        :pack_val, 2026-08-05, 1d
    Reconcile pack_task RAW and DDS            :pack_rec, 2026-08-05, 1d

    section 06.08 order_trans
    Inspect actual RAW columns                :crit, ot_cols, 2026-08-06, 1d
    Fix mappings                              :crit, ot_map, 2026-08-06, 1d
    Select indexed chunk strategy             :crit, ot_strategy, 2026-08-06, 1d
    Preflight or approve technical plan       :ot_pre, after ot_strategy, 1d

    section 07.08 ALK_MARKSERIAL
    Fix normalization preflight contract      :crit, norm_contract, 2026-08-07, 1d
    Compare source key staging and CTAS       :crit, norm_compare, 2026-08-07, 1d
    Estimate disk WAL rollback and resume     :crit, norm_risk, 2026-08-07, 1d
    Approve benchmark 100k 250k 500k 1M       :bench, 2026-08-07, 1d

    section 08.08-09.08 Weekly gate
    Full unit and regression test run         :crit, gate_tests, 2026-08-08, 1d
    Full read-only preflight                  :crit, gate_pre, 2026-08-08, 1d
    ETL status and reconciliation             :gate_status, 2026-08-09, 1d
    Update status documents                   :gate_docs, 2026-08-09, 1d
```

## Текущий readiness

```text
READY FOR RUNTIME CHECK:
- purchase_order

READY / VALIDATION REQUIRED:
- picking_route
- pack_task

READY WITH INVESTIGATION REQUIRED:
- sales_order

BLOCKED:
- order_trans
- serial_mark_normalization
- serial_mark
```

## Фактически завершено

```text
purchase_order code fix merged
→ full_table corrected
→ composite conflict key supported
→ ON CONFLICT DO UPDATE supported
→ mandatory preflight added
→ regression tests added

sales_order run45 diagnostic toolkit added
```

Это завершение разработки, а не подтверждение production/runtime загрузки.

## Критический путь

```text
current pytest baseline
→ sales_order run45 diagnosis
→ purchase_order runtime validation
→ picking_route and pack_task validation
→ order_trans indexed design
→ serial_mark architecture decision
→ weekly gate
```

## Условия перехода

- `purchase_order` считается закрытым только после `preflight = READY`, нового `COMPLETED` run и repeat-run проверки;
- `sales_order` нельзя возобновлять до разбора run 45;
- `order_trans` не запускается при Seq Scan или неиндексируемом chunk key;
- `serial_mark` не изменяет RAW массовым UPDATE;
- blocked stage запрещено запускать в `full`;
- любой изменяющий запуск предваряется проверкой диска, WAL, activity, vacuum, index progress и locks.
