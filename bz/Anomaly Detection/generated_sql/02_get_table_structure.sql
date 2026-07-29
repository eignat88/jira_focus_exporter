-- ============================================================
-- Запрос 2. Получение структуры таблиц
-- ============================================================
-- Назначение: Получить полную структуру всех найденных таблиц
-- Источник: Анализ проекта Anomaly Detection
-- Дата: 2026-07-08
-- ============================================================

SELECT
    c.TABLE_SCHEMA,
    c.TABLE_NAME,
    c.ORDINAL_POSITION AS FieldNo,
    c.COLUMN_NAME AS FieldName,
    c.DATA_TYPE AS DataType,
    c.CHARACTER_MAXIMUM_LENGTH AS MaxLength,
    c.NUMERIC_PRECISION AS NumericPrecision,
    c.NUMERIC_SCALE AS NumericScale,
    c.IS_NULLABLE AS IsNullable,
    c.COLUMN_DEFAULT AS DefaultValue
FROM INFORMATION_SCHEMA.COLUMNS c
WHERE c.TABLE_NAME IN (
    -- Основные таблицы (Anomaly Detection)
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

    -- Справочные и вспомогательные таблицы
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
    'MPApiEngine',
    'ALK_InventLocation_SkipKM',
    'FEACCTable_RU',

    -- Кастомные таблицы мониторинга
    'ALK_WMS_MetricsTable',
    'ALK_WMS_MetricsAlertsTable'
)
ORDER BY
    c.TABLE_NAME,
    c.ORDINAL_POSITION;
