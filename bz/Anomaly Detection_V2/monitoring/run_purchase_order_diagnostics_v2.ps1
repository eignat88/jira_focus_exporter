[CmdletBinding()]
param(
    [string]$ProjectRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2",
    [string]$LogRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\logs\3",
    [string]$PgHost = $(if ($env:PGHOST) { $env:PGHOST } else { "localhost" }),
    [int]$PgPort = $(if ($env:PGPORT) { [int]$env:PGPORT } else { 5432 }),
    [string]$PgDatabase = $(if ($env:PGDATABASE) { $env:PGDATABASE } else { "wms_analysis" }),
    [string]$PgUser = $(if ($env:PGUSER) { $env:PGUSER } else { "postgres" }),
    [string]$PsqlPath = "C:\Program Files\PostgreSQL\17\bin\psql.exe",
    [string]$PythonExe = "python",
    [int]$BatchSize = 100000,
    [string]$PreflightJson = "",
    [switch]$SkipPreflight
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Log {
    param(
        [Parameter(Mandatory)][string]$Message,
        [ValidateSet("INFO", "WARN", "ERROR")][string]$Level = "INFO"
    )
    $line = "{0:yyyy-MM-dd HH:mm:ss} [{1}] {2}" -f (Get-Date), $Level, $Message
    $line | Tee-Object -FilePath $script:MainLog -Append
}

function Add-SummaryRow {
    param(
        [Parameter(Mandatory)][string]$Section,
        [Parameter(Mandatory)][string]$Metric,
        [AllowNull()][object]$Value,
        [string]$Status = "INFO",
        [string]$Details = ""
    )

    $script:SummaryRows.Add([pscustomobject]@{
        timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        stage     = "purchase_order"
        source    = "raw_ax.purchtable"
        target    = "dds.purchase_order"
        section   = $Section
        metric    = $Metric
        value     = if ($null -eq $Value) { "" } else { [string]$Value }
        status    = $Status
        details   = $Details
    }) | Out-Null
}

function Import-CsvSafe {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return @()
    }

    $file = Get-Item -LiteralPath $Path
    if ($file.Length -eq 0) {
        return @()
    }

    try {
        return @(Import-Csv -LiteralPath $Path)
    }
    catch {
        Write-Log "Cannot import CSV '$Path': $($_.Exception.Message)" "WARN"
        return @()
    }
}

function Convert-ToBool {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return $false }
    return ([string]$Value).Trim().ToLowerInvariant() -in @("true", "t", "1", "yes")
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SqlScript = Join-Path $ScriptDir "purchase_order_diagnostics_v2.sql"

if (-not (Test-Path -LiteralPath $SqlScript)) {
    throw "SQL script not found: $SqlScript"
}
if (-not (Test-Path -LiteralPath $PsqlPath)) {
    throw "psql.exe not found: $PsqlPath"
}
if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    throw "Project directory not found: $ProjectRoot"
}

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunDir = Join-Path $LogRoot "purchase_order_$Timestamp"
New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

$script:MainLog = Join-Path $RunDir "purchase_order_diagnostics_$Timestamp.log"
$SummaryCsv = Join-Path $RunDir "purchase_order_summary_$Timestamp.csv"
$PreflightConsoleLog = Join-Path $RunDir "preflight_console_$Timestamp.log"
$PsqlConsoleLog = Join-Path $RunDir "psql_console_$Timestamp.log"
$script:SummaryRows = [System.Collections.Generic.List[object]]::new()

Write-Log "Diagnostics started"
Write-Log "ProjectRoot: $ProjectRoot"
Write-Log "RunDir: $RunDir"
Write-Log "PostgreSQL: ${PgHost}:${PgPort}/${PgDatabase}, user=$PgUser"

# -------------------------------------------------------------------------
# 1. Preflight
# -------------------------------------------------------------------------
$ResolvedPreflightJson = $null

