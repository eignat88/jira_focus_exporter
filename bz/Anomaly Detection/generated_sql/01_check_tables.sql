-- ============================================================
-- Запрос 1. Проверка существования таблиц
-- ============================================================
-- Назначение: Определить, какие из требуемых таблиц существуют в БД
-- Источник: Анализ проекта Anomaly Detection
-- Дата: 2026-07-08
-- ============================================================

SELECT
    TABLE_SCHEMA,
    TABLE_NAME,
    TABLE_TYPE
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_NAME IN (
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
ORDER BY TABLE_NAME;
