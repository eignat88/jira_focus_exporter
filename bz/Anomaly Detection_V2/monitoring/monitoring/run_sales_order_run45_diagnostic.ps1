param(
    [switch]$Analyze,
    [string]$AnalyzeBatches = "100000",
    [int]$RangeStart = 0
)

$ErrorActionPreference = "Stop"

$ProjectRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2"
$ScriptPath = Join-Path $ProjectRoot "monitoring\sales_order_run45_diagnostic.py"
$OutputDir = Join-Path $ProjectRoot "logs\4"

Set-Location $ProjectRoot

$Arguments = @(
    $ScriptPath,
    "--output-dir", $OutputDir,
    "--host", "localhost",
    "--port", "5432",
    "--database", "wms_analysis"
)

if ($Analyze) {
    $Arguments += "--analyze"
    $Arguments += "--analyze-batches"
    $Arguments += $AnalyzeBatches
}

if ($RangeStart -gt 0) {
    $Arguments += "--range-start"
    $Arguments += $RangeStart
}

python @Arguments

if ($LASTEXITCODE -ne 0) {
    throw "Диагностический скрипт завершился с кодом $LASTEXITCODE"
}
