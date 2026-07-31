# Запускать из:
# D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\monitoring

[CmdletBinding()]
param(
    [string]$PythonExe = "python",
    [string]$PgHost = "localhost",
    [int]$PgPort = 5432,
    [string]$PgDatabase = "wms_analysis",
    [string]$PgUser = "postgres"
)

$ErrorActionPreference = "Stop"

$ProjectDir = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2"
$MonitoringDir = Join-Path $ProjectDir "monitoring"
$ScriptPath = Join-Path $MonitoringDir "diagnose_sales_order_preflight.py"
$LogDir = Join-Path $ProjectDir "logs\3"

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

if (-not (Test-Path $ScriptPath)) {
    throw "Не найден Python-скрипт: $ScriptPath"
}

$env:PGHOST = $PgHost
$env:PGPORT = "$PgPort"
$env:PGDATABASE = $PgDatabase
$env:PGUSER = $PgUser

Write-Host "RAW -> DDS diagnostic preflight"
Write-Host "Source: raw_ax.salestable"
Write-Host "Target: dds.sales_order"
Write-Host "Logs:   $LogDir"
Write-Host ""

& $PythonExe $ScriptPath
$ExitCode = $LASTEXITCODE

Write-Host ""
if ($ExitCode -eq 0) {
    Write-Host "Диагностика завершена успешно."
} else {
    Write-Host "Диагностика завершена с ошибкой. ExitCode=$ExitCode"
}

Write-Host "Последние файлы:"
Get-ChildItem $LogDir -File |
    Where-Object { $_.Name -like "sales_order_preflight_*" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 15 Name, Length, LastWriteTime |
    Format-Table -AutoSize

exit $ExitCode
