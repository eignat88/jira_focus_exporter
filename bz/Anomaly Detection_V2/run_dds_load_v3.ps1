param(
    [ValidateSet("preflight", "full", "resume", "restart_stage", "validate_only")]
    [string]$Mode = "preflight",

    [string]$Stage = "",

    [int]$BatchSize = 500000,

    [string]$Timezone = "Europe/Moscow",

    [int]$MaxAttempts = 3,

    [int]$ProgressWidth = 30,

    [ValidateSet("exact", "estimate", "cached")]
    [string]$CountMode = "exact",

    [switch]$AsciiProgress
)

chcp 65001 | Out-Null

$Utf8 = New-Object System.Text.UTF8Encoding($false)

[Console]::InputEncoding = $Utf8
[Console]::OutputEncoding = $Utf8
$OutputEncoding = $Utf8

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonScript = Join-Path $ProjectRoot "scripts\run_dds_load_v3.py"

if (-not (Test-Path $PythonScript)) {
    Write-Error "Python script not found: $PythonScript"
    exit 1
}

$Arguments = @(
    $PythonScript,
    "--mode", $Mode,
    "--batch-size", $BatchSize,
    "--timezone", $Timezone,
    "--max-attempts", $MaxAttempts,
    "--progress-width", $ProgressWidth,
    "--count-mode", $CountMode
)

if ($Stage) {
    $Arguments += @("--stage", $Stage)
}

if ($AsciiProgress) {
    $Arguments += "--ascii-progress"
}

Write-Host ("=" * 70)
Write-Host "DDS LOAD PIPELINE v3"
Write-Host "Mode: $Mode"
Write-Host "Batch size: $BatchSize"
Write-Host "Timezone: $Timezone"
Write-Host "Count mode: $CountMode"
if ($Stage) { Write-Host "Stage: $Stage" }
Write-Host ("=" * 70)

& python @Arguments

$ExitCode = $LASTEXITCODE

switch ($ExitCode) {
    0   { Write-Host "DDS pipeline completed successfully." -ForegroundColor Green }
    1   { Write-Host "DDS pipeline failed." -ForegroundColor Red }
    2   { Write-Host "Run blocked by preflight." -ForegroundColor Yellow }
    130 { Write-Host "DDS pipeline cancelled by user." -ForegroundColor Yellow }
    default { Write-Host "DDS pipeline exited with code $ExitCode." -ForegroundColor Red }
}

exit $ExitCode
