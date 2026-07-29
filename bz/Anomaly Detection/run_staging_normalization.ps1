# Run NORMALIZED_STAGING for alk_markserial
# Usage: .\run_staging_normalization.ps1 -Mode full -BatchSize 250000

param(
    [ValidateSet("preflight", "full", "resume", "restart_stage", "validate_only")]
    [string]$Mode = "preflight",

    [string]$Stage = "serial_mark_normalization",

    [int]$BatchSize = 250000
)

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=" * 70
Write-Host "NORMALIZED_STAGING Pipeline"
Write-Host "Mode: $Mode"
Write-Host "Stage: $Stage"
Write-Host "Batch Size: $BatchSize"
Write-Host "=" * 70

# Run diagnostics first
Write-Host "`nRunning diagnostics..."
python -m ax_to_postgres_etl.diagnostics.raw_strategy_cli --mode scan --table alk_markserial

if ($Mode -ne "preflight") {
    Write-Host "`nStarting normalization..."
    python -m ax_to_postgres_etl.pipelines.dds_cli `
        --mode $Mode `
        --stage $Stage `
        --batch-size $BatchSize
}