if ($PreflightJson) {
    if (-not (Test-Path -LiteralPath $PreflightJson)) {
        throw "Specified preflight JSON not found: $PreflightJson"
    }
    $ResolvedPreflightJson = (Resolve-Path -LiteralPath $PreflightJson).Path
    Write-Log "Using supplied preflight JSON: $ResolvedPreflightJson"
}
elseif (-not $SkipPreflight) {
    Write-Log "Running read-only purchase_order preflight"

    $PreflightStartedAt = Get-Date
    Push-Location $ProjectRoot
    try {
        & $PythonExe -m ax_to_postgres_etl.pipelines.dds_cli `
            --mode preflight `
            --stage purchase_order `
            --batch-size $BatchSize 2>&1 |
            Tee-Object -FilePath $PreflightConsoleLog

        $PreflightExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    Write-Log "Preflight exit code: $PreflightExitCode"

    $Candidates = @(
        Get-ChildItem -LiteralPath $ProjectRoot -Filter "preflight_purchase_order_*.json" `
            -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -ge $PreflightStartedAt.AddMinutes(-1) } |
        Sort-Object LastWriteTime -Descending
    )

    if ($Candidates.Count -gt 0) {
        $ResolvedPreflightJson = $Candidates[0].FullName
        Copy-Item -LiteralPath $ResolvedPreflightJson `
            -Destination (Join-Path $RunDir $Candidates[0].Name) -Force
        $ResolvedPreflightJson = Join-Path $RunDir $Candidates[0].Name
        Write-Log "Preflight JSON found: $ResolvedPreflightJson"
    }
    else {
        Write-Log "Preflight JSON was not found after the command" "WARN"
    }
}
else {
    Write-Log "Preflight skipped by parameter" "WARN"
}

if ($ResolvedPreflightJson) {
    try {
        $Preflight = Get-Content -LiteralPath $ResolvedPreflightJson -Raw -Encoding UTF8 |
            ConvertFrom-Json

        Add-SummaryRow "preflight" "result" $Preflight.result `
            $(if ($Preflight.result -eq "OK") { "OK" } else { "BLOCKED" })

        Add-SummaryRow "preflight" "warnings" $Preflight.warnings `
            $(if ([int]$Preflight.warnings -eq 0) { "OK" } else { "WARN" })

        Add-SummaryRow "preflight" "errors" $Preflight.errors `
            $(if ([int]$Preflight.errors -eq 0) { "OK" } else { "ERROR" })

        foreach ($check in @($Preflight.checks)) {
            Add-SummaryRow "preflight_check" $check.name $check.message $check.status $check.details
        }
    }
    catch {
        Write-Log "Failed to parse preflight JSON: $($_.Exception.Message)" "ERROR"
        Add-SummaryRow "preflight" "json_parse" "FAILED" "ERROR" $_.Exception.Message
    }
}
else {
    Add-SummaryRow "preflight" "result" "NOT_AVAILABLE" "WARN" `
        "No preflight JSON was supplied or discovered"
}

# -------------------------------------------------------------------------
# 2. PostgreSQL read-only diagnostics
# -------------------------------------------------------------------------
Write-Log "Running PostgreSQL read-only diagnostics"

$PsqlOutputDir = $RunDir.Replace("\", "/")
$PsqlArgs = @(
    "-X",
    "--set", "ON_ERROR_STOP=1",
    "--host", $PgHost,
    "--port", [string]$PgPort,
    "--username", $PgUser,
    "--dbname", $PgDatabase,
    "--file", $SqlScript,
    "--set", "output_dir=$PsqlOutputDir"
)

$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $PsqlPath @PsqlArgs 2>&1 |
        ForEach-Object {
            $line = $_.ToString()
            $line | Tee-Object -FilePath $PsqlConsoleLog -Append
        }

    $PsqlExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
}

if ($PsqlExitCode -ne 0) {
    Write-Log "psql diagnostics failed with exit code $PsqlExitCode" "ERROR"
    Add-SummaryRow "execution" "psql_exit_code" $PsqlExitCode "ERROR" `
        "See $PsqlConsoleLog"
}
else {
    Write-Log "psql diagnostics completed"
    Add-SummaryRow "execution" "psql_exit_code" $PsqlExitCode "OK"
}

# -------------------------------------------------------------------------
# 3. Build consolidated summary
# -------------------------------------------------------------------------
$Existence = Import-CsvSafe (Join-Path $RunDir "table_existence.csv")
if ($Existence.Count -gt 0) {
    Add-SummaryRow "objects" "source_table_exists" `
        ($Existence[0].raw_table -eq "raw_ax.purchtable") `
        $(if ($Existence[0].raw_table) { "OK" } else { "ERROR" }) `
        $Existence[0].raw_table

    Add-SummaryRow "objects" "target_table_exists" `
        ($Existence[0].dds_table -eq "dds.purchase_order") `
        $(if ($Existence[0].dds_table) { "OK" } else { "ERROR" }) `
        $Existence[0].dds_table
}

$Sizes = Import-CsvSafe (Join-Path $RunDir "relation_sizes.csv")
foreach ($row in $Sizes) {
    $prefix = if ($row.schema_name -eq "raw_ax") { "source" } else { "target" }

    Add-SummaryRow "size" "${prefix}_estimated_rows" $row.estimated_rows "INFO"
    Add-SummaryRow "size" "${prefix}_heap_size" $row.heap_size "INFO" $row.heap_bytes
    Add-SummaryRow "size" "${prefix}_indexes_size" $row.indexes_size "INFO" $row.indexes_bytes
    Add-SummaryRow "size" "${prefix}_total_size" $row.total_size "INFO" $row.total_bytes
}

