[CmdletBinding()]
param(
    [string]$ProjectRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2",
    [string]$LogRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\logs\3",
    [string]$PythonExe = "python",
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunDir = Join-Path $LogRoot "purchase_order_fix_validation_$Timestamp"
New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

$LogFile = Join-Path $RunDir "validation.log"
$SummaryFile = Join-Path $RunDir "validation_summary.csv"
$CodeMatchesFile = Join-Path $RunDir "code_matches.csv"

function Write-ValidationLog {
    param([string]$Message)

    $Line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $Line
    Add-Content -LiteralPath $LogFile -Value $Line -Encoding UTF8
}

function Invoke-NativeStep {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string[]]$Arguments,
        [bool]$FailureAllowed = $false
    )

    Write-ValidationLog "START $Name"
    Write-ValidationLog "COMMAND: $PythonExe $($Arguments -join ' ')"

    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    try {
        $Output = & $PythonExe @Arguments 2>&1
        $ExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousPreference
    }

    foreach ($Item in @($Output)) {
        $Line = $Item.ToString()
        Write-Host $Line
        Add-Content -LiteralPath $LogFile -Value $Line -Encoding UTF8
    }

    Write-ValidationLog "END $Name exit=$ExitCode"

    if ($ExitCode -ne 0 -and -not $FailureAllowed) {
        throw "$Name failed with exit code $ExitCode. See $LogFile"
    }

    return [int]$ExitCode
}

$CompileExit = -1
$TestsExit = -1
$PreflightExit = -1
$ReadyForFull = $false
$ValidationError = ""

Push-Location $ProjectRoot
try {
    # Step 1: syntax validation
    $CompileExit = Invoke-NativeStep `
        -Name "compileall" `
        -Arguments @("-m", "compileall", "ax_to_postgres_etl") `
        -FailureAllowed $false

    # Step 2: unit tests
    if ($SkipTests) {
        $TestsExit = -2
        Write-ValidationLog "SKIP pytest by parameter"
    }
    else {
        $TestsExit = Invoke-NativeStep `
            -Name "pytest" `
            -Arguments @("-m", "pytest") `
            -FailureAllowed $true
    }

    # Step 3: read-only preflight
    $PreflightExit = Invoke-NativeStep `
        -Name "purchase_order preflight" `
        -Arguments @(
            "-m",
            "ax_to_postgres_etl.pipelines.dds_cli",
            "--mode", "preflight",
            "--stage", "purchase_order",
            "--batch-size", "100000"
        ) `
        -FailureAllowed $true

    # Step 4: save code evidence
    $SourceFiles = @(
        "ax_to_postgres_etl\pipelines\raw_to_dds.py",
        "ax_to_postgres_etl\pipelines\runner.py"
    )

    $Matches = @(
        foreach ($File in $SourceFiles) {
            if (Test-Path -LiteralPath $File) {
                Select-String `
                    -Path $File `
                    -Pattern "recid_bigint|full_table|get_boundaries" |
                    Select-Object Path, LineNumber, Line
            }
        }
    )

    $Matches |
        Export-Csv `
            -LiteralPath $CodeMatchesFile `
            -NoTypeInformation `
            -Encoding UTF8

    $ReadyForFull = (
        $CompileExit -eq 0 -and
        $PreflightExit -eq 0
    )
}
catch {
    $ValidationError = $_.Exception.Message
    Write-ValidationLog "ERROR: $ValidationError"
}
finally {
    Pop-Location
}

[pscustomobject]@{
    timestamp       = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    compile_exit    = $CompileExit
    tests_exit      = $TestsExit
    preflight_exit  = $PreflightExit
    ready_for_full  = $ReadyForFull
    validation_error = $ValidationError
    decision        = if ($ReadyForFull) {
        "PRECHECK_OK_VERIFY_MAPPING_AND_BIGINT"
    }
    else {
        "BLOCKED"
    }
    note = if ($ReadyForFull) {
        "Preflight passed. Confirm purchase_order_id=bigint and corrected mapping before full."
    }
    else {
        "Do not run full. Review validation.log, code_matches.csv, and latest preflight JSON."
    }
} |
    Export-Csv `
        -LiteralPath $SummaryFile `
        -NoTypeInformation `
        -Encoding UTF8

Write-Host ""
Write-Host "Validation completed."
Write-Host "Log:     $LogFile"
Write-Host "Summary: $SummaryFile"
Write-Host "Matches: $CodeMatchesFile"
Write-Host "Decision: $(if ($ReadyForFull) { 'PRECHECK_OK' } else { 'BLOCKED' })"

if ($ValidationError) {
    exit 2
}

if ($CompileExit -ne 0) {
    exit 3
}

if ($PreflightExit -ne 0) {
    exit 1
}

exit 0
