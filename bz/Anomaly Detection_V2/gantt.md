# ДИАГРАММА ГАНТА — UNIFIED ETL

**Обновлено:** 2026-07-31 19:24

```mermaid
gantt
    title Unified ETL AX 2012/WMS — план 01.08–07.08.2026
    dateFormat YYYY-MM-DD
    axisFormat %d.%m

    section Runtime baseline
    Pytest 111 passed / 3 failed          :done, baseline_tests, 2026-07-31, 1d
    Full RAW to DDS preflight             :done, baseline_pre, 2026-07-31, 1d
    Pipeline status snapshot              :done, baseline_status, 2026-07-31, 1d

    section 01.08 Tests and failed runs
    Fix RetryPolicy jitter test           :crit, retry_test, 2026-08-01, 1d
    Mark SQL Server tests integration     :crit, int_mark, 2026-08-01, 1d
    Replace bool returns with asserts     :warn_fix, 2026-08-01, 1d
    Diagnose runs 38 and 45               :crit, failed_runs, 2026-08-01, 1d

    section 02.08 purchase_order
    Inspect raw purchtable columns        :crit, po_cols, 2026-08-02, 1d
    Fix full_table preflight              :crit, po_pre_fix, 2026-08-02, 1d
    Fix purchase_order mappings           :crit, po_map, 2026-08-02, 1d
    Load and validate purchase_order      :po_load, after po_pre_fix, 1d

    section 03.08 sales_order
    Diagnose failed run 45                :crit, so_fail, 2026-08-03, 1d
    Compare batch 100k and 250k plans     :so_plan, 2026-08-03, 1d
    Resume or restart sales_order         :so_run, after so_fail, 1d
    Reconcile sales_order                 :so_val, after so_run, 1d

    section 04.08 READY stages
    Validate picking_route                :pick_val, 2026-08-04, 1d
    Explain RAW/DDS count difference      :pick_diff, 2026-08-04, 1d
    Validate pack_task                    :pack_val, 2026-08-04, 1d
    Check duplicate and conflict behavior :pack_diff, 2026-08-04, 1d

    section 05.08 order_trans
    Inspect actual RAW columns            :crit, ot_cols, 2026-08-05, 1d
    Fix order_trans mappings              :crit, ot_map, 2026-08-05, 1d
    Select indexed chunk strategy         :crit, ot_strategy, 2026-08-05, 1d
    Preflight order_trans                 :ot_pre, after ot_strategy, 1d

    section 06.08 ALK_MARKSERIAL
    Fix normalization preflight contract  :crit, norm_pre, 2026-08-06, 1d
    Compare source key staging and CTAS   :crit, norm_compare, 2026-08-06, 1d
    Estimate disk WAL rollback and resume :crit, norm_risk, 2026-08-06, 1d
    Approve benchmark plan 100k to 1M     :bench_plan, 2026-08-06, 1d

    section 07.08 Weekly gate
    Full unit test run                    :crit, gate_tests, 2026-08-07, 1d
    Separate integration test result      :gate_int, 2026-08-07, 1d
    Full preflight and status             :crit, gate_pre, 2026-08-07, 1d
    Update project documents              :gate_docs, 2026-08-07, 1d
```

## Текущий readiness

```text
READY:               picking_route, pack_task
READY_WITH_WARNINGS: sales_order
BLOCKED:             purchase_order, order_trans,
                     serial_mark_normalization, serial_mark
```

## Критический путь недели

```text
pytest stabilization
→ purchase_order READY
→ sales_order recovery
→ validation of picking_route and pack_task
→ order_trans indexed design
→ serial_mark architecture decision
→ full weekly gate
```

## Условия перехода

- blocked stage запрещено запускать в `full`;
- `sales_order` нельзя повторно запускать до разбора run 45;
- `purchase_order` запускается только после исчезновения обращения к `recid_bigint` и исправления mappings;
- `order_trans` не переводится в загрузку при Seq Scan или отсутствующем chunk key;
- `serial_mark` не изменяет RAW массовым UPDATE;
- любой изменяющий запуск предваряется проверкой диска, WAL, activity, vacuum, index progress и locks.