$SourceColumns = Import-CsvSafe (Join-Path $RunDir "source_columns.csv")
$TargetColumns = Import-CsvSafe (Join-Path $RunDir "target_columns.csv")

$SourceRecid = @($SourceColumns | Where-Object column_name -eq "recid")
if ($SourceRecid.Count -gt 0) {
    Add-SummaryRow "keys" "source_key_type" $SourceRecid[0].udt_name "OK" "raw_ax.purchtable.recid"
}
else {
    Add-SummaryRow "keys" "source_key_type" "MISSING" "ERROR" "raw_ax.purchtable.recid"
}

$TargetKey = @($TargetColumns | Where-Object column_name -eq "purchase_order_id")
if ($TargetKey.Count -gt 0) {
    $targetKeyStatus = if ($TargetKey[0].udt_name -eq "int8") { "OK" } else { "WARN" }
    Add-SummaryRow "keys" "target_key_type" $TargetKey[0].udt_name $targetKeyStatus `
        "dds.purchase_order.purchase_order_id"
}
else {
    Add-SummaryRow "keys" "target_key_type" "MISSING" "ERROR"
}

$MappingChecks = Import-CsvSafe (Join-Path $RunDir "mapping_columns_check.csv")
foreach ($row in $MappingChecks) {
    $exists = Convert-ToBool $row.exists_in_source
    Add-SummaryRow "mapping" $row.column_name $exists `
        $(if ($exists) { "OK" } else { "WARN" }) `
        $(if ($exists) { "$($row.data_type)/$($row.udt_name)" } else { "Column not found in raw_ax.purchtable" })
}

$RecidQuality = Import-CsvSafe (Join-Path $RunDir "recid_quality.csv")
if ($RecidQuality.Count -gt 0) {
    $q = $RecidQuality[0]

    Add-SummaryRow "recid_quality" "total_rows_exact" $q.total_rows "INFO"
    Add-SummaryRow "recid_quality" "null_recid" $q.null_recid `
        $(if ([int64]$q.null_recid -eq 0) { "OK" } else { "ERROR" })
    Add-SummaryRow "recid_quality" "empty_recid" $q.empty_recid `
        $(if ([int64]$q.empty_recid -eq 0) { "OK" } else { "ERROR" })
    Add-SummaryRow "recid_quality" "non_numeric_recid" $q.non_numeric_recid `
        $(if ([int64]$q.non_numeric_recid -eq 0) { "OK" } else { "ERROR" })
    Add-SummaryRow "recid_quality" "min_recid" $q.min_recid "INFO"
    Add-SummaryRow "recid_quality" "max_recid" $q.max_recid "INFO"

    $ExceedsInt4 = Convert-ToBool $q.exceeds_int4
    Add-SummaryRow "recid_quality" "exceeds_int4" $ExceedsInt4 `
        $(if ($ExceedsInt4) { "ERROR" } else { "OK" }) `
        $(if ($ExceedsInt4) {
            "Target purchase_order_id must be bigint before loading"
        } else {
            "Current maximum fits int4"
        })
}

$Constraints = Import-CsvSafe (Join-Path $RunDir "target_constraints.csv")
$PurchaseOrderPk = @(
    $Constraints |
    Where-Object {
        $_.conname -eq "purchase_order_pkey" -or
        ($_.contype -eq "p" -and $_.definition -match "purchase_order_id")
    }
)
Add-SummaryRow "constraints" "purchase_order_unique_constraint" `
    ($PurchaseOrderPk.Count -gt 0) `
    $(if ($PurchaseOrderPk.Count -gt 0) { "OK" } else { "ERROR" }) `
    $(if ($PurchaseOrderPk.Count -gt 0) { $PurchaseOrderPk[0].definition } else { "Not found" })

$Stats = Import-CsvSafe (Join-Path $RunDir "table_statistics.csv")
$SourceStats = @($Stats | Where-Object { $_.schemaname -eq "raw_ax" -and $_.relname -eq "purchtable" })
if ($SourceStats.Count -gt 0) {
    $lastAnalyze = if ($SourceStats[0].last_analyze) {
        $SourceStats[0].last_analyze
    }
    elseif ($SourceStats[0].last_autoanalyze) {
        $SourceStats[0].last_autoanalyze
    }
    else {
        ""
    }

    Add-SummaryRow "statistics" "source_last_analyze" `
        $(if ($lastAnalyze) { $lastAnalyze } else { "NOT_RECORDED" }) `
        $(if ($lastAnalyze) { "OK" } else { "WARN" }) `
        "The diagnostic script does not execute ANALYZE"
}

