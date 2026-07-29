# ============================================================
# Запуск ETL от имени ALKOR\ignatchenko-adm
# ============================================================

Write-Host "============================================"
Write-Host " ETL: SQL Server AX2012 → PostgreSQL"
Write-Host "============================================"
Write-Host ""
Write-Host "Запуск от имени: ALKOR\ignatchenko-adm" -ForegroundColor Cyan
Write-Host ""

$etlPath = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\ax_to_postgres_etl"

# Запуск Python скрипта
python "$etlPath\main.py"

Write-Host ""
Write-Host "============================================"
Write-Host " Готово!"
Write-Host "============================================"
