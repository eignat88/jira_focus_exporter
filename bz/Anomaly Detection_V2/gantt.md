# ДИАГРАММА ГАНТА — UNIFIED ETL

**Обновлено:** 2026-08-03 14:25

```mermaid
gantt
    title Unified ETL AX 2012/WMS — план 03.08–09.08.2026
    dateFormat YYYY-MM-DD
    axisFormat %d.%m

    section Completed baseline
    Merge purchase_order PR #11              :done, po_pr, 2026-08-03, 1d
    Run full read-only preflight              :done, full_pre, 2026-08-03, 1d
    purchase_order preflight READY            :done, po_ready, 2026-08-03, 1d
    picking_route preflight READY             :done, pick_ready, 2026-08-03, 1d
    pack_task preflight READY                 :done, pack_ready, 2026-08-03, 1d
    sales_order READY WITH WARNINGS           :done, sales_ready, 2026-08-03, 1d

    section 03.08 Runtime start
    Start WAL monitor                         :crit, wal_start, 2026-08-03, 1d
    Run purchase_order full                   :crit, po_full, 2026-08-03, 1d
    Validate purchase_order                   :po_validate, after po_full, 1d
    Verify purchase_order repeat run          :po_repeat, after po_validate, 1d
    Run sales_order run45 diagnostics         :crit, sales_diag, 2026-08-03, 1d

    section 04.08 READY stages
    Validate picking_route                    :pick_val, 2026-08-04, 1d
    Reconcile picking_route RAW and DDS        :pick_rec, 2026-08-04, 1d
    Validate pack_task                        :pack_val, 2026-08-04, 1d
    Reconcile pack_task RAW and DDS            :pack_rec, 2026-08-04, 1d

    section 05.08-06.08 order_trans
    Inspect actual RAW columns                :crit, ot_cols, 2026-08-05, 1d
    Fix three invalid mappings                :crit, ot_map, 2026-08-05, 1d
    Select indexed chunk strategy             :crit, ot_strategy, 2026-08-06, 1d
    Run read-only preflight                   :ot_pre, after ot_strategy, 1d

    section 07.08 ALK_MARKSERIAL
    Fix normalization preflight contract      :crit, norm_contract, 2026-08-07, 1d
    Compare source key staging and CTAS       :crit, norm_compare, 2026-08-07, 1d
    Estimate disk WAL rollback and resume     :crit, norm_risk, 2026-08-07, 1d
    Approve benchmark 100k 250k 500k 1M       :bench, 2026-08-07, 1d

    section 08.08-09.08 Weekly gate
    Full unit and regression test run         :crit, gate_tests, 2026-08-08, 1d
    Repeat full read-only preflight           :crit, gate_pre, 2026-08-08, 1d
    ETL status and reconciliation             :gate_status, 2026-08-09, 1d
    Summarize WAL history                     :gate_wal, 2026-08-09, 1d
    Update status documents                   :gate_docs, 2026-08-09, 1d
```

## Текущий readiness

```text
READY:
- purchase_order
- picking_route
- pack_task

READY_WITH_WARNINGS:
- sales_order

BLOCKED:
- order_trans
- serial_mark_normalization
- serial_mark
```

## Фактические блокеры

```text
order_trans:
- recid text incompatible with numeric_range
- missing ordertransid
- missing pickedqty
- missing wastedqty
- missing recid_bigint index
- EXPLAIN references nonexistent recid_bigint

serial_mark_normalization:
- no columns mapping for current preflight contract

serial_mark:
- recid text incompatible with numeric_range
- no recid_bigint B-tree
- blocking Seq Scan on about 153.2M rows / 78.8 GB
- WAL risk HIGH
```

## Критический путь

```text
purchase_order runtime completion
→ sales_order run45 diagnosis
→ picking_route and pack_task validation
→ order_trans indexed design
→ serial_mark normalization contract
→ serial_mark architecture decision
→ weekly gate
```

## Условия перехода

- `purchase_order` закрывается только после COMPLETED run, validate-only и repeat-run проверки;
- `sales_order` не возобновляется до разбора run 45 и Bitmap Heap Scan;
- `picking_route` и `pack_task` требуют reconciliation, поскольку preflight использует оценочные counts;
- `order_trans` не запускается до исправления mapping и индексируемого chunk key;
- `serial_mark_normalization` не запускается до полноценного preflight;
- `serial_mark` не изменяет RAW массовым UPDATE;
- любой изменяющий запуск предваряется проверкой disk, WAL, activity, vacuum, index progress и locks.
