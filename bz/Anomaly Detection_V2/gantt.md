# ДИАГРАММА ГАНТА — UNIFIED ETL

**Обновлено:** 2026-07-31

```mermaid
gantt
    title Unified ETL AX 2012/WMS — актуальный план
    dateFormat YYYY-MM-DD
    axisFormat %d.%m

    section Реализовано
    Unified ETL core                 :done, core, 2026-07-13, 8d
    Resume, heartbeat, advisory lock :done, resume, 2026-07-16, 5d
    WAL monitoring                   :done, wal, 2026-07-21, 1d
    RAW to DDS configuration         :done, cfg, 2026-07-22, 8d
    Sales order diagnostics          :done, salesdiag, 2026-07-30, 2d
    Full-table preflight fix PR 9    :done, pr9, 2026-07-31, 1d
    Chunk strategy propagation PR 10 :done, pr10, 2026-07-31, 1d

    section Стабилизация
    Full unit test run               :active, tests, 2026-07-31, 2d
    Separate integration tests       :crit, inttests, after tests, 2d
    Validate canonical YAML          :crit, yaml, 2026-08-01, 2d

    section Preflight
    purchase_order preflight         :crit, po_pre, 2026-08-01, 1d
    sales_order preflight            :so_pre, after po_pre, 1d
    picking_route preflight          :pick_pre, after so_pre, 1d
    pack_task preflight              :pack_pre, after pick_pre, 1d
    order_trans preflight            :order_pre, after pack_pre, 1d
    serial_mark preflight            :crit, serial_pre, after order_pre, 1d

    section DDS загрузка
    purchase_order load and validate :po_load, after po_pre, 1d
    sales_order load and validate    :so_load, after so_pre, 2d
    picking_route load and validate  :pick_load, after pick_pre, 2d
    pack_task load and validate      :pack_load, after pack_pre, 3d
    order_trans load and validate    :order_load, after order_pre, 5d

    section ALK_MARKSERIAL
    Select normalization approach    :crit, norm_decision, after serial_pre, 2d
    Build normalized staging/key     :crit, norm_build, after norm_decision, 5d
    Benchmark 100k-1M batches        :bench, after norm_build, 3d
    Full serial_mark load            :crit, serial_load, after bench, 14d
    Resume and failure tests         :crit, resume_test, after serial_load, 3d
    RAW-DDS reconciliation           :crit, reconcile, after resume_test, 3d

    section MART и ML
    Rebuild MART on validated DDS    :mart, after reconcile, 4d
    Validate features                :features, after mart, 3d
    Re-evaluate anomaly models       :ml, after features, 5d
```

## Критический путь

```text
Тесты → preflight → исправление blockers → нормализация ALK_MARKSERIAL
→ benchmark → полная загрузка dds.serial_mark → resume-тесты
→ сверка RAW/DDS → MART → ML
```

## Условия перехода между этапами

- к изменяющему запуску переходить только после успешного read-only preflight;
- загрузку следующего stage начинать после валидации conflict key и результата предыдущего;
- тяжёлую загрузку `serial_mark` запускать только после проверки диска, WAL, активных backend-процессов и индексов;
- MART и ML не считать актуальными до подтверждения полноты DDS.
