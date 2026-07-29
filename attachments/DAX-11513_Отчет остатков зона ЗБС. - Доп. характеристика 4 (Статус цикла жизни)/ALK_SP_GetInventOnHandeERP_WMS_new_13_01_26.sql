--USE [AX63_WMS_PROD]
--GO
--/****** Object:  StoredProcedure [dbo].[ALK_SP_GetInventOnHandeERP_WMS_new]    Script Date: 23.12.2025 13:43:10 ******/
--SET ANSI_NULLS ON
--GO
--SET QUOTED_IDENTIFIER ON
--GO

--ALTER PROCEDURE [dbo].[ALK_SP_GetInventOnHandeERP_WMS_new]
--AS
--BEGIN
	SET NOCOUNT ON;
	SET DEADLOCK_PRIORITY LOW;

	DECLARE @RETRY_COUNT_CURRENT INT  = 0
	DECLARE @RETRY_COUNT_MAXIMUM INT  = 3
	DECLARE @ERROR_NUM INT			  = 0
	DECLARE @ERROR_MSG NVARCHAR(MAX)

	WHILE @RETRY_COUNT_CURRENT < @RETRY_COUNT_MAXIMUM
	BEGIN
		BEGIN TRY
			--BEGIN TRANSACTION;  

				DECLARE @ExDesc25ID_Ecom   nvarchar(100) = N'Ядро e-comm'
				DECLARE @ExDesc25ID_EcomMP nvarchar(100) = N'Ядро ecomm МП'  --N'Ядро e-comm МП'
				DECLARE @ExDesc25ID_NS     nvarchar(100) = N'Not_Sales'
				-- все что не попало в предыдущие категории идет как NDef 
		
				---свободные остатки ЗБС----------------------------------------------------------------------------
				DROP TABLE IF EXISTS #tmp_Import_WMS
				--создаем временную таблицу для импорта
				CREATE TABLE #tmp_Import_WMS(
					[ITEMID] [nvarchar](13) PRIMARY KEY,
					[QTY] [numeric](32, 16) NULL,
					[from_lsn] binary(10) NOT NULL,
					[to_lsn] binary(10) NOT NULL
				)

				INSERT INTO #tmp_Import_WMS
				(	
					[ITEMID],
					[QTY],
					[from_lsn],
					[to_lsn]
				)
				select '', 0, 0x0, 0x0
				--  !!!!Отключен забор данных нет прав на хранимку у моей учетки !!!!EXEC [AX63_WMS_PROD].[dbo].[ALK_SP_GetInventOnHandeDif] 0

				--INSERT INTO [dbo].[BO_log] ([Message], [Error],	[LogTime])
				--SELECT N'Загружено в #tmp_Import_WMS из ХП [dbo].[ALK_SP_GetInventOnHandeDif] строк ' + CAST(@@ROWCOUNT AS nvarchar(100)) , 0, GETDATE();

				-- свободные остатки ERP ИМ-------------------------------------------------------------------------
				DROP TABLE IF EXISTS #T

				CREATE TABLE #T ( ITEMID			 nvarchar(13) NOT NULL
								 ,AvailPhysicalERP   numeric(32, 16)
								 ,PhysicalInvent	 numeric(32, 16)
								 ,ReservPhysical	 numeric(32, 16)
								)

				INSERT INTO #T (ITEMID, AvailPhysicalERP, PhysicalInvent, ReservPhysical)
				EXEC [sax-db].[ALK12_ZBS].[dbo].[ALK_SP_GetInventOnHandByIMChannel]

				--INSERT INTO [dbo].[BO_log] ([Message], [Error],	[LogTime])
				--SELECT N'Загружено в #T из ERP [dbo].[ALK_SP_GetInventOnHandByIMChannel] строк ' + CAST(@@ROWCOUNT AS nvarchar(100)) , 0, GETDATE();

				--!! select * from #t
				--свободные остатки ERP все-------------------------------------------------------------------------
				DROP TABLE IF EXISTS #ERP_ALL

				SELECT inventSum.itemId, sum(inventSum.AvailPhysical) AS AvailPhysical
				INTO #ERP_ALL
				FROM [sax-db].[ALK12_ZBS].[dbo].inventSum
				JOIN [sax-db].[ALK12_ZBS].[dbo].inventDim ON INVENTDIM.PARTITION = inventSum.PARTITION
					AND inventDim.inventDimId = inventSum.InventDimId
				JOIN [sax-db].[ALK12_ZBS].[dbo].DKL_InventTable ON DKL_InventTable.PARTITION = inventSum.PARTITION
					AND DKL_InventTable.ITEMID = inventSum.ITEMID
				WHERE 
					inventDim.InventLocationId = N'Лк_Пд_Цс'
					AND inventSum.CLOSED = 0
					AND DKL_InventTable.INVENTSTATUS = 1 --Активна
				GROUP BY inventSum.ItemId
				HAVING sum(inventSum.AvailPhysical) > 0  -- fix 23.01.24 былло PhysicalInvent

				--INSERT INTO [dbo].[BO_log] ([Message], [Error],	[LogTime])
				--SELECT N'Загружено в #ERP_ALL из ERP [dbo].[inventSum] строк ' + CAST(@@ROWCOUNT AS nvarchar(100)) , 0, GETDATE();

				--!! select * from #ERP_ALL
				--остатки витрины-----------------------------------------------------------------------------------
				DROP TABLE IF EXISTS #MSSN_ost;
				SELECT [ITEMID], [QTY]
				INTO #MSSN_ost
				FROM [sax-db].[MSSN].[dbo].[ShowCaseCalcView] sc
				WHERE INVENTLOCATIONID = N'ЛК_Пд_ЦС'
					AND qty != 0

				--INSERT INTO [dbo].[BO_log] ([Message], [Error],	[LogTime])
				--SELECT N'Загружено в #MSSN_ost из витрины [dbo].[ShowCaseCalcView] строк ' + CAST(@@ROWCOUNT AS nvarchar(100)) , 0, GETDATE();

				--!! select * from #MSSN_ost
				-- свободные остатки WMS----------------------------------------------------------------------------
				DROP TABLE IF EXISTS #R
				CREATE TABLE #R ( ITEMID nvarchar(13) NOT NULL,
									QTY    numeric(32, 16)
								)

				INSERT INTO #R (ITEMID, QTY)
				SELECT invSum.ITEMID AS ITEMID, sum(invSum.AvailPhysical) AS QTY
				from [AX63_WMS_PROD].[dbo].[InventSum] invSum
							join [AX63_WMS_PROD].[dbo].[inventDim] invDim on invDim.inventDimId   = invSum.InventDimId
							and invDim.DATAAREAID  = invSum.DATAAREAID
							and invDim.PARTITION  = invsum.PARTITION
							and invDim.INVENTLOCATIONID = N'ЛК_Пд_ЦС'
							join [AX63_WMS_PROD].[dbo].[wMSLocation] locId on locId.inventLocationId = invDim.InventLocationId 
							and locId.wMSLocationId = invDim.wMSLocationId
							and invDim.DATAAREAID  = locId.DATAAREAID
							and invDim.PARTITION  = locId.PARTITION
							join [AX63_WMS_PROD].[dbo].[wMSStoreArea] storeArea on storeArea.storeAreaId = locId.storeAreaId
							and storeArea.DATAAREAID  = locId.DATAAREAID
							and storeArea.PARTITION  = locId.PARTITION
							--and (storeArea.LFL_WarehouseStockOnlineStore = 1 OR storeArea.LFL_SCSStockOnlineStore = 1) --как в PBD
					where invSum.closed = 0
						and invSum.AvailPhysical > 0
						and locId.outputBlockingCauseId = '' --без блокировованных ячеек
						and not(locId.WMSLOCATIONID = N'BF_PR_OUT' AND locId.INVENTLOCATIONID = N'ЛК_ПД_ЦС') -- нужно не выбирать остатки по ячейке «BF_PR_OUT»
						and invSum.DATAAREAID = N'dat'
						and invSum.partition = 5637144576
						and (
							locId.storeAreaId not in (
								SELECT LFL_InboundReceiveBufferStoreArea FROM WMSStoreZone
									where  WMSStoreZone.storeZoneId = (SELECT TOP 1 LFL_ReceiveSortByPieceStoreZoneId  FROM [AX63_WMS_PROD].[dbo].[WMS_WMSAdvancedParameters])
										or WMSStoreZone.storeZoneId = (SELECT TOP 1 LFL_ReceiveSortByPalletStoreZoneId FROM [AX63_WMS_PROD].[dbo].[WMS_WMSAdvancedParameters])
										or WMSStoreZone.storeZoneId = (SELECT TOP 1 LFL_ReceiveSortByBoxStoreZoneId    FROM [AX63_WMS_PROD].[dbo].[WMS_WMSAdvancedParameters])
							)
						and  locId.storeAreaId not in (
							SELECT storeZoneId FROM WMSStoreZone
								where  WMSStoreZone.storeZoneId = (SELECT TOP 1 LFL_ReceiveSortByPieceStoreZoneId  FROM [AX63_WMS_PROD].[dbo].[WMS_WMSAdvancedParameters])
									or WMSStoreZone.storeZoneId = (SELECT TOP 1 LFL_ReceiveSortByPalletStoreZoneId FROM [AX63_WMS_PROD].[dbo].[WMS_WMSAdvancedParameters])
									or WMSStoreZone.storeZoneId = (SELECT TOP 1 LFL_ReceiveSortByBoxStoreZoneId    FROM [AX63_WMS_PROD].[dbo].[WMS_WMSAdvancedParameters])
							)
						)
					group by invSum.ITEMID

				INSERT INTO #R (ITEMID, QTY)
				SELECT T.ITEMID, SUM(T.QTY) 
				FROM AX63_WMS_PROD..InventTrans T WITH (NOLOCK)
				WHERE T.PARTITION = 5637144576 AND T.DATAAREAID = 'dat'
					AND T.StatusIssue in (6)
					AND EXISTS (SELECT * 
								FROM AX63_WMS_PROD..InventTransOrigin O WITH (NOLOCK) 
									INNER JOIN AX63_WMS_PROD..SalesLine L WITH (NOLOCK) ON L.DATAAREAID = O.DATAAREAID AND L.PARTITION = O.PARTITION AND L.InventTransId = O.InventTransId
									INNER JOIN AX63_WMS_PROD..SalesTable S WITH (NOLOCK) ON S.DATAAREAID = L.DATAAREAID AND S.PARTITION = L.PARTITION AND S.SalesId = L.SalesId
								WHERE O.RecId = T.INVENTTRANSORIGIN AND S.SALESSTATUS in (1, 5) AND L.SALESSTATUS in (1, 5))
				GROUP BY T.ITEMID

				DROP TABLE IF EXISTS #R_SUM
				SELECT ITEMID, SUM(QTY) QTY
				INTO #R_SUM
				FROM #R
				GROUP BY ITEMID
				HAVING SUM(QTY) > 0

				--INSERT INTO [dbo].[BO_log] ([Message], [Error],	[LogTime])
				--SELECT N'Загружено в #R_SUM из WMS [dbo].[InventSum] строк ' + CAST(@@ROWCOUNT AS nvarchar(100)) , 0, GETDATE();

				--!! select * from #R_SUM
				-- свободные остатки РС001----------------------------------------------------------------------------
				DROP TABLE IF EXISTS #ostPC
				CREATE TABLE #ostPC ( ITEMID nvarchar(13) NOT NULL,
									  QTY    numeric(32, 16)
									)

				INSERT INTO #ostPC (ITEMID, QTY)
				SELECT invSum.ITEMID AS ITEMID, sum(invSum.AvailPhysical) AS QTY
				from [AX63_WMS_PROD].[dbo].[InventSum] invSum
							join [AX63_WMS_PROD].[dbo].[inventDim] invDim on invDim.inventDimId   = invSum.InventDimId
							and invDim.DATAAREAID  = invSum.DATAAREAID
							and invDim.PARTITION  = invsum.PARTITION
							and invDim.INVENTLOCATIONID = N'РС001'
					where invSum.closed = 0
						and invSum.AvailPhysical != 0
						and invSum.DATAAREAID = N'dat'
						and invSum.partition = 5637144576
					group by invSum.ITEMID

				--INSERT INTO [dbo].[BO_log] ([Message], [Error],	[LogTime])
				--SELECT N'Загружено в #ostPC из WMS [dbo].[InventSum] строк ' + CAST(@@ROWCOUNT AS nvarchar(100)) , 0, GETDATE();

				--!! select * from #ostPC
				-- свободные остатки ЛК_Пд_ЦС----------------------------------------------------------------------------
				DROP TABLE IF EXISTS #ostLCPDCS
				CREATE TABLE #ostLCPDCS ( ITEMID nvarchar(13) NOT NULL,
									  QTY    numeric(32, 16)
									)

				INSERT INTO #ostLCPDCS (ITEMID, QTY)
				SELECT invSum.ITEMID AS ITEMID, sum(invSum.AvailPhysical) AS QTY
				from [AX63_WMS_PROD].[dbo].[InventSum] invSum
							join [AX63_WMS_PROD].[dbo].[inventDim] invDim on invDim.inventDimId   = invSum.InventDimId
							and invDim.DATAAREAID  = invSum.DATAAREAID
							and invDim.PARTITION  = invsum.PARTITION
							and invDim.INVENTLOCATIONID = N'ЛК_Пд_ЦС'
					where invSum.closed = 0
						and invSum.AvailPhysical != 0
						and invSum.DATAAREAID = N'dat'
						and invSum.partition = 5637144576
					group by invSum.ITEMID

				--INSERT INTO [dbo].[BO_log] ([Message], [Error],	[LogTime])
				--SELECT N'Загружено в #ostLCPDCS из WMS [dbo].[InventSum] строк ' + CAST(@@ROWCOUNT AS nvarchar(100)) , 0, GETDATE();

				--!! select * from #ostLCPDCS
				--данные из SQL04 - WMS ЗБС по областям---------------------------------------------------------------
				DROP TABLE IF EXISTS #SQL_EXCH

				SELECT 
					   [AvailPhysical]
					  ,[AvailPhysicalSCS]
					  ,[ItemId]
				  INTO #SQL_EXCH
				  FROM [SQL04.ALKOR.RU].[Exchange_DAX_WMS].[dbo].[INT_InventOnHandEShop]
				  where [ExchangeSessionId] = (SELECT MAX([ExchangeSessionId]) FROM [SQL04.ALKOR.RU].[Exchange_DAX_WMS].[dbo].[INT_InventOnHandEShop])

				--INSERT INTO [dbo].[BO_log] ([Message], [Error],	[LogTime])
				--SELECT N'Загружено в #SQL_EXCH из [SQL04.ALKOR.RU].[Exchange_DAX_WMS].[dbo].[INT_InventOnHandEShop] строк ' + CAST(@@ROWCOUNT AS nvarchar(100)) , 0, GETDATE();

				--!! select * from #SQL_EXCH
				--данные АВС классификатора--------------------------------------------------------------------------
				DROP TABLE IF EXISTS #ABC

				SELECT 
					 [itemId]
					,[ReplenishmentStrategy]
				INTO #ABC
				FROM LFL_SCSSALESFORECASTLINES
				where [Session_Id] = (SELECT MAX([Session_Id]) FROM LFL_SCSSALESFORECASTLINES)

				--INSERT INTO [dbo].[BO_log] ([Message], [Error],	[LogTime])
				--SELECT N'Загружено в #ABC из WMS LFL_SCSSALESFORECASTLINES строк ' + CAST(@@ROWCOUNT AS nvarchar(100)) , 0, GETDATE();

				--!! select * from #ABC
				--InventTable ERP -------------------------------------------------------------------------
				DROP TABLE IF EXISTS #ERP_InventTable

				SELECT it.itemId
					  ,CASE WHEN it.ALK_ItemSignID = 99 AND it.ALK_ItemCommersion = 0 AND (it.ALK_ItemCategoryId <> N'Комплектующие' OR it.ALK_ItemCategoryId <> N'РЕКЛАМНАЯ ПРОДУКЦИЯ') THEN 1 ELSE 0 END AS SignItem
					  ,it.ALK_EXTRADESC25ID AS EXTRADESC25ID
					  ,it.ALK_ExtraDesc4Id AS ExtraDesc4Id
				INTO #ERP_InventTable
				FROM [sax-db].[ALK12_ZBS].[dbo].InventTable it

				--INSERT INTO [dbo].[BO_log] ([Message], [Error],	[LogTime])
				--SELECT N'Загружено в #ERP_InventTable из ERP [dbo].InventTable строк ' + CAST(@@ROWCOUNT AS nvarchar(100)) , 0, GETDATE();

				--!! select * from #ERP_InventTable
				--справочник товаров для вывода--------------------------------------------------------------------
				DROP TABLE IF EXISTS #item

				SELECT	 COALESCE(#T.ITEMID, wms.ItemId, #R_SUM.ItemId, #ERP_ALL.ItemId, #MSSN_ost.ItemId, #SQL_EXCH.ItemId, #ostPC.ItemId, #ostLCPDCS.itemId) AS ItemId
				INTO #item
				FROM #T
				FULL OUTER JOIN #tmp_Import_WMS wms		ON wms.ItemId		= #T.ItemId
				FULL OUTER JOIN #R_SUM					ON #R_SUM.ItemId	= #T.ItemId
				FULL OUTER JOIN #ERP_ALL				ON #ERP_ALL.ItemId	= #T.ItemId
				FULL OUTER JOIN #MSSN_ost				ON #MSSN_ost.ItemId = #T.ItemId
				FULL OUTER JOIN #SQL_EXCH				ON #SQL_EXCH.ItemId = #T.ItemId
				FULL OUTER JOIN #ostPC					ON #ostPC.ItemId	= #T.ItemId
				FULL OUTER JOIN #ostLCPDCS				ON #ostLCPDCS.itemId = #T.ItemId
				GROUP BY COALESCE(#T.ITEMID, wms.ItemId, #R_SUM.ItemId, #ERP_ALL.ItemId, #MSSN_ost.ItemId, #SQL_EXCH.ItemId, #ostPC.ItemId, #ostLCPDCS.itemId)

				--INSERT INTO [dbo].[BO_log] ([Message], [Error],	[LogTime])
				--SELECT N'Загружено в #item строк ' + CAST(@@ROWCOUNT AS nvarchar(100)) , 0, GETDATE();

				--!! select * from #item
				--ассортимент ИМ -----------------------------------------------------------------------------------
				DROP TABLE IF EXISTS #assortIM
				SELECT DISTINCT	item.ITEMID
				INTO #assortIM
				FROM 
				(SELECT [ITEMID] AS ITEMID FROM [sax-db].[ALK12_ZBS].[dbo].[ALK_ESHOPASSORTMENT] where [ALK_ESHOPASSORTMENT].SHOWCASEID = 'Letu_ru'
				UNION 
				SELECT [ITEMID] AS ITEMID FROM [sax-db].[ALK12_ZBS].[dbo].[ALK_MARKETPLACEASSORTMENT] where [ALK_MARKETPLACEASSORTMENT].SHOWCASEID = 'Letu_ru'
				) item

				--INSERT INTO [dbo].[BO_log] ([Message], [Error],	[LogTime])
				--SELECT N'Загружено в #assortIM из ERP строк ' + CAST(@@ROWCOUNT AS nvarchar(100)) , 0, GETDATE();

				--!! select * from #assortIM
				--вставить данные в базу Otchety--------------------------------------------------------------------
				DECLARE @countERP int = (SELECT count(*) FROM #T WHERE #T.AvailPhysicalERP > 0)
				DECLARE @countERP_Ecom int = (SELECT count(*) FROM #T WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_Ecom))
				DECLARE @countERP_EcomMP int = (SELECT count(*) FROM #T WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_EcomMP))
				DECLARE @countERP_NS int = (SELECT count(*) FROM #T WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_NS))
				DECLARE @countERP_EcomPA_MP int = COALESCE((SELECT count(*) FROM #T WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID 
							IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID = @ExDesc25ID_Ecom OR EXTRADESC25ID = @ExDesc25ID_EcomMP))), 0)
				DECLARE @countERP_NDef int = COALESCE((SELECT count(*) FROM #T WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID 
							IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID NOT IN(@ExDesc25ID_Ecom, @ExDesc25ID_EcomMP, @ExDesc25ID_NS)))), 0)

				DECLARE @countWMS0andInERP int =  (SELECT count(*) FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID 	WHERE #T.AvailPhysicalERP > 0 and (#tmp_Import_WMS.QTY = 0 OR #tmp_Import_WMS.ITEMID  IS NULL))
				DECLARE @countWMS0andInERP_Ecom int =  (SELECT count(*) FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID 	WHERE #T.AvailPhysicalERP > 0 and (#tmp_Import_WMS.QTY = 0 OR #tmp_Import_WMS.ITEMID  IS NULL) AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_Ecom))
				DECLARE @countWMS0andInERP_EcomMP int =  (SELECT count(*) FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID 	WHERE #T.AvailPhysicalERP > 0 and (#tmp_Import_WMS.QTY = 0 OR #tmp_Import_WMS.ITEMID  IS NULL) AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_EcomMP))
				DECLARE @countWMS0andInERP_NS int =  (SELECT count(*) FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID 	WHERE #T.AvailPhysicalERP > 0 and (#tmp_Import_WMS.QTY = 0 OR #tmp_Import_WMS.ITEMID  IS NULL) AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_NS))
				DECLARE @countWMS0andInERP_EcomPA_MP int =  COALESCE((SELECT count(*) FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID 	WHERE #T.AvailPhysicalERP > 0 and (#tmp_Import_WMS.QTY = 0 OR #tmp_Import_WMS.ITEMID  IS NULL) AND #T.ITEMID 
							IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID = @ExDesc25ID_Ecom OR EXTRADESC25ID = @ExDesc25ID_EcomMP))), 0)
				DECLARE @countWMS0andInERP_NDef int =  COALESCE((SELECT count(*) FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID 	WHERE #T.AvailPhysicalERP > 0 and (#tmp_Import_WMS.QTY = 0 OR #tmp_Import_WMS.ITEMID  IS NULL) AND #T.ITEMID 
							IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID NOT IN(@ExDesc25ID_Ecom, @ExDesc25ID_EcomMP, @ExDesc25ID_NS)))), 0)

				DECLARE @percentCountWMS0andInERPCountERP numeric (32,16) = 0
				DECLARE @percentCountWMS0andInERPCountERP_Ecom numeric (32,16) = 0
				DECLARE @percentCountWMS0andInERPCountERP_EcomMP numeric (32,16) = 0
				DECLARE @percentCountWMS0andInERPCountERP_NS numeric (32,16) = 0
				DECLARE @percentCountWMS0andInERPCountERP_EcomPA_MP numeric (32,16) = 0
				DECLARE @percentCountWMS0andInERPCountERP_NDef numeric (32,16) = 0

				DECLARE @countWMS int = (SELECT count(*) FROM #T JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID	WHERE #T.AvailPhysicalERP > 0 and #tmp_Import_WMS.QTY > 0);
				DECLARE @countWMS_Ecom int = (SELECT count(*) FROM #T JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID	WHERE #T.AvailPhysicalERP > 0 and #tmp_Import_WMS.QTY > 0 AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_Ecom));
				DECLARE @countWMS_EcomMP int = (SELECT count(*) FROM #T JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID	WHERE #T.AvailPhysicalERP > 0 and #tmp_Import_WMS.QTY > 0 AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_EcomMP));
				DECLARE @countWMS_NS int = (SELECT count(*) FROM #T JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID	WHERE #T.AvailPhysicalERP > 0 and #tmp_Import_WMS.QTY > 0 AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_NS));
				DECLARE @countWMS_EcomPA_MP int = COALESCE((SELECT count(*) FROM #T JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID	WHERE #T.AvailPhysicalERP > 0 and #tmp_Import_WMS.QTY > 0 AND #T.ITEMID 
							IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID = @ExDesc25ID_Ecom OR EXTRADESC25ID = @ExDesc25ID_EcomMP))), 0);
				DECLARE @countWMS_NDef int = COALESCE((SELECT count(*) FROM #T JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID	WHERE #T.AvailPhysicalERP > 0 and #tmp_Import_WMS.QTY > 0 AND #T.ITEMID 
							IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID NOT IN(@ExDesc25ID_Ecom, @ExDesc25ID_EcomMP, @ExDesc25ID_NS)))), 0)

				DECLARE @sumWMS int = (SELECT SUM(#tmp_Import_WMS.QTY) FROM #T JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID WHERE #T.AvailPhysicalERP > 0);
				DECLARE @sumWMS_Ecom int = (SELECT SUM(#tmp_Import_WMS.QTY) FROM #T JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_Ecom));
				DECLARE @sumWMS_EcomMP int = (SELECT SUM(#tmp_Import_WMS.QTY) FROM #T JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_EcomMP));
				DECLARE @sumWMS_NS int = (SELECT SUM(#tmp_Import_WMS.QTY) FROM #T JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_NS));
				DECLARE @sumWMS_EcomPA_MP int = COALESCE((SELECT SUM(#tmp_Import_WMS.QTY) FROM #T JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID 
							IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID = @ExDesc25ID_Ecom OR EXTRADESC25ID = @ExDesc25ID_EcomMP))), 0);
				DECLARE @sumWMS_NDef int = COALESCE((SELECT SUM(#tmp_Import_WMS.QTY) FROM #T JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID 
							IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID NOT IN(@ExDesc25ID_Ecom, @ExDesc25ID_EcomMP, @ExDesc25ID_NS)))), 0)

				if(@countERP != 0)
					SET @percentCountWMS0andInERPCountERP = ROUND((cast((@countERP - @countWMS0andInERP) as numeric(32,16))) / @countERP * 100.0, 2)

				if(@countERP_Ecom != 0)
					SET @percentCountWMS0andInERPCountERP_Ecom = ROUND((cast((@countERP_Ecom - @countWMS0andInERP_Ecom) as numeric(32,16))) / @countERP_Ecom * 100.0, 2)

				if(@countERP_EcomMP != 0)
					SET @percentCountWMS0andInERPCountERP_EcomMP = ROUND((cast((@countERP_EcomMP - @countWMS0andInERP_EcomMP) as numeric(32,16))) / @countERP_EcomMP * 100.0, 2)

				if(@countERP_NS != 0)
					SET @percentCountWMS0andInERPCountERP_NS = ROUND((cast((@countERP_NS - @countWMS0andInERP_NS) as numeric(32,16))) / @countERP_NS * 100.0, 2)

				if(@countERP_EcomPA_MP != 0)
					SET @percentCountWMS0andInERPCountERP_EcomPA_MP = ROUND((cast((@countERP_EcomPA_MP - @countWMS0andInERP_EcomPA_MP) as numeric(32,16))) / @countERP_EcomPA_MP * 100.0, 2)

				if(@countERP_NDef != 0)
					SET @percentCountWMS0andInERPCountERP_NDef = ROUND((cast((@countERP_NDef - @countWMS0andInERP_NDef) as numeric(32,16))) / @countERP_NDef * 100.0, 2)

				DECLARE @sumERP numeric(32,16) = (SELECT SUM(AvailPhysicalERP) FROM #T)
				DECLARE @sumERP_Ecom numeric(32,16) = (SELECT SUM(AvailPhysicalERP) FROM #T WHERE ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_Ecom))
				DECLARE @sumERP_EcomMP numeric(32,16) = (SELECT SUM(AvailPhysicalERP) FROM #T WHERE ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_EcomMP))
				DECLARE @sumERP_NS numeric(32,16) = (SELECT SUM(AvailPhysicalERP) FROM #T WHERE ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_NS))
				DECLARE @sumERP_EcomPA_MP numeric(32,16) = COALESCE((SELECT SUM(AvailPhysicalERP) FROM #T WHERE ITEMID 
							IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID = @ExDesc25ID_Ecom OR EXTRADESC25ID = @ExDesc25ID_EcomMP))), 0)
				DECLARE @sumERP_NDef numeric(32,16) = COALESCE((SELECT SUM(AvailPhysicalERP) FROM #T WHERE ITEMID 
							IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID NOT IN(@ExDesc25ID_Ecom, @ExDesc25ID_EcomMP, @ExDesc25ID_NS)))), 0)
	
				DECLARE @sumWMS0andInERP numeric(32,16) =  (SELECT SUM(AvailPhysicalERP) FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID 	WHERE #T.AvailPhysicalERP > 0 and (#tmp_Import_WMS.QTY = 0 OR #tmp_Import_WMS.ITEMID  IS NULL))
				DECLARE @sumWMS0andInERP_Ecom numeric(32,16) =  (SELECT SUM(AvailPhysicalERP) FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID 	WHERE #T.AvailPhysicalERP > 0 and (#tmp_Import_WMS.QTY = 0 OR #tmp_Import_WMS.ITEMID  IS NULL) AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_Ecom))
				DECLARE @sumWMS0andInERP_EcomMP numeric(32,16) =  (SELECT SUM(AvailPhysicalERP) FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID 	WHERE #T.AvailPhysicalERP > 0 and (#tmp_Import_WMS.QTY = 0 OR #tmp_Import_WMS.ITEMID  IS NULL) AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_EcomMP))
				DECLARE @sumWMS0andInERP_NS numeric(32,16) =  (SELECT SUM(AvailPhysicalERP) FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID 	WHERE #T.AvailPhysicalERP > 0 and (#tmp_Import_WMS.QTY = 0 OR #tmp_Import_WMS.ITEMID  IS NULL) AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_NS))
				DECLARE @sumWMS0andInERP_EcomPA_MP numeric(32,16) =  COALESCE((SELECT SUM(AvailPhysicalERP) FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID 	WHERE #T.AvailPhysicalERP > 0 and (#tmp_Import_WMS.QTY = 0 OR #tmp_Import_WMS.ITEMID  IS NULL) AND #T.ITEMID 
							IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID = @ExDesc25ID_Ecom OR EXTRADESC25ID = @ExDesc25ID_EcomMP))), 0)
				DECLARE @sumWMS0andInERP_NDef numeric(32,16) =  COALESCE((SELECT SUM(AvailPhysicalERP) FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID 	WHERE #T.AvailPhysicalERP > 0 and (#tmp_Import_WMS.QTY = 0 OR #tmp_Import_WMS.ITEMID  IS NULL) AND #T.ITEMID 
							IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID NOT IN(@ExDesc25ID_Ecom, @ExDesc25ID_EcomMP, @ExDesc25ID_NS)))), 0)
	
				DECLARE @percentSumWMS0andInERPSumERP numeric (32,16) = 0
				DECLARE @percentSumWMS0andInERPSumERP_Ecom numeric (32,16) = 0
				DECLARE @percentSumWMS0andInERPSumERP_EcomMP numeric (32,16) = 0
				DECLARE @percentSumWMS0andInERPSumERP_NS numeric (32,16) = 0
				DECLARE @percentSumWMS0andInERPSumERP_EcomPA_MP numeric (32,16) = 0
				DECLARE @percentSumWMS0andInERPSumERP_NDef numeric (32,16) = 0

				IF(@countERP != 0)
					SET @percentSumWMS0andInERPSumERP = (SELECT sum(coalesce(#tmp_Import_WMS.QTY,0)/#T.AvailPhysicalERP)/@countERP*100.0 FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID WHERE #T.AvailPhysicalERP > 0)
				ELSE 
					SET @percentSumWMS0andInERPSumERP = 0;

				IF(@countERP_Ecom != 0)
					SET @percentSumWMS0andInERPSumERP_Ecom = (SELECT sum(coalesce(#tmp_Import_WMS.QTY,0)/#T.AvailPhysicalERP)/@countERP_Ecom*100.0 FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_Ecom))
				ELSE 
					SET @percentSumWMS0andInERPSumERP_Ecom = 0;

				IF(@countERP_EcomMP != 0)
					SET @percentSumWMS0andInERPSumERP_EcomMP = (SELECT sum(coalesce(#tmp_Import_WMS.QTY,0)/#T.AvailPhysicalERP)/@countERP_EcomMP*100.0 FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_EcomMP))
				ELSE 
					SET @percentSumWMS0andInERPSumERP_EcomMP = 0;

				IF(@countERP_NS != 0)
					SET @percentSumWMS0andInERPSumERP_NS = (SELECT sum(coalesce(#tmp_Import_WMS.QTY,0)/#T.AvailPhysicalERP)/@countERP_NS*100.0 FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_NS))
				ELSE 
					SET @percentSumWMS0andInERPSumERP_NS = 0;

				IF(@countERP_EcomPA_MP != 0)
					SET @percentSumWMS0andInERPSumERP_EcomPA_MP = (SELECT sum(coalesce(#tmp_Import_WMS.QTY,0)/#T.AvailPhysicalERP)/@countERP_EcomPA_MP*100.0 FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID 
							IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID = @ExDesc25ID_Ecom OR EXTRADESC25ID = @ExDesc25ID_EcomMP)))
				ELSE 
					SET @percentSumWMS0andInERPSumERP_EcomPA_MP = 0;

				IF(@countERP_NDef != 0)
					SET @percentSumWMS0andInERPSumERP_NDef = (SELECT sum(coalesce(#tmp_Import_WMS.QTY,0)/#T.AvailPhysicalERP)/@countERP_NDef*100.0 FROM #T LEFT OUTER JOIN #tmp_Import_WMS ON #tmp_Import_WMS.itemId = #T.itemID WHERE #T.AvailPhysicalERP > 0 AND #T.ITEMID 
							IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID NOT IN(@ExDesc25ID_Ecom, @ExDesc25ID_EcomMP, @ExDesc25ID_NS))))
				ELSE 
					SET @percentSumWMS0andInERPSumERP_NDef = 0;

				--INSERT INTO [Otchety].[dbo].[ALK_InventOnHandeERP_WMS]
				--(
			 --      [CreatedDT]
			 --     ,[SumERP]
			 --     ,[SumWMS]
			 --     ,[CountERP]
			 --     ,[CountWMS]
				--  ,[PercentCount]
				--  ,[PercentSum]
				--  ,[CountIM]
				--  ,[SumIM]
			 --     ,[SumERP_Ecom]
			 --     ,[SumWMS_Ecom]
			 --     ,[CountERP_Ecom]
			 --     ,[CountWMS_Ecom]
				--  ,[PercentCount_Ecom]
				--  ,[PercentSum_Ecom]
				--  ,[CountIM_Ecom]
				--  ,[SumIM_Ecom]
			 --     ,[SumERP_EcomMP]
			 --     ,[SumWMS_EcomMP]
			 --     ,[CountERP_EcomMP]
			 --     ,[CountWMS_EcomMP]
				--  ,[PercentCount_EcomMP]
				--  ,[PercentSum_EcomMP]
				--  ,[CountIM_EcomMP]
				--  ,[SumIM_EcomMP]
			 --     ,[SumERP_NS]
			 --     ,[SumWMS_NS]
			 --     ,[CountERP_NS]
			 --     ,[CountWMS_NS]
				--  ,[PercentCount_NS]
				--  ,[PercentSum_NS]
				--  ,[CountIM_NS]
				--  ,[SumIM_NS]
			 --     ,[SumERP_EcomPA_MP]
			 --     ,[SumWMS_EcomPA_MP]
			 --     ,[CountERP_EcomPA_MP]
			 --     ,[CountWMS_EcomPA_MP]
				--  ,[PercentCount_EcomPA_MP]
				--  ,[PercentSum_EcomPA_MP]
				--  ,[CountIM_EcomPA_MP]
				--  ,[SumIM_EcomPA_MP]
			 --     ,[SumERP_NDef]
			 --     ,[SumWMS_NDef]
			 --     ,[CountERP_NDef]
			 --     ,[CountWMS_NDef]
				--  ,[PercentCount_NDef]
				--  ,[PercentSum_NDef]
				--  ,[CountIM_NDef]
				--  ,[SumIM_NDef]
				--)
			 --	SELECT 
				--	 GETDATE()
				--	,@sumERP
				--	,@sumWMS
				--	,@countERP
				--	,@countWMS
				--	,@percentCountWMS0andInERPCountERP
				--	,@percentSumWMS0andInERPSumERP
				--	,COALESCE((SELECT COUNT(*) FROM #MSSN_ost WHERE #MSSN_ost.qty > 0), 0)
				--	,COALESCE((SELECT SUM(QTY) FROM #MSSN_ost WHERE #MSSN_ost.qty > 0), 0)
				--	,@sumERP_Ecom
				--	,@sumWMS_Ecom
				--	,@countERP_Ecom
				--	,@countWMS_Ecom
				--	,@percentCountWMS0andInERPCountERP_Ecom
				--	,@percentSumWMS0andInERPSumERP_Ecom
				--	,COALESCE((SELECT COUNT(*) FROM #MSSN_ost WHERE #MSSN_ost.qty > 0 AND #MSSN_ost.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_Ecom)), 0)
				--	,COALESCE((SELECT SUM(QTY) FROM #MSSN_ost WHERE #MSSN_ost.qty > 0 AND #MSSN_ost.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_Ecom)), 0)
				--	,@sumERP_EcomMP
				--	,@sumWMS_EcomMP
				--	,@countERP_EcomMP
				--	,@countWMS_EcomMP
				--	,@percentCountWMS0andInERPCountERP_EcomMP
				--	,@percentSumWMS0andInERPSumERP_EcomMP
				--	,COALESCE((SELECT COUNT(*) FROM #MSSN_ost WHERE #MSSN_ost.qty > 0 AND #MSSN_ost.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_EcomMP)), 0)
				--	,COALESCE((SELECT SUM(QTY) FROM #MSSN_ost WHERE #MSSN_ost.qty > 0 AND #MSSN_ost.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_EcomMP)), 0)
				--	,@sumERP_NS
				--	,@sumWMS_NS
				--	,@countERP_NS
				--	,@countWMS_NS
				--	,@percentCountWMS0andInERPCountERP_NS
				--	,@percentSumWMS0andInERPSumERP_NS
				--	,COALESCE((SELECT COUNT(*) FROM #MSSN_ost WHERE #MSSN_ost.qty > 0 AND #MSSN_ost.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_NS)), 0)
				--	,COALESCE((SELECT SUM(QTY) FROM #MSSN_ost WHERE #MSSN_ost.qty > 0 AND #MSSN_ost.ITEMID IN (SELECT ITEMID FROM #ERP_InventTable WHERE EXTRADESC25ID = @ExDesc25ID_NS)), 0)

				--	,@sumERP_EcomPA_MP
				--	,@sumWMS_EcomPA_MP
				--	,@countERP_EcomPA_MP
				--	,@countWMS_EcomPA_MP
				--	,@percentCountWMS0andInERPCountERP_EcomPA_MP
				--	,@percentSumWMS0andInERPSumERP_EcomPA_MP
				--	,COALESCE((SELECT COUNT(*) FROM #MSSN_ost WHERE #MSSN_ost.qty > 0 AND #MSSN_ost.ITEMID 
				--			IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID = @ExDesc25ID_Ecom OR EXTRADESC25ID = @ExDesc25ID_EcomMP))), 0)
				--	,COALESCE((SELECT SUM(QTY) FROM #MSSN_ost WHERE #MSSN_ost.qty > 0 AND #MSSN_ost.ITEMID 
				--			IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID = @ExDesc25ID_Ecom OR EXTRADESC25ID = @ExDesc25ID_EcomMP))), 0)


				--	,@sumERP_NDef
				--	,@sumWMS_NDef
				--	,@countERP_NDef
				--	,@countWMS_NDef
				--	,@percentCountWMS0andInERPCountERP_NDef
				--	,@percentSumWMS0andInERPSumERP_NDef
				--	,COALESCE((SELECT COUNT(*) FROM #MSSN_ost WHERE #MSSN_ost.qty > 0 AND #MSSN_ost.ITEMID 
				--			IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID NOT IN(@ExDesc25ID_Ecom, @ExDesc25ID_EcomMP, @ExDesc25ID_NS)))), 0)
				--	,COALESCE((SELECT SUM(QTY) FROM #MSSN_ost WHERE #MSSN_ost.qty > 0 AND #MSSN_ost.ITEMID 
				--			IN (SELECT ITEMID FROM #ERP_InventTable WHERE (EXTRADESC25ID NOT IN(@ExDesc25ID_Ecom, @ExDesc25ID_EcomMP, @ExDesc25ID_NS)))), 0)

				--INSERT INTO [dbo].[BO_log] ([Message], [Error],	[LogTime])
				--SELECT N'Выгружено в [Otchety].[dbo].[ALK_InventOnHandeERP_WMS] строк ' + CAST(@@ROWCOUNT AS nvarchar(100)) , 0, GETDATE();

				--вывод данных--------------------------------------------------------------------------------------
				SELECT	 #item.ItemId AS 'Ном-ра' 
						,CASE WHEN left(#item.ItemId, 3) = 'MPL' THEN 1 ELSE 0 END AS N'Маркеплейс'
						,COALESCE(CAST(#ERP_ALL.AvailPhysical AS int), 0) AS 'кол-во ERP-все'
						,COALESCE(CAST(#T.AvailPhysicalERP AS int), 0) AS 'кол-во ERP ИМ' 
						,COALESCE(CAST(#R_SUM.QTY AS int), 0) AS 'кол-во WMS-хранение'
						,COALESCE(CAST(wms.QTY AS int), 0) AS 'кол-во WMS-ЗБС (вход в хранение)'
						,COALESCE(CAST(#SQL_EXCH.[AvailPhysical] AS int), 0) AS 'ЗБС не SCS'
						,COALESCE(CAST(#SQL_EXCH.[AvailPhysicalSCS] AS int), 0) AS 'ЗБС SCS'
						,COALESCE(CAST(#MSSN_ost.qty AS int), 0) AS 'кол-во витрина'
						,it.UnitVolume AS N'Объем'
						,(it.NetWeight + it.TaraWeight) AS N'Вес'
						,CASE WHEN wms.QTY != 0 AND #T.AvailPhysicalERP != 0 THEN wms.QTY/(#T.AvailPhysicalERP/100) ELSE 0 END AS N'%Пополнения'
						,CASE 
							WHEN #ABC.REPLENISHMENTSTRATEGY IS NULL THEN N'НЕТ' 
							WHEN #ABC.REPLENISHMENTSTRATEGY = 0 THEN N'НЕТ' 
							WHEN #ABC.REPLENISHMENTSTRATEGY = 1 THEN N'A' 
							WHEN #ABC.REPLENISHMENTSTRATEGY = 2 THEN N'B' 
							WHEN #ABC.REPLENISHMENTSTRATEGY = 3 THEN N'C' 
							WHEN #ABC.REPLENISHMENTSTRATEGY = 4 THEN N'X' 
							WHEN #ABC.REPLENISHMENTSTRATEGY = 5 THEN N'Y' 
							WHEN #ABC.REPLENISHMENTSTRATEGY = 6 THEN N'Z' 
							WHEN #ABC.REPLENISHMENTSTRATEGY = 7 THEN N'AX' 
							WHEN #ABC.REPLENISHMENTSTRATEGY = 8 THEN N'AY' 
							WHEN #ABC.REPLENISHMENTSTRATEGY = 9 THEN N'AZ' 
							WHEN #ABC.REPLENISHMENTSTRATEGY = 10 THEN N'BX'
							WHEN #ABC.REPLENISHMENTSTRATEGY = 11 THEN N'BY'  
							WHEN #ABC.REPLENISHMENTSTRATEGY = 12 THEN N'BZ' 
							WHEN #ABC.REPLENISHMENTSTRATEGY = 13 THEN N'CX' 
							WHEN #ABC.REPLENISHMENTSTRATEGY = 14 THEN N'CY' 
							WHEN #ABC.REPLENISHMENTSTRATEGY = 15 THEN N'CZ' 
							ELSE 'НЕТ'
							END AS N'Стратегия'
						,CASE WHEN (((COALESCE(CAST(#ERP_ALL.AvailPhysical AS int), 0) + COALESCE(CAST(#T.AvailPhysicalERP AS int), 0)) > 0 AND (COALESCE(CAST(#R_SUM.QTY AS int), 0) + COALESCE(CAST(wms.QTY AS int), 0)) = 0)
								OR  ((COALESCE(CAST(#ERP_ALL.AvailPhysical AS int), 0) + COALESCE(CAST(#T.AvailPhysicalERP AS int), 0)) = 0 AND (COALESCE(CAST(#R_SUM.QTY AS int), 0) + COALESCE(CAST(wms.QTY AS int), 0)) > 0))
							  THEN 1 ELSE 0 END AS N'Контроль'
						,CASE WHEN #assortIM.ITEMID IS NOT NULL THEN 1 ELSE 0 END AS 'Продажи сайта'
						,COALESCE(CAST(#ostPC.QTY AS int), 0) AS N'Склад РС'
						,COALESCE(CAST(#ostLCPDCS.QTY AS int), 0) AS N'Все остатки ЛК_ПД_ЦС'
						,COALESCE(#ERP_InventTable.SignItem, 0) AS N'Признак товара'
						,COALESCE(#ERP_InventTable.EXTRADESC25ID, '') AS N'Доп характеристика 25'
						,COALESCE(CAST(iiis.MULTIPLEQTY as int), 0) AS N'Кол-во товаров в коробе'
						,COALESCE(#ERP_InventTable.ExtraDesc4Id, '') AS N'Доп хар 4 (Статус цикла жизни)'
				FROM #item
				LEFT OUTER JOIN #T					ON #T.ItemId		= #item.ItemId
				LEFT OUTER JOIN #tmp_Import_WMS wms ON wms.ItemId		= #item.ItemId
				LEFT OUTER JOIN #R_SUM				ON #R_SUM.ItemId	= #item.ItemId
				LEFT OUTER JOIN #ERP_ALL			ON #ERP_ALL.ItemId	= #item.ItemId
				LEFT OUTER JOIN #MSSN_ost			ON #MSSN_ost.ItemId = #item.ItemId
				LEFT OUTER JOIN #SQL_EXCH			ON #SQL_EXCH.ItemId = #item.ItemId
				LEFT OUTER JOIN InventTable	it		ON it.ITEMID		= #item.ItemId
				LEFT OUTER JOIN #ABC				ON #ABC.ITEMID		= #item.ItemId
				LEFT OUTER JOIN #assortIM			ON #assortIM.ITEMID	= #item.ItemId
				LEFT OUTER JOIN #ostPC				ON #ostPC.ItemId	= #item.ItemId
				LEFT OUTER JOIN #ostLCPDCS			ON #ostLCPDCS.itemId = #item.ItemId
				LEFT OUTER JOIN #ERP_InventTable    ON #ERP_InventTable.ItemId = #item.ItemId
				LEFT OUTER JOIN InventItemInventSetup iiis ON iiis.[PARTITION] = 5637144576 AND iiis.DATAAREAID = N'dat' AND iiis.ITEMID = #item.ItemId AND iiis.INVENTDIMID = N'AllBlank'

				DROP TABLE IF EXISTS #tmp_Import_WMS
				DROP TABLE IF EXISTS #T
				DROP TABLE IF EXISTS #ERP_ALL
				DROP TABLE IF EXISTS #R
				DROP TABLE IF EXISTS #SQL_EXCH
				DROP TABLE IF EXISTS #MSSN_ost
				DROP TABLE IF EXISTS #R_SUM
				DROP TABLE IF EXISTS #item
				DROP TABLE IF EXISTS #ABC
				DROP TABLE IF EXISTS #assortIM
				DROP TABLE IF EXISTS #ostPC
				DROP TABLE IF EXISTS #ostLCPDCS
				DROP TABLE IF EXISTS #ERP_InventTable
			--COMMIT TRANSACTION;  
			BREAK;
		END TRY
		BEGIN CATCH
			 SELECT @ERROR_NUM = ERROR_NUMBER(), @ERROR_MSG = ERROR_MESSAGE()
			 PRINT @ERROR_MSG
			 PRINT @ERROR_NUM
			-- ROLLBACK

			--INSERT INTO [dbo].[BO_log] ([Message], [Error],	[LogTime])
			--SELECT @ERROR_MSG, @ERROR_NUM, GETDATE();

			 SET @RETRY_COUNT_CURRENT = @RETRY_COUNT_CURRENT + 1

			 CONTINUE;
		END CATCH;
	END
--END