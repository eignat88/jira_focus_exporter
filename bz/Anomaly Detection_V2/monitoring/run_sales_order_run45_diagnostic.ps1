param(
    [switch]$Analyze,
    [string]$AnalyzeBatches = "100000",
    [long]$RangeStart = 0
)

$ErrorActionPreference = "Stop"

$ProjectRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2"
$ScriptPath = Join-Path $ProjectRoot "monitoring\sales_order_run45_diagnostic.py"
$OutputDir = Join-Path $ProjectRoot "logs\4"

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Python diagnostic script not found: $ScriptPath"
}

if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

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
    $Arguments += $RangeStart.ToString()
}

Write-Host "Running:"
Write-Host "python $($Arguments -join ' ')"

& python @Arguments
$ExitCode = $LASTEXITCODE

if ($ExitCode -ne 0) {
    throw "Diagnostic script failed with exit code $ExitCode"
}

Write-Host "Diagnostic completed successfully."
