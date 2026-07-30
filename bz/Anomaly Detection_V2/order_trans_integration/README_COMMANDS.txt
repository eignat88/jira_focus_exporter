1. Copy scripts to project:

New-Item -ItemType Directory -Path "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\sql\order_trans" -Force
New-Item -ItemType Directory -Path "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\monitoring" -Force

Copy-Item "$env:USERPROFILE\Downloads\010_preflight_order_trans.sql" "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\sql\order_trans\010_preflight_order_trans.sql" -Force
Copy-Item "$env:USERPROFILE\Downloads\020_prepare_order_trans_model.sql" "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\sql\order_trans\020_prepare_order_trans_model.sql" -Force
Copy-Item "$env:USERPROFILE\Downloads\030_load_wmsordertrans_staging.ps1" "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\monitoring\030_load_wmsordertrans_staging.ps1" -Force
Copy-Item "$env:USERPROFILE\Downloads\040_validate_order_trans_ready.sql" "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\sql\order_trans\040_validate_order_trans_ready.sql" -Force
Copy-Item "$env:USERPROFILE\Downloads\050_load_dds_order_trans.sql" "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\sql\order_trans\050_load_dds_order_trans.sql" -Force

2. Read-only preflight:

& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -X -h localhost -p 5432 -U postgres -d wms_analysis -v ON_ERROR_STOP=1 -f "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\sql\order_trans\010_preflight_order_trans.sql"

3. Check disk and activity before changes:

Get-Volume -DriveLetter D | Select-Object DriveLetter,@{N='SizeGB';E={[math]::Round($_.Size/1GB,2)}},@{N='FreeGB';E={[math]::Round($_.SizeRemaining/1GB,2)}},@{N='FreePercent';E={[math]::Round($_.SizeRemaining/$_.Size*100,2)}}

4. Prepare DDS and staging (changing operation):

& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -X -h localhost -p 5432 -U postgres -d wms_analysis -v ON_ERROR_STOP=1 -f "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\sql\order_trans\020_prepare_order_trans_model.sql"

5. Load normalized staging, first benchmark with 100k:

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\monitoring\030_load_wmsordertrans_staging.ps1" -BatchSize 100000

Resume uses the same command because checkpoint JSON is read automatically.

6. Validate plan:

& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -X -h localhost -p 5432 -U postgres -d wms_analysis -v ON_ERROR_STOP=1 -f "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\sql\order_trans\040_validate_order_trans_ready.sql"

Required: Index Cond on recid_bigint. Not allowed: numeric Filter/Sort over btrim(recid)::bigint or Parallel Seq Scan.
