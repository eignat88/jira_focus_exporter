param(
    [ValidateSet("preflight", "full", "resume", "restart-stage", "validate-only", "status")]
    [string]$Mode = "preflight",

    [string]$Stage,

    [int]$BatchSize = 250000,

    [ValidateSet("none", "estimate", "exact", "cached")]
    [string]$CountMode = "estimate",

    [switch]$TruncateTarget,

    [switch]$AsciiProgress
)

# ── Encoding ────────────────────────────────────────────────────────
chcp 65001 | Out-Null
$Utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8
[Console]::OutputEncoding = $Utf8
$OutputEncoding = $Utf8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# ── Project root ────────────────────────────────────────────────────
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── Validate parameter combinations ─────────────────────────────────
$ForbiddenTruncate = @("preflight", "resume", "status", "validate-only")
if ($TruncateTarget -and $Mode -in $ForbiddenTruncate) {
    throw "-TruncateTarget is not allowed in mode '$Mode'."
}

if ($Mode -eq "restart-stage" -and -not $Stage) {
    throw "-Stage is required for mode 'restart-stage'."
}

if ($BatchSize -le 0) {
    throw "-BatchSize must be greater than 0."
}

# ── Header ──────────────────────────────────────────────────────────
Write-Host ("=" * 70)
Write-Host "RAW -> DDS PIPELINE (Unified ETL)"
Write-Host "Project: $ProjectRoot"
Write-Host "Mode: $Mode"
Write-Host "Batch Size: $BatchSize"
Write-Host "Count Mode: $CountMode"
if ($Stage) { Write-Host "Stage: $Stage" }
if ($TruncateTarget) { Write-Host "Truncate Target: YES" }
Write-Host ("=" * 70)

# ── Check Python ────────────────────────────────────────────────────
$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCmd) {
    Write-Host "ERROR: Python not found in PATH." -ForegroundColor Red
    exit 1
}

# ── Build arguments ─────────────────────────────────────────────────
$Arguments = @(
    "-m",
    "ax_to_postgres_etl.pipelines.dds_cli",
    "--mode", $Mode,
    "--batch-size", $BatchSize,
    "--count-mode", $CountMode
)

if ($Stage) {
    $Arguments += @("--stage", $Stage)
}

if ($TruncateTarget) {
    $Arguments += "--truncate-target"
}

if ($AsciiProgress) {
    $Arguments += "--ascii-progress"
}

# ── Execute from project root ───────────────────────────────────────
Push-Location $ProjectRoot

try {
    Write-Host "`nExecuting: python $($Arguments -join ' ')" -ForegroundColor DarkGray
    & python @Arguments
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

# ── Result ──────────────────────────────────────────────────────────
Write-Host ""
switch ($ExitCode) {
    0   { Write-Host "Pipeline completed successfully." -ForegroundColor Green }
    1   { Write-Host "Pipeline failed." -ForegroundColor Red }
    2   { Write-Host "Pipeline blocked (another run active)." -ForegroundColor Yellow }
    130 { Write-Host "Pipeline cancelled by user." -ForegroundColor Yellow }
    default { Write-Host "Pipeline exited with code $ExitCode." -ForegroundColor Red }
}

exit $ExitCode