$Indexes = Import-CsvSafe (Join-Path $RunDir "indexes.csv")
$RecidBtree = @(
    $Indexes |
    Where-Object {
        $_.schemaname -eq "raw_ax" -and
        $_.tablename -eq "purchtable" -and
        $_.indexdef -match "USING btree" -and
        $_.indexdef -match "\(\s*recid\s*\)"
    }
)
Add-SummaryRow "indexes" "source_recid_btree_index" `
    ($RecidBtree.Count -gt 0) `
    $(if ($RecidBtree.Count -gt 0) { "OK" } else { "WARN" }) `
    $(if ($RecidBtree.Count -gt 0) { $RecidBtree[0].indexname } else { "Not found" })

$RecidBigintColumn = @($SourceColumns | Where-Object column_name -eq "recid_bigint")
Add-SummaryRow "configuration" "recid_bigint_exists" `
    ($RecidBigintColumn.Count -gt 0) `
    $(if ($RecidBigintColumn.Count -gt 0) { "OK" } else { "WARN" }) `
    "For chunk_strategy=full_table this column should not be required by preflight"

$MissingRequired = @(
    $MappingChecks |
    Where-Object {
        -not (Convert-ToBool $_.exists_in_source) -and
        $_.column_name -in @(
            "vendaccount",
            "orderdate",
            "deliverydate",
            "currencycode",
            "purchstatus",
            "modifieddatetime",
            "createddatetime",
            "dataareaid"
        )
    }
)

foreach ($missing in $MissingRequired) {
    Add-SummaryRow "blocking_reason" "missing_mapping_column" `
        $missing.column_name "ERROR" `
        "Correct YAML or adapter mapping before --mode full"
}

if ($RecidBigintColumn.Count -eq 0) {
    Add-SummaryRow "blocking_reason" "preflight_recid_bigint_validation" `
        "recid_bigint is absent" "ERROR" `
        "Preflight must ignore numeric-range key and index checks for chunk_strategy=full_table"
}

$Activity = Import-CsvSafe (Join-Path $RunDir "pg_stat_activity.csv")
$LongTransactions = @(
    $Activity |
    Where-Object {
        $_.transaction_duration -and
        $_.transaction_duration -notmatch "^00:00:"
    }
)
Add-SummaryRow "runtime" "active_sessions" $Activity.Count "INFO"
Add-SummaryRow "runtime" "potential_long_transactions" $LongTransactions.Count `
    $(if ($LongTransactions.Count -eq 0) { "OK" } else { "WARN" })

$CreateIndexProgress = Import-CsvSafe (Join-Path $RunDir "pg_stat_progress_create_index.csv")
$VacuumProgress = Import-CsvSafe (Join-Path $RunDir "pg_stat_progress_vacuum.csv")
Add-SummaryRow "runtime" "active_create_index" $CreateIndexProgress.Count `
    $(if ($CreateIndexProgress.Count -eq 0) { "OK" } else { "WARN" })
Add-SummaryRow "runtime" "active_vacuum" $VacuumProgress.Count `
    $(if ($VacuumProgress.Count -eq 0) { "OK" } else { "WARN" })

# Decision
$ErrorRows = @($script:SummaryRows | Where-Object status -eq "ERROR")
$WarnRows = @($script:SummaryRows | Where-Object status -eq "WARN")

$Decision = if ($ErrorRows.Count -eq 0) {
    "READY_FOR_TEST_LOAD"
}
else {
    "BLOCKED"
}

Add-SummaryRow "decision" "purchase_order_load_status" $Decision `
    $(if ($Decision -eq "READY_FOR_TEST_LOAD") { "OK" } else { "BLOCKED" }) `
    "Run --mode full only when preflight errors=0 and no blocking diagnostics remain"

Add-SummaryRow "decision" "error_count" $ErrorRows.Count `
    $(if ($ErrorRows.Count -eq 0) { "OK" } else { "ERROR" })
Add-SummaryRow "decision" "warning_count" $WarnRows.Count `
    $(if ($WarnRows.Count -eq 0) { "OK" } else { "WARN" })

$script:SummaryRows |
    Export-Csv -LiteralPath $SummaryCsv -NoTypeInformation -Encoding UTF8

$LatestSummaryCsv = Join-Path $LogRoot "purchase_order_summary_latest.csv"
Copy-Item -LiteralPath $SummaryCsv -Destination $LatestSummaryCsv -Force

Write-Log "Summary CSV: $SummaryCsv"
Write-Log "Latest summary copy: $LatestSummaryCsv"
Write-Log "Decision: $Decision"
Write-Log "Errors: $($ErrorRows.Count), warnings: $($WarnRows.Count)"
Write-Log "Diagnostics finished"

Write-Host ""
Write-Host "Diagnostics complete."
Write-Host "Summary: $SummaryCsv"
Write-Host "Logs:    $RunDir"
Write-Host "Status:  $Decision"
