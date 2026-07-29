-- ============================================================================
-- МЕТРИКИ МОНИТОРИНГА ФУНКЦИОНАЛА КМ/ШК В WMS
-- Источник: Анализ 12 XPO-файлов SharedProject ALK_WMS
-- Дата создания: 2026-07-07
-- ============================================================================

-- ============================================================================
-- 1. ЕЖЕДНЕВНАЯ СТАТИСТИКА ПО ОШИБКАМ КМ
-- ============================================================================

-- 1.1. Количество ошибок по типам за последние 7 дней
SELECT 
    CAST(journal.StartDate AS DATE) AS dt,
    CASE 
        WHEN journal.OperationType = 'LFL_SCSCargoPackageRepacked' THEN 'Упаковка'
        WHEN journal.OperationType = 'LFL_SCSCargoPackageRepackedIM' THEN 'Упаковка ИМ'
        WHEN journal.OperationType = 'LFL_Aggregate' THEN 'Агрегация'
        WHEN journal.OperationType = 'CheckedQty' THEN 'Контроль'
        WHEN journal.OperationType = 'LFL_AddToSSCC2' THEN 'Агрегация SSCC'
        ELSE CAST(journal.OperationType AS VARCHAR(50))
    END AS operation_name,
    COUNT(*) AS operation_count,
    SUM(journal.Qty) AS total_qty,
    SUM(journal.Lines) AS total_lines
FROM WMS_JournalWarehouseOperationTable journal
WHERE journal.StartDate >= DATEADD(day, -7, GETDATE())
GROUP BY 
    CAST(journal.StartDate AS DATE),
    CASE 
        WHEN journal.OperationType = 'LFL_SCSCargoPackageRepacked' THEN 'Упаковка'
        WHEN journal.OperationType = 'LFL_SCSCargoPackageRepackedIM' THEN 'Упаковка ИМ'
        WHEN journal.OperationType = 'LFL_Aggregate' THEN 'Агрегация'
        WHEN journal.OperationType = 'CheckedQty' THEN 'Контроль'
        WHEN journal.OperationType = 'LFL_AddToSSCC2' THEN 'Агрегация SSCC'
        ELSE CAST(journal.OperationType AS VARCHAR(50))
    END
ORDER BY dt DESC, operation_count DESC;

-- 1.2. Проблемные КМ по заданиям на упаковку
SELECT 
    diffAct.LFL_SCSPackTaskId AS task_id,
    diffAct.ActType AS act_type,
    COUNT(*) AS problem_count,
    SUM(diffAct.DiffQtyForPick) AS problem_qty,
    MIN(diffAct.CreatedDateTime) AS first_problem,
    MAX(diffAct.CreatedDateTime) AS last_problem
FROM WMS_PickDiffActLine diffAct
WHERE diffAct.ActType = 'ProblemKM'
    AND diffAct.CreatedDateTime >= DATEADD(day, -7, GETDATE())
GROUP BY diffAct.LFL_SCSPackTaskId, diffAct.ActType
ORDER BY problem_qty DESC;

-- ============================================================================
-- 2. АНАЛИЗ НАСТРОЕК
-- ============================================================================

-- 2.1. Товарные группы с ALK_ScanMC=Да (обязательное КМ)
SELECT 
    tg.GroupId,
    tg.Description,
    tg.ALK_ScanMC,
    tg.ALK_MPCisTemplate,
    CASE 
        WHEN tg.ALK_MPCisTemplate IS NULL THEN 'НЕ НАСТРОЕН'
        ELSE 'НАСТРОЕН'
    END AS template_status
FROM MPProductGroup tg
WHERE tg.ALK_ScanMC = 1  -- NoYes::Yes
ORDER BY tg.GroupId;

