-- ============================================================
-- Запрос 3. Поиск связей таблиц (по именам колонок)
-- ============================================================
-- Назначение: Найти потенциальные связи между таблицами
--              по совпадению имён колонок (FK-ограничений нет в D365/AX)
-- Источник: Анализ проекта Anomaly Detection
-- Дата: 2026-07-08
-- Примечание: Запрос 03_find_relations (FK) вернул пустой результат —
--              связи в D365/AX определяются на уровне X++, не SQL.
--              Данный запрос использует эвристику по именам колонок.
-- ============================================================

-- === Часть 1: Поиск колонок с типичными именами ключей ===

SELECT
    c1.TABLE_NAME AS TableA,
    c1.COLUMN_NAME AS ColumnA,
    c2.TABLE_NAME AS TableB,
    c2.COLUMN_NAME AS ColumnB
FROM INFORMATION_SCHEMA.COLUMNS c1
JOIN INFORMATION_SCHEMA.COLUMNS c2
    ON c1.COLUMN_NAME = c2.COLUMN_NAME
    AND c1.TABLE_NAME <> c2.TABLE_NAME
WHERE c1.COLUMN_NAME IN (
    'ItemId',
    'SerialNumber',
    'ItemSerialNumber',
    'RequestId',
    'SalesId',
    'PickingRouteId',
    'TaskId',
    'LFL_SCSPackTaskId',
    'InventDimId',
    'InventLocationId',
    'MPProductGroup',
    'FEACCId',
    'EmplId',
    'PickingLineBuffer',
    'MD5Hash',
    'StatusExt',
    'RecId'
)
AND c1.TABLE_NAME IN (
    'LFL_MarkingCodeTable', 'WMS_PickDiffActLine', 'WMS_JournalWarehouseOperationTable',
    'ALK_MarkSerial', 'LFL_PickingLineBufferMarking', 'MPProductGroupFEACCMarkDates',
    'MPProductGroup', 'LFL_RequestTable', 'WMSPickingRoute', 'LFL_SCSPackTask',
    'InventTable', 'InventDim', 'PurchTable', 'SalesTable',
    'WMS_PickingLineBuffer', 'WMSOrderTrans', 'LFL_SSCCTable', 'InventLocation',
    'FEACCInventTable_RU', 'LFL_RequestTableES', 'SalesParameters',
    'InventItemBarcode', 'WMSPalletType', 'WMS_WMSJournalHandler'
)
ORDER BY c1.COLUMN_NAME, c1.TABLE_NAME;

-- === Часть 2: Поиск FK-колонок по паттерну (*Id, *RefId) ===

SELECT
    c.TABLE_NAME,
    c.COLUMN_NAME,
    c.DATA_TYPE,
    c.CHARACTER_MAXIMUM_LENGTH
FROM INFORMATION_SCHEMA.COLUMNS c
WHERE c.COLUMN_NAME LIKE '%Id'
   OR c.COLUMN_NAME LIKE '%RefId'
   OR c.COLUMN_NAME LIKE '%TypeId'
   OR c.COLUMN_NAME LIKE '%Status'
   OR c.COLUMN_NAME LIKE '%Key'
ORDER BY c.TABLE_NAME, c.COLUMN_NAME;

-- === Часть 3: Проверка наличия индексов ===
-- (совместимо с SQL Server 2012+, без STRING_AGG)

SELECT
    OBJECT_NAME(i.object_id) AS TableName,
    i.name AS IndexName,
    i.type_desc AS IndexType,
    STUFF((
        SELECT ', ' + COL_NAME(ic2.object_id, ic2.column_id)
        FROM sys.index_columns ic2
        WHERE ic2.object_id = i.object_id AND ic2.index_id = i.index_id
        ORDER BY ic2.key_ordinal
        FOR XML PATH('')
    ), 1, 2, '') AS IndexColumns
FROM sys.indexes i
WHERE OBJECT_NAME(i.object_id) IN (
    'LFL_MarkingCodeTable', 'WMS_PickDiffActLine', 'WMS_JournalWarehouseOperationTable',
    'ALK_MarkSerial', 'LFL_PickingLineBufferMarking', 'MPProductGroupFEACCMarkDates',
    'MPProductGroup', 'LFL_RequestTable', 'WMSPickingRoute', 'LFL_SCSPackTask',
    'InventTable', 'InventDim', 'PurchTable', 'SalesTable',
    'WMS_PickingLineBuffer', 'WMSOrderTrans', 'LFL_SSCCTable', 'InventLocation',
    'FEACCInventTable_RU', 'LFL_RequestTableES', 'SalesParameters',
    'InventItemBarcode', 'WMSPalletType', 'WMS_WMSJournalHandler'
)
AND i.name IS NOT NULL
ORDER BY OBJECT_NAME(i.object_id), i.name;
