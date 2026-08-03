# ДИАГРАММА ГАНТА — UNIFIED ETL

**Обновлено:** 2026-08-03 16:02

```mermaid
gantt
    title Unified ETL AX 2012/WMS — план 03.08–09.08.2026
    dateFormat YYYY-MM-DD
    axisFormat %d.%m

    section Completed runtime
    Full read-only preflight                 :done, full_pre, 2026-08-03, 1d
    purchase_order full run 66               :done, po66, 2026-08-03, 1d
    purchase_order CLI validate run 67       :done, po67, 2026-08-03, 1d
    purchase_order repeat full run 68        :done, po68, 2026-08-03, 1d
    Diagnose sales_order run 45              :done, so45, 2026-08-03, 1d
    Verify sales_order preflight batch 100k  :done, so_pre100, 2026-08-03, 1d
    Prepare stage execution checkpoint       :done, checkpoint, 2026-08-03, 1d

    section 03.08 Current work
    Reconcile purchase_order runs 66-68      :crit, po_rec, 2026-08-03, 1d
    Run sales_order full batch 100k          :crit, so_full, 2026-08-03, 1d
    Validate sales_order                     :so_val, after so_full, 1d
    Reconcile sales_order RAW and DDS        :so_rec, after so_val, 1d

    section 04.08 READY stages
    Validate picking_route                   :pick_val, 2026-08-04, 1d
    Reconcile picking_route RAW and DDS       :pick_rec, 2026-08-04, 1d
    Validate pack_task                       :pack_val, 2026-08-04, 1d
    Reconcile pack_task RAW and DDS           :pack_rec, 2026-08-04, 1d

    section 05.08-06.08 order_trans
    Inspect actual RAW columns               :crit, ot_cols, 2026-08-05, 1d
    Fix invalid mappings                     :crit, ot_map, 2026-08-05, 1d
    Select indexed chunk strategy            :crit, ot_strategy, 2026-08-06, 1d
    Run read-only preflight                  :ot_pre, after ot_strategy, 1d

    section 07.08 ALK_MARKSERIAL
    Fix normalization preflight contract     :crit, norm_contract, 2026-08-07, 1d
    Compare source key staging and CTAS      :crit, norm_compare, 2026-08-07, 1d
    Estimate disk WAL rollback and resume    :crit, norm_risk, 2026-08-07, 1d
    Approve benchmark 100k 250k 500k 1M      :bench, 2026-08-07, 1d

    section 08.08-09.08 Weekly gate
    Full unit and regression test run        :crit, gate_tests, 2026-08-08, 1d
    Repeat full read-only preflight          :crit, gate_pre, 2026-08-08, 1d
    ETL status and reconciliation            :gate_status, 2026-08-09, 1d
    Summarize WAL history                    :gate_wal, 2026-08-09, 1d
    Update status documents                  :gate_docs, 2026-08-09, 1d
```

## Текущий readiness

```text
RUNTIME COMPLETED:
- purchase_order

READY FOR NEW FULL RUN:
- sales_order

READY FOR VALIDATION:
- picking_route
- pack_task

BLOCKED:
- order_trans
- serial_mark_normalization
- serial_mark
```

## Подтверждённые факты

```text
purchase_order:
- run 66 completed
- run 67 completed under validate-only CLI label but used runtime path
- run 68 repeat full completed
- final reconciliation remains

sales_order:
- run 45 failed before chunks
- cause: missing PipelineSpec.chunk_strategy in old code path
- data impact: none
- resume is not required
- batch 100k preflight READY_WITH_WARNINGS
- Bitmap Heap Scan uses Bitmap Index Scan

implementation checkpoint:
- DB execution pending on Windows host
- validate-only fixed to a real read-only path
- reconciliation SQL and guarded PowerShell runner prepared
- order_trans and serial_mark staging mappings prepared
```

## Критический путь

```text
purchase_order reconciliation
→ sales_order full 100k
→ sales_order validation
→ picking_route and pack_task validation
→ order_trans indexed design
→ serial_mark normalization contract
→ serial_mark architecture decision
→ weekly gate
```

## Условия перехода

- `purchase_order` закрывается после точной reconciliation runs 66–68;
- `sales_order` запускается новым `full`, а не `resume` run 45;
- `picking_route` и `pack_task` требуют validate-only и reconciliation;
- `order_trans` не запускается до исправления mapping и индексируемого chunk key;
- `serial_mark_normalization` не запускается до полноценного preflight;
- `serial_mark` не изменяет RAW массовым UPDATE;
- любой изменяющий запуск предваряется проверкой disk, WAL, activity, vacuum, index progress и locks.
