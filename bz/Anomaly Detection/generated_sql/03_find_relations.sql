-- ============================================================
-- Запрос 3. Поиск связей таблиц (внешние ключи)
-- ============================================================
-- Назначение: Определить связи между таблицами через FK
-- Источник: Анализ проекта Anomaly Detection
-- Дата: 2026-07-08
-- ============================================================

SELECT
    fk.name AS FK_Name,
    OBJECT_NAME(fk.parent_object_id) AS ChildTable,
    COL_NAME(fc.parent_object_id, fc.parent_column_id) AS ChildField,
    OBJECT_NAME(fk.referenced_object_id) AS ParentTable,
    COL_NAME(fc.referenced_object_id, fc.referenced_column_id) AS ParentField
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fc
    ON fk.object_id = fc.constraint_object_id
WHERE OBJECT_NAME(fk.parent_object_id) IN (
    -- Основные таблицы
    'LFL_MarkingCodeTable',
    'WMS_PickDiffActLine',
    'WMS_JournalWarehouseOperationTable',
    'ALK_MarkSerial',
    'LFL_PickingLineBufferMarking',
    'MPProductGroupFEACCMarkDates',
    'MPProductGroup',
    'LFL_RequestTable',
    'WMSPickingRoute',
    'LFL_SCSPackTask',

    -- Вспомогательные таблицы
    'InventTable',
    'InventDim',
    'PurchTable',
    'SalesTable',
    'WMS_PickingLineBuffer',
    'WMSOrderTrans',
    'LFL_SSCCTable',
    'InventLocation',
    'FEACCInventTable_RU',
    'LFL_RequestTableES',
    'SalesParameters',
    'InventItemBarcode',
    'WMSPalletType',
    'WMS_WMSJournalHandler',
    'LFL_ParmReceiptMarkingCode',
    'LFL_MarkingCodeAggregationSSCC2',
    'LFL_ApproveRequest',
    'LFL_MsgReqDirectProcessing_MarkSerial',
    'FEACCTable_RU'
)
OR OBJECT_NAME(fk.referenced_object_id) IN (
    'LFL_MarkingCodeTable',
    'WMS_PickDiffActLine',
    'WMS_JournalWarehouseOperationTable',
    'ALK_MarkSerial',
    'LFL_PickingLineBufferMarking',
    'MPProductGroupFEACCMarkDates',
    'MPProductGroup',
    'LFL_RequestTable',
    'WMSPickingRoute',
    'LFL_SCSPackTask',
    'InventTable',
    'InventDim',
    'PurchTable',
    'SalesTable',
    'WMS_PickingLineBuffer',
    'WMSOrderTrans',
    'LFL_SSCCTable',
    'InventLocation',
    'FEACCInventTable_RU',
    'LFL_RequestTableES',
    'SalesParameters',
    'InventItemBarcode',
    'WMSPalletType',
    'WMS_WMSJournalHandler',
    'LFL_ParmReceiptMarkingCode',
    'LFL_MarkingCodeAggregationSSCC2',
    'LFL_ApproveRequest',
    'LFL_MsgReqDirectProcessing_MarkSerial',
    'FEACCTable_RU'
)
ORDER BY
    OBJECT_NAME(fk.parent_object_id),
    fk.name;