-- 2.2. Настройки EnableWithoutMC (допуск ОСУ без КМ)
SELECT 
    tg.GroupId AS product_group,
    tg.Description,
    dates.FEACCId_RU,
    dates.EnableWithoutMC,
    dates.InternalForbidenTurnoverDate,
    dates.MandatoryStartDate,
    CASE 
        WHEN dates.InternalForbidenTurnoverDate <= GETDATE() THEN 'СРОК ИСТЁК'
        WHEN dates.InternalForbidenTurnoverDate <= DATEADD(day, 30, GETDATE() THEN 'ИСТЕКАЕТ ЧЕРЕЗ 30 ДНЕЙ'
        ELSE 'АКТИВНО'
    END AS status,
    CASE 
        WHEN dates.EnableWithoutMC = 1 
            AND dates.InternalForbidenTurnoverDate <= GETDATE() 
        THEN 'ТРЕБУЕТСЯ ОТКЛЮЧЕНИЕ'
        ELSE 'ОК'
    END AS action_required
FROM MPProductGroup tg
JOIN MPProductGroupFEACCMarkDates dates 
    ON dates.MPProductGroup = tg.RecId
WHERE dates.EnableWithoutMC = 1
ORDER BY dates.InternalForbidenTurnoverDate;

-- 2.3. Заявки с IsMarkingCodeSign=Да (маркированная продукция)
SELECT 
    req.RequestId,
    req.IsMarkingCodeSign,
    req.OurShop,
    req.ServMAGNITKI,
    purch.PurchId,
    purch.ALK_IsMarkingCodeSign,
    req.CreatedDateTime
FROM LFL_RequestTable req
LEFT JOIN PurchTable purch 
    ON purch.LFL_RequestId = req.RequestId
WHERE req.IsMarkingCodeSign = 1
    AND req.CreatedDateTime >= DATEADD(day, -30, GETDATE())
ORDER BY req.CreatedDateTime DESC;

-- ============================================================================
-- 3. СТАТИСТИКА ПО КОНКУРЕНЦИИ MP
-- ============================================================================

-- 3.1. Конкурентные КМ (Competitor=Да)
SELECT 
    mc.ItemId,
    it.ItemName,
    COUNT(*) AS competitor_count,
    MIN(mc.CreatedDateTime) AS first_seen,
    MAX(mc.CreatedDateTime) AS last_seen
FROM LFL_MarkingCodeTable mc
JOIN InventTable it ON it.ItemId = mc.ItemId
WHERE mc.Competitor = 1  -- NoYes::Yes
    AND mc.CreatedDateTime >= DATEADD(day, -30, GETDATE())
GROUP BY mc.ItemId, it.ItemName
ORDER BY competitor_count DESC;

-- 3.2. Распределение竞争对手 по заявкам
SELECT 
    req.RequestId,
    req.OurShop,
    COUNT(mc.RecId) AS total_km,
    SUM(CASE WHEN mc.Competitor = 1 THEN 1 ELSE 0 END) AS competitor_km,
    CAST(SUM(CASE WHEN mc.Competitor = 1 THEN 1 ELSE 0 END) AS FLOAT) / 
        NULLIF(COUNT(mc.RecId), 0) * 100 AS competitor_pct
FROM LFL_RequestTable req
JOIN LFL_MarkingCodeTable mc ON mc.RequestId = req.RequestId
WHERE req.CreatedDateTime >= DATEADD(day, -30, GETDATE())
GROUP BY req.RequestId, req.OurShop
HAVING SUM(CASE WHEN mc.Competitor = 1 THEN 1 ELSE 0 END) > 0
ORDER BY competitor_pct DESC;

-- ============================================================================
-- 4. СТАТУСЫ ГИС МТ (ALK_MarkSerial)
-- ============================================================================

-- 4.1. Распределение статусов КМ
SELECT 
    ms.StatusExt AS status,
    COUNT(*) AS km_count,
    MIN(ms.CreatedDateTime) AS earliest,
    MAX(ms.CreatedDateTime) AS latest
FROM ALK_MarkSerial ms
WHERE ms.CreatedDateTime >= DATEADD(day, -7, GETDATE())
GROUP BY ms.StatusExt
ORDER BY km_count DESC;

-- 4.2. КМ с недопустимыми статусами (не INTRODUCED)
SELECT 
    mc.ItemId,
    mc.ItemSerialNumber,
    ms.StatusExt,
    mc.Blocked,
    mc.Accepted,
    mc.Shipped,
    mc.CreatedDateTime
FROM LFL_MarkingCodeTable mc
JOIN ALK_MarkSerial ms 
    ON ms.ItemId = mc.ItemId 
    AND ms.SerialNumber = mc.ItemSerialNumber
WHERE ms.StatusExt != 'INTRODUCED'  -- Статус != Введён в оборот
    AND mc.Blocked = 0
    AND mc.CreatedDateTime >= DATEADD(day, -7, GETDATE())
ORDER BY mc.CreatedDateTime DESC;

-- ============================================================================
-- 5. ЭФФЕКТИВНОСТЬ РАБОТЫ СОТРУДНИКОВ
-- ============================================================================

-- 5.1. Среднее время обработки задания
SELECT 
    task.PackWorker AS worker_id,
    COUNT(*) AS tasks_completed,
    AVG(DATEDIFF(minute, task.PackStartDateTime, task.PackFinishDateTime)) AS avg_minutes,
    MIN(DATEDIFF(minute, task.PackStartDateTime, task.PackFinishDateTime)) AS min_minutes,
    MAX(DATEDIFF(minute, task.PackStartDateTime, task.PackFinishDateTime)) AS max_minutes
FROM LFL_SCSPackTask task
WHERE task.PackStatus = 2  -- Finished
    AND task.PackFinishDateTime >= DATEADD(day, -7, GETDATE())
GROUP BY task.PackWorker
ORDER BY avg_minutes;

-- 5.2. Количество проблемных КМ по сотрудникам
SELECT 
    task.PackWorker AS worker_id,
    COUNT(DISTINCT diffAct.LFL_SCSPackTaskId) AS tasks_with_problems,
    SUM(diffAct.DiffQtyForPick) AS total_problem_qty
FROM WMS_PickDiffActLine diffAct
JOIN LFL_SCSPackTask task ON task.TaskId = diffAct.LFL_SCSPackTaskId
WHERE diffAct.ActType = 'ProblemKM'
    AND diffAct.CreatedDateTime >= DATEADD(day, -7, GETDATE())
GROUP BY task.PackWorker
ORDER BY total_problem_qty DESC;

-- ============================================================================
-- 6. ПРОИЗВОДИТЕЛЬНОСТЬ ЗАПРОСОВ
-- ============================================================================

-- 6.1. Индексы для оптимизации (проверка наличия)
SELECT 
    OBJECT_NAME(i.object_id) AS table_name,
    i.name AS index_name,
    i.type_desc,
    STRING_AGG(c.name, ', ') AS columns
FROM sys.indexes i
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE OBJECT_NAME(i.object_id) IN (
    'LFL_MarkingCodeTable',
    'WMS_PickDiffActLine',
    'WMS_JournalWarehouseOperationTable',
    'LFL_PickingLineBufferMarking'
)
GROUP BY i.object_id, i.name, i.type_desc
ORDER BY table_name, index_name;

-- 6.2. Статистика использования индексов
SELECT 
    OBJECT_NAME(s.object_id) AS table_name,
    i.name AS index_name,
    s.user_seeks,
    s.user_scans,
    s.user_lookups,
    s.user_updates
FROM sys.dm_db_index_usage_stats s
JOIN sys.indexes i ON i.object_id = s.object_id AND i.index_id = s.index_id
WHERE OBJECT_NAME(s.object_id) IN (
    'LFL_MarkingCodeTable',
    'WMS_PickDiffActLine',
    'LFL_PickingLineBufferMarking'
)
ORDER BY s.user_seeks + s.user_scans DESC;

-- ============================================================================
-- 7. АЛЕРТЫ (для пакетной обработки)
-- ============================================================================

-- 7.1. EnableWithoutMC истекает через 7 дней
SELECT 
    tg.GroupId,
    dates.FEACCId_RU,
    dates.InternalForbidenTurnoverDate,
    DATEDIFF(day, GETDATE(), dates.InternalForbidenTurnoverDate) AS days_remaining
FROM MPProductGroupFEACCMarkDates dates
JOIN MPProductGroup tg ON tg.RecId = dates.MPProductGroup
WHERE dates.EnableWithoutMC = 1
    AND dates.InternalForbidenTurnoverDate BETWEEN GETDATE() AND DATEADD(day, 7, GETDATE())
ORDER BY dates.InternalForbidenTurnoverDate;

-- 7.2. Заблокированные КМ с активными заданиями
SELECT 
    mc.ItemId,
    mc.ItemSerialNumber,
    mc.Blocked,
    plr.WMS_PickingRouteID AS active_route
FROM LFL_MarkingCodeTable mc
JOIN LFL_PickingLineBufferMarking plb 
    ON plb.ItemId = mc.ItemId 
    AND plb.ItemSerialNumber = mc.ItemSerialNumber
JOIN WMS_PickingLineBuffer plr 
    ON plr.RecId = plb.PickingLineBufferRecId
WHERE mc.Blocked = 1
    AND plr.PickedAndWasteQty < plr.Qty  -- Незавершённое задание
ORDER BY mc.ItemId;

-- 7.3. Конкуренция MP: товары с >3 конкурентами
SELECT 
    mc.ItemId,
    it.ItemName,
    COUNT(DISTINCT mc.ExtOwnerName) AS owner_count
FROM LFL_MarkingCodeTable mc
JOIN InventTable it ON it.ItemId = mc.ItemId
WHERE mc.Competitor = 1
    AND mc.CreatedDateTime >= DATEADD(day, -30, GETDATE())
GROUP BY mc.ItemId, it.ItemName
HAVING COUNT(DISTINCT mc.ExtOwnerName) > 3
ORDER BY owner_count DESC;
