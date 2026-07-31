[CmdletBinding()]
param(
    [string]$ProjectRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2",
    [string]$LogRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\logs\3",
    [string]$PythonExe = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunDir = Join-Path $LogRoot "purchase_order_fix_validation_$Timestamp"
New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

$LogFile = Join-Path $RunDir "validation.log"

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Command,
        [switch]$AllowFailure
    )

    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] START $Name" |
        Tee-Object -FilePath $LogFile -Append

    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command 2>&1 |
            ForEach-Object {
                $_.ToString() | Tee-Object -FilePath $LogFile -Append
            }
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $oldPreference
    }

    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] END $Name exit=$exitCode" |
        Tee-Object -FilePath $LogFile -Append

    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "$Name failed with exit code $exitCode. See $LogFile"
    }

    return $exitCode
}

Push-Location $ProjectRoot
try {
    Invoke-Step "compileall" {
        & $PythonExe -m compileall ax_to_postgres_etl
    }

    # Tests are allowed to fail here so the preflight output is still collected.
    Invoke-Step "pytest" {
        & $PythonExe -m pytest
    } -AllowFailure | Out-Null

    $PreflightExit = Invoke-Step "purchase_order preflight" {
        & $PythonExe -m ax_to_postgres_etl.pipelines.dds_cli `
            --mode preflight `
            --stage purchase_order `
            --batch-size 100000
    } -AllowFailure

    $SourceFiles = @(
        "ax_to_postgres_etl\pipelines\raw_to_dds.py",
        "ax_to_postgres_etl\pipelines\runner.py"
    )

    $Matches = foreach ($File in $SourceFiles) {
        Select-String -Path $File -Pattern "recid_bigint|full_table|get_boundaries" |
            Select-Object Path, LineNumber, Line
    }

    $Matches |
        Export-Csv (Join-Path $RunDir "code_matches.csv") `
        -NoTypeInformation -Encoding UTF8

    [pscustomobject]@{
        timestamp      = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        compile_status = "OK"
        preflight_exit = $PreflightExit
        ready_for_full = ($PreflightExit -eq 0)
        note = if ($PreflightExit -eq 0) {
            "Preflight passed. Verify mapping and bigint target key before full."
        } else {
            "Do not run full. Review validation.log and latest preflight JSON."
        }
    } |
        Export-Csv (Join-Path $RunDir "validation_summary.csv") `
        -NoTypeInformation -Encoding UTF8
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Validation logs: $RunDir"
