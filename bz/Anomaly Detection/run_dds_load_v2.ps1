# Run DDS Load Pipeline v2
# Usage: .\run_dds_load_v2.ps1 -Mode full -BatchSize 500000

param(
    [ValidateSet('preflight', 'full', 'resume', 'restart_stage', 'validate_only')]
    [string]$Mode = 'preflight',
    
    [string]$Stage = '',
    
    [int]$BatchSize = 500000,
    
    [string]$Timezone = 'Europe/Moscow',
    
    [int]$MaxAttempts = 3,
    
    [int]$ProgressWidth = 30,
    
    [switch]$AsciiProgress
)

$ProjectRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection"

Set-Location $ProjectRoot

Write-Host "=" * 70
Write-Host "DDS LOAD PIPELINE v2"
Write-Host "Mode: $Mode"
Write-Host "Batch Size: $BatchSize"
Write-Host "Timezone: $Timezone"
Write-Host "Start: $(Get-Date -Format 'dd.MM.yyyy HH:mm:ss')"
Write-Host "=" * 70

$pythonArgs = @(
    ".\scripts\run_dds_load_v2.py",
    "--mode", $Mode,
    "--batch-size", $BatchSize,
    "--timezone", $Timezone,
    "--max-attempts", $MaxAttempts,
    "--progress-width", $ProgressWidth
)

if ($Stage) {
    $pythonArgs += "--stage", $Stage
}

if ($AsciiProgress) {
    $pythonArgs += "--ascii-progress"
}

& python $pythonArgs
