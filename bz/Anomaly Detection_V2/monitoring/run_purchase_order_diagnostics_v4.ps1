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

function Add-Row {
    param(
        [Parameter(Mandatory)][string]$Section,
        [Parameter(Mandatory)][string]$Metric,
        [AllowNull()][object]$Value,
        [string]$Status = "INFO",
        [string]$Details = ""
    )
    $script:Rows.Add([pscustomobject]@{
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

function Add-ExecutiveRow {
    param(
        [Parameter(Mandatory)][int]$Order,
        [Parameter(Mandatory)][string]$Category,
        [Parameter(Mandatory)][string]$Check,
        [AllowNull()][object]$Value,
        [string]$Status = "INFO",
        [string]$Impact = "",
        [string]$RequiredAction = ""
    )
    $script:ExecutiveRows.Add([pscustomobject]@{
        order           = $Order
        timestamp       = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        category        = $Category
        check           = $Check
        value           = if ($null -eq $Value) { "" } else { [string]$Value }
        status          = $Status
        impact          = $Impact
        required_action = $RequiredAction
    }) | Out-Null
}

function Import-CsvSafe {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    if ((Get-Item -LiteralPath $Path).Length -eq 0) { return @() }
    try { return @(Import-Csv -LiteralPath $Path) }
    catch {
        Write-Log "Cannot import CSV '$Path': $($_.Exception.Message)" "WARN"
        return @()
    }
}

function To-Bool {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return $false }
    return ([string]$Value).Trim().ToLowerInvariant() -in @("true", "t", "1", "yes")
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SqlScript = Join-Path $ScriptDir "purchase_order_diagnostics_v3.sql"

if (-not (Test-Path -LiteralPath $SqlScript)) { throw "SQL script not found: $SqlScript" }
if (-not (Test-Path -LiteralPath $PsqlPath)) { throw "psql.exe not found: $PsqlPath" }
if (-not (Test-Path -LiteralPath $ProjectRoot)) { throw "Project directory not found: $ProjectRoot" }

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunDir = Join-Path $LogRoot "purchase_order_$Timestamp"
New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

$script:MainLog = Join-Path $RunDir "purchase_order_diagnostics_$Timestamp.log"
$DetailedCsv = Join-Path $RunDir "purchase_order_summary_detailed_$Timestamp.csv"
$ExecutiveCsv = Join-Path $RunDir "purchase_order_summary_$Timestamp.csv"
$PreflightConsoleLog = Join-Path $RunDir "preflight_console_$Timestamp.log"
$PsqlConsoleLog = Join-Path $RunDir "psql_console_$Timestamp.log"

$script:Rows = [System.Collections.Generic.List[object]]::new()
$script:ExecutiveRows = [System.Collections.Generic.List[object]]::new()

Write-Log "Diagnostics started"
Write-Log "RunDir: $RunDir"
Write-Log "PostgreSQL: ${PgHost}:${PgPort}/${PgDatabase}, user=$PgUser"

# 1. Run or load preflight
$ResolvedPreflightJson = $null
$Preflight = $null

if ($PreflightJson) {
    if (-not (Test-Path -LiteralPath $PreflightJson)) {
        throw "Specified preflight JSON not found: $PreflightJson"
    }
    $ResolvedPreflightJson = (Resolve-Path -LiteralPath $PreflightJson).Path
}
elseif (-not $SkipPreflight) {
    $PreflightStartedAt = Get-Date
    Push-Location $ProjectRoot
    try {
        $PreviousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $PythonExe -m ax_to_postgres_etl.pipelines.dds_cli `
                --mode preflight `
                --stage purchase_order `
                --batch-size $BatchSize 2>&1 |
                ForEach-Object {
                    $_.ToString() | Tee-Object -FilePath $PreflightConsoleLog -Append
                }
            $PreflightExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $PreviousPreference
        }
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
        $ResolvedPreflightJson = Join-Path $RunDir $Candidates[0].Name
        Copy-Item -LiteralPath $Candidates[0].FullName -Destination $ResolvedPreflightJson -Force
    }
}

if ($ResolvedPreflightJson) {
    $Preflight = Get-Content -LiteralPath $ResolvedPreflightJson -Raw -Encoding UTF8 | ConvertFrom-Json
    Add-Row "preflight" "result" $Preflight.result $(if ($Preflight.result -eq "OK") { "OK" } else { "BLOCKED" })
    Add-Row "preflight" "warnings" $Preflight.warnings $(if ([int]$Preflight.warnings -eq 0) { "OK" } else { "WARN" })
    Add-Row "preflight" "errors" $Preflight.errors $(if ([int]$Preflight.errors -eq 0) { "OK" } else { "ERROR" })
    foreach ($check in @($Preflight.checks)) {
        Add-Row "preflight_check" $check.name $check.message $check.status $check.details
    }
}
else {
    Add-Row "preflight" "result" "NOT_AVAILABLE" "WARN" "No preflight JSON available"
}

# 2. PostgreSQL diagnostics
$PsqlOutputDir = $RunDir.Replace("\", "/")
$PsqlArgs = @(
    "-X", "--set", "ON_ERROR_STOP=1",
    "--host", $PgHost,
    "--port", [string]$PgPort,
    "--username", $PgUser,
    "--dbname", $PgDatabase,
    "--file", $SqlScript,
    "--set", "output_dir=$PsqlOutputDir"
)

$PreviousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $PsqlPath @PsqlArgs 2>&1 |
        ForEach-Object {
            $_.ToString() | Tee-Object -FilePath $PsqlConsoleLog -Append
        }
    $PsqlExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $PreviousPreference
}

Add-Row "execution" "psql_exit_code" $PsqlExitCode `
    $(if ($PsqlExitCode -eq 0) { "OK" } else { "ERROR" }) `
    $(if ($PsqlExitCode -eq 0) { "" } else { "See $PsqlConsoleLog" })

if ($PsqlExitCode -ne 0) {
    throw "PostgreSQL diagnostics failed. See $PsqlConsoleLog"
}

# 3. Load raw diagnostic CSV files
$Objects = @(Import-CsvSafe (Join-Path $RunDir "object_existence.csv"))
$SourceColumns = @(Import-CsvSafe (Join-Path $RunDir "source_columns.csv"))
$TargetColumns = @(Import-CsvSafe (Join-Path $RunDir "target_columns.csv"))
$Sizes = @(Import-CsvSafe (Join-Path $RunDir "relation_sizes.csv"))
$TargetCount = @(Import-CsvSafe (Join-Path $RunDir "target_row_count.csv"))
$Stats = @(Import-CsvSafe (Join-Path $RunDir "table_statistics.csv"))
$Indexes = @(Import-CsvSafe (Join-Path $RunDir "indexes.csv"))
$Constraints = @(Import-CsvSafe (Join-Path $RunDir "target_constraints.csv"))
$Recid = @(Import-CsvSafe (Join-Path $RunDir "recid_quality.csv"))
$Mapping = @(Import-CsvSafe (Join-Path $RunDir "mapping_columns_check.csv"))
$RelevantActivity = @(Import-CsvSafe (Join-Path $RunDir "relevant_activity.csv"))
$LongTransactions = @(Import-CsvSafe (Join-Path $RunDir "long_transactions.csv"))
$CreateIndex = @(Import-CsvSafe (Join-Path $RunDir "pg_stat_progress_create_index.csv"))
$Vacuum = @(Import-CsvSafe (Join-Path $RunDir "pg_stat_progress_vacuum.csv"))

$SourceExists = $Objects.Count -gt 0 -and [bool]$Objects[0].source_table
$TargetExists = $Objects.Count -gt 0 -and [bool]$Objects[0].target_table

$SourceSize = @($Sizes | Where-Object schema_name -eq "raw_ax")
$TargetSize = @($Sizes | Where-Object schema_name -eq "dds")
$SourceRecidColumn = @($SourceColumns | Where-Object column_name -eq "recid")
$TargetKey = @($TargetColumns | Where-Object column_name -eq "purchase_order_id")
$Pk = @($Constraints | Where-Object { $_.conname -eq "purchase_order_pkey" -or ($_.contype -eq "p" -and $_.definition -match "purchase_order_id") })
$RecidIndex = @($Indexes | Where-Object {
    $_.schemaname -eq "raw_ax" -and
    $_.tablename -eq "purchtable" -and
    $_.indexdef -match "USING btree" -and
    $_.indexdef -match "\(\s*recid\s*\)"
})
$SourceStats = @($Stats | Where-Object { $_.schemaname -eq "raw_ax" -and $_.relname -eq "purchtable" })

$Q = if ($Recid.Count -gt 0) { $Recid[0] } else { $null }
$ExceedsInt4 = if ($Q) { To-Bool $Q.exceeds_int4 } else { $false }
$TargetIsInt8 = $TargetKey.Count -gt 0 -and $TargetKey[0].udt_name -eq "int8"
$TargetRowsExact = if ($TargetCount.Count -gt 0) { [int64]$TargetCount[0].target_rows_exact } else { -1 }
$SourceAnalyze = if ($SourceStats.Count -gt 0) {
    if ($SourceStats[0].last_analyze) { $SourceStats[0].last_analyze }
    elseif ($SourceStats[0].last_autoanalyze) { $SourceStats[0].last_autoanalyze }
    else { "" }
} else { "" }

$VendAccount = @($Mapping | Where-Object column_name -eq "vendaccount")
$OrderDate = @($Mapping | Where-Object column_name -eq "orderdate")
$RecidBigint = @($Mapping | Where-Object column_name -eq "recid_bigint")

$VendAccountExists = $VendAccount.Count -gt 0 -and (To-Bool $VendAccount[0].exists_in_source)
$OrderDateExists = $OrderDate.Count -gt 0 -and (To-Bool $OrderDate[0].exists_in_source)
$RecidBigintExists = $RecidBigint.Count -gt 0 -and (To-Bool $RecidBigint[0].exists_in_source)

$PreflightResult = if ($Preflight) { [string]$Preflight.result } else { "NOT_AVAILABLE" }
$PreflightErrors = if ($Preflight) { [int]$Preflight.errors } else { -1 }
$PreflightWarnings = if ($Preflight) { [int]$Preflight.warnings } else { -1 }

$PreflightRecidBigintError = $false
$PreflightFullTable = $false
if ($Preflight) {
    foreach ($check in @($Preflight.checks)) {
        $text = "$($check.message) $($check.details)"
        if ($text -match "recid_bigint") { $PreflightRecidBigintError = $true }
        if ($text -match "strategy full_table") { $PreflightFullTable = $true }
    }
}

# 4. Detailed rows
Add-Row "objects" "source_table_exists" $SourceExists $(if ($SourceExists) { "OK" } else { "ERROR" })
Add-Row "objects" "target_table_exists" $TargetExists $(if ($TargetExists) { "OK" } else { "ERROR" })
if ($SourceSize.Count -gt 0) {
    Add-Row "source" "estimated_rows" $SourceSize[0].estimated_rows "INFO"
    Add-Row "source" "heap_size" $SourceSize[0].heap_size "INFO"
    Add-Row "source" "indexes_size" $SourceSize[0].indexes_size "INFO"
    Add-Row "source" "total_size" $SourceSize[0].total_size "INFO"
}
if ($TargetSize.Count -gt 0) {
    Add-Row "target" "estimated_rows" $TargetSize[0].estimated_rows "INFO"
    Add-Row "target" "heap_size" $TargetSize[0].heap_size "INFO"
    Add-Row "target" "indexes_size" $TargetSize[0].indexes_size "INFO"
    Add-Row "target" "total_size" $TargetSize[0].total_size "INFO"
}
Add-Row "target" "exact_rows" $TargetRowsExact $(if ($TargetRowsExact -eq 0) { "OK" } else { "INFO" })
Add-Row "keys" "source_recid_type" $(if ($SourceRecidColumn.Count -gt 0) { $SourceRecidColumn[0].udt_name } else { "MISSING" }) `
    $(if ($SourceRecidColumn.Count -gt 0) { "OK" } else { "ERROR" })
Add-Row "keys" "target_purchase_order_id_type" $(if ($TargetKey.Count -gt 0) { $TargetKey[0].udt_name } else { "MISSING" }) `
    $(if ($TargetIsInt8) { "OK" } else { "ERROR" })
Add-Row "constraints" "purchase_order_pkey" ($Pk.Count -gt 0) $(if ($Pk.Count -gt 0) { "OK" } else { "ERROR" }) `
    $(if ($Pk.Count -gt 0) { $Pk[0].definition } else { "Not found" })
Add-Row "indexes" "source_recid_btree" ($RecidIndex.Count -gt 0) $(if ($RecidIndex.Count -gt 0) { "OK" } else { "WARN" }) `
    $(if ($RecidIndex.Count -gt 0) { $RecidIndex[0].indexname } else { "Not found" })

if ($Q) {
    Add-Row "recid" "total_rows_exact" $Q.total_rows "INFO"
    Add-Row "recid" "null_recid" $Q.null_recid $(if ([int64]$Q.null_recid -eq 0) { "OK" } else { "ERROR" })
    Add-Row "recid" "empty_recid" $Q.empty_recid $(if ([int64]$Q.empty_recid -eq 0) { "OK" } else { "ERROR" })
    Add-Row "recid" "non_numeric_recid" $Q.non_numeric_recid $(if ([int64]$Q.non_numeric_recid -eq 0) { "OK" } else { "ERROR" })
    Add-Row "recid" "min_recid" $Q.min_recid "INFO"
    Add-Row "recid" "max_recid" $Q.max_recid "INFO"
    Add-Row "recid" "int4_max" $Q.int4_max "INFO"
    Add-Row "recid" "exceeds_int4" $ExceedsInt4 $(if ($ExceedsInt4) { "ERROR" } else { "OK" })
}
Add-Row "mapping" "vendaccount_exists" $VendAccountExists $(if ($VendAccountExists) { "OK" } else { "ERROR" })
Add-Row "mapping" "orderdate_exists" $OrderDateExists $(if ($OrderDateExists) { "OK" } else { "ERROR" })
Add-Row "preflight_logic" "chunk_strategy_full_table_detected" $PreflightFullTable $(if ($PreflightFullTable) { "OK" } else { "WARN" })
Add-Row "preflight_logic" "recid_bigint_exists" $RecidBigintExists $(if ($RecidBigintExists) { "INFO" } else { "WARN" })
Add-Row "preflight_logic" "preflight_checks_missing_recid_bigint" $PreflightRecidBigintError `
    $(if ($PreflightRecidBigintError -and $PreflightFullTable) { "ERROR" } else { "INFO" })
Add-Row "statistics" "source_last_analyze" $(if ($SourceAnalyze) { $SourceAnalyze } else { "NOT_RECORDED" }) `
    $(if ($SourceAnalyze) { "OK" } else { "WARN" })
Add-Row "runtime" "relevant_sessions" $RelevantActivity.Count $(if ($RelevantActivity.Count -eq 0) { "OK" } else { "WARN" })
Add-Row "runtime" "long_transactions_15m" $LongTransactions.Count $(if ($LongTransactions.Count -eq 0) { "OK" } else { "WARN" })
Add-Row "runtime" "active_vacuum" $Vacuum.Count $(if ($Vacuum.Count -eq 0) { "OK" } else { "WARN" })
Add-Row "runtime" "active_create_index" $CreateIndex.Count $(if ($CreateIndex.Count -eq 0) { "OK" } else { "WARN" })

# 5. Executive summary
$Order = 10
Add-ExecutiveRow $Order "status" "preflight_result" $PreflightResult `
    $(if ($PreflightResult -eq "OK") { "OK" } else { "BLOCKED" }) `
    "The stage cannot be loaded while preflight is blocked." `
    "Fix all preflight errors and rerun --mode preflight."
$Order += 10
Add-ExecutiveRow $Order "status" "preflight_errors" $PreflightErrors `
    $(if ($PreflightErrors -eq 0) { "OK" } else { "ERROR" })
$Order += 10
Add-ExecutiveRow $Order "status" "preflight_warnings" $PreflightWarnings `
    $(if ($PreflightWarnings -eq 0) { "OK" } else { "WARN" })

$Facts = @(
    @{ Cat="availability"; Check="source_table_exists"; Value=$SourceExists; Status=$(if ($SourceExists){"OK"}else{"ERROR"}); Impact=""; Action="" },
    @{ Cat="availability"; Check="target_table_exists"; Value=$TargetExists; Status=$(if ($TargetExists){"OK"}else{"ERROR"}); Impact=""; Action="" },
    @{ Cat="runtime"; Check="conflicting_sessions"; Value=$RelevantActivity.Count; Status=$(if ($RelevantActivity.Count -eq 0){"OK"}else{"WARN"}); Impact="Sessions may hold or wait for locks."; Action="Review relevant_activity.csv before DDL or load." },
    @{ Cat="runtime"; Check="long_transactions_15m"; Value=$LongTransactions.Count; Status=$(if ($LongTransactions.Count -eq 0){"OK"}else{"WARN"}); Impact="Long transactions can retain MVCC snapshots and delay cleanup."; Action="Review long_transactions.csv." },
    @{ Cat="runtime"; Check="active_vacuum"; Value=$Vacuum.Count; Status=$(if ($Vacuum.Count -eq 0){"OK"}else{"WARN"}); Impact="Concurrent I/O can affect timing."; Action="Wait or assess vacuum target." },
    @{ Cat="runtime"; Check="active_create_index"; Value=$CreateIndex.Count; Status=$(if ($CreateIndex.Count -eq 0){"OK"}else{"WARN"}); Impact="Concurrent index build can increase I/O."; Action="Wait or assess active index build." }
)
foreach ($f in $Facts) {
    $Order += 10
    Add-ExecutiveRow $Order $f.Cat $f.Check $f.Value $f.Status $f.Impact $f.Action
}

if ($SourceSize.Count -gt 0) {
    foreach ($pair in @(
        @("source_estimated_rows",$SourceSize[0].estimated_rows),
        @("source_heap_size",$SourceSize[0].heap_size),
        @("source_indexes_size",$SourceSize[0].indexes_size),
        @("source_total_size",$SourceSize[0].total_size)
    )) {
        $Order += 10
        Add-ExecutiveRow $Order "source" $pair[0] $pair[1] "INFO"
    }
}
if ($Q) {
    foreach ($pair in @(
        @("source_exact_rows",$Q.total_rows),
        @("min_recid",$Q.min_recid),
        @("max_recid",$Q.max_recid),
        @("int4_max",$Q.int4_max),
        @("null_recid",$Q.null_recid),
        @("empty_recid",$Q.empty_recid),
        @("non_numeric_recid",$Q.non_numeric_recid)
    )) {
        $Order += 10
        Add-ExecutiveRow $Order "source" $pair[0] $pair[1] "INFO"
    }
}
if ($TargetSize.Count -gt 0) {
    foreach ($pair in @(
        @("target_exact_rows",$TargetRowsExact),
        @("target_heap_size",$TargetSize[0].heap_size),
        @("target_indexes_size",$TargetSize[0].indexes_size),
        @("target_total_size",$TargetSize[0].total_size)
    )) {
        $Order += 10
        Add-ExecutiveRow $Order "target" $pair[0] $pair[1] "INFO"
    }
}

$Order += 10
Add-ExecutiveRow $Order "schema" "source_recid_type" `
    $(if ($SourceRecidColumn.Count -gt 0) { $SourceRecidColumn[0].udt_name } else { "MISSING" }) `
    $(if ($SourceRecidColumn.Count -gt 0) { "OK" } else { "ERROR" })
$Order += 10
Add-ExecutiveRow $Order "schema" "target_purchase_order_id_type" `
    $(if ($TargetKey.Count -gt 0) { $TargetKey[0].udt_name } else { "MISSING" }) `
    $(if ($TargetIsInt8) { "OK" } else { "ERROR" }) `
    $(if ($ExceedsInt4 -and -not $TargetIsInt8) { "AX RECID values exceed PostgreSQL int4; load will fail with integer out of range." } else { "" }) `
    $(if ($ExceedsInt4 -and -not $TargetIsInt8) { "Change dds.purchase_order.purchase_order_id to bigint before loading." } else { "" })
$Order += 10
Add-ExecutiveRow $Order "schema" "purchase_order_pkey" `
    $(if ($Pk.Count -gt 0) { $Pk[0].definition } else { "NOT_FOUND" }) `
    $(if ($Pk.Count -gt 0) { "OK" } else { "ERROR" })
$Order += 10
Add-ExecutiveRow $Order "schema" "source_recid_btree_index" `
    $(if ($RecidIndex.Count -gt 0) { $RecidIndex[0].indexname } else { "NOT_FOUND" }) `
    $(if ($RecidIndex.Count -gt 0) { "OK" } else { "WARN" })

$Order += 10
Add-ExecutiveRow $Order "blocker" "target_key_int4_overflow" `
    ($ExceedsInt4 -and -not $TargetIsInt8) `
    $(if ($ExceedsInt4 -and -not $TargetIsInt8) { "ERROR" } else { "OK" }) `
    $(if ($ExceedsInt4 -and -not $TargetIsInt8) { "Guaranteed integer out of range during load." } else { "" }) `
    $(if ($ExceedsInt4 -and -not $TargetIsInt8) { "ALTER target key to bigint while the target table is empty." } else { "" })
$Order += 10
Add-ExecutiveRow $Order "blocker" "missing_vendaccount" (-not $VendAccountExists) `
    $(if ($VendAccountExists) { "OK" } else { "ERROR" }) `
    $(if ($VendAccountExists) { "" } else { "Current mapping references a missing source column." }) `
    $(if ($VendAccountExists) { "" } else { "Use source_candidate_columns.csv to identify the real source field and fix YAML/adapter mapping." })
$Order += 10
Add-ExecutiveRow $Order "blocker" "missing_orderdate" (-not $OrderDateExists) `
    $(if ($OrderDateExists) { "OK" } else { "ERROR" }) `
    $(if ($OrderDateExists) { "" } else { "Current mapping references a missing source column." }) `
    $(if ($OrderDateExists) { "" } else { "Use source_candidate_columns.csv to identify the real source field and fix YAML/adapter mapping." })
$Order += 10
Add-ExecutiveRow $Order "blocker" "full_table_preflight_uses_recid_bigint" `
    ($PreflightFullTable -and $PreflightRecidBigintError -and -not $RecidBigintExists) `
    $(if ($PreflightFullTable -and $PreflightRecidBigintError -and -not $RecidBigintExists) { "ERROR" } else { "OK" }) `
    $(if ($PreflightFullTable -and $PreflightRecidBigintError -and -not $RecidBigintExists) { "Preflight incorrectly validates a numeric-range key for a full_table stage." } else { "" }) `
    $(if ($PreflightFullTable -and $PreflightRecidBigintError -and -not $RecidBigintExists) { "For full_table, skip recid_bigint index/range checks and run EXPLAIN without ANALYZE on the full SELECT." } else { "" })

$Order += 10
Add-ExecutiveRow $Order "quality" "recid_cast_to_bigint_safe" `
    $(if ($Q) { ([int64]$Q.null_recid -eq 0 -and [int64]$Q.empty_recid -eq 0 -and [int64]$Q.non_numeric_recid -eq 0) } else { $false }) `
    $(if ($Q -and [int64]$Q.null_recid -eq 0 -and [int64]$Q.empty_recid -eq 0 -and [int64]$Q.non_numeric_recid -eq 0) { "OK" } else { "ERROR" }) `
    "Use btrim(src.recid)::bigint during INSERT; do not mass-update RAW." `
    "Keep RAW unchanged and normalize during RAW -> DDS."
$Order += 10
Add-ExecutiveRow $Order "statistics" "source_analyze_recorded" ([bool]$SourceAnalyze) `
    $(if ($SourceAnalyze) { "OK" } else { "WARN" }) `
    $(if ($SourceAnalyze) { "" } else { "Planner statistics have no recorded ANALYZE timestamp." }) `
    $(if ($SourceAnalyze) { "" } else { "Run ANALYZE raw_ax.purchtable separately after reviewing current activity." })

# Decision is based on blocking conditions, not on informational warnings.
$BlockingConditions = @(
    (-not $SourceExists),
    (-not $TargetExists),
    ($Pk.Count -eq 0),
    ($ExceedsInt4 -and -not $TargetIsInt8),
    (-not $VendAccountExists),
    (-not $OrderDateExists),
    ($PreflightFullTable -and $PreflightRecidBigintError -and -not $RecidBigintExists),
    ($PreflightResult -ne "OK")
)
$BlockingCount = @($BlockingConditions | Where-Object { $_ }).Count
$Decision = if ($BlockingCount -eq 0) { "READY_FOR_TEST_LOAD" } else { "BLOCKED" }

$Order += 10
Add-ExecutiveRow $Order "decision" "purchase_order_load_status" $Decision `
    $(if ($Decision -eq "READY_FOR_TEST_LOAD") { "OK" } else { "BLOCKED" }) `
    $(if ($Decision -eq "BLOCKED") { "Do not run --mode full." } else { "Preflight and diagnostics allow a controlled test load." }) `
    $(if ($Decision -eq "BLOCKED") { "Resolve all ERROR rows, rerun diagnostics, and require preflight errors=0." } else { "Run a controlled test load and validate counts and conflicts." })

$ExecutiveErrors = @($script:ExecutiveRows | Where-Object status -eq "ERROR").Count
$ExecutiveWarnings = @($script:ExecutiveRows | Where-Object status -eq "WARN").Count
$Order += 10
Add-ExecutiveRow $Order "decision" "errors" $ExecutiveErrors $(if ($ExecutiveErrors -eq 0) { "OK" } else { "ERROR" })
$Order += 10
Add-ExecutiveRow $Order "decision" "warnings" $ExecutiveWarnings $(if ($ExecutiveWarnings -eq 0) { "OK" } else { "WARN" })

# Recommended sequence
$Actions = @(
    "1. Change dds.purchase_order.purchase_order_id from int4 to bigint while the target is empty.",
    "2. Review source_candidate_columns.csv and identify real fields for vendaccount and orderdate.",
    "3. Correct YAML or adapter mapping.",
    "4. Correct preflight branching for chunk_strategy=full_table.",
    "5. Run ANALYZE raw_ax.purchtable as a separate controlled operation.",
    "6. Rerun preflight and diagnostics.",
    "7. Run --mode full only when preflight result=OK and errors=0."
)
foreach ($Action in $Actions) {
    $Order += 10
    Add-ExecutiveRow $Order "recommended_sequence" "action" $Action "ACTION"
}

# 6. Save outputs
$script:Rows | Export-Csv -LiteralPath $DetailedCsv -NoTypeInformation -Encoding UTF8
$script:ExecutiveRows | Sort-Object order |
    Export-Csv -LiteralPath $ExecutiveCsv -NoTypeInformation -Encoding UTF8

$LatestExecutive = Join-Path $LogRoot "purchase_order_summary_latest.csv"
$LatestDetailed = Join-Path $LogRoot "purchase_order_summary_detailed_latest.csv"
Copy-Item -LiteralPath $ExecutiveCsv -Destination $LatestExecutive -Force
Copy-Item -LiteralPath $DetailedCsv -Destination $LatestDetailed -Force

Write-Log "Executive summary: $ExecutiveCsv"
Write-Log "Detailed summary: $DetailedCsv"
Write-Log "Decision: $Decision"
Write-Log "Diagnostics completed"

Write-Host ""
Write-Host "Diagnostics complete."
Write-Host "Executive summary: $ExecutiveCsv"
Write-Host "Detailed summary:  $DetailedCsv"
Write-Host "Latest summary:    $LatestExecutive"
Write-Host "Status:            $Decision"
