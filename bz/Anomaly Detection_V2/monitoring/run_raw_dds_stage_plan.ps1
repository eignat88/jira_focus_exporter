#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$HostName = 'localhost',
    [int]$Port = 5432,
    [string]$Database = 'wms_analysis',
    [string]$UserName = 'postgres',
    [string]$PsqlPath = 'C:\Program Files\PostgreSQL\17\bin\psql.exe',
    [switch]$ExecuteSalesOrderFull,
    [int]$SalesOrderBatchSize = 100000,
    [int]$MinimumFreeSpaceGB = 100
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$LogDirectory = Join-Path $ProjectRoot "logs\raw_dds_stage_plan_$Timestamp"
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null

if (-not (Test-Path -LiteralPath $PsqlPath)) {
    throw "psql.exe not found: $PsqlPath"
}

function Invoke-PsqlFile {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Path
    )
    Write-Host "`n=== $Name ==="
    $LogPath = Join-Path $LogDirectory "$Name.txt"
    & $PsqlPath -X -h $HostName -p $Port -U $UserName -d $Database `
        -v ON_ERROR_STOP=1 -f $Path 2>&1 | Tee-Object -FilePath $LogPath
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed. See $LogPath"
    }
}

function Invoke-PsqlScalar {
    param([Parameter(Mandatory)][string]$Sql)
    $Output = & $PsqlPath -X -h $HostName -p $Port -U $UserName -d $Database `
        -v ON_ERROR_STOP=1 -A -t -q -c $Sql 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw (($Output | ForEach-Object { [string]$_ }) -join "`n")
    }
    return (($Output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Invoke-PythonStage {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string[]]$Arguments
    )
    Write-Host "`n=== $Name ==="
    $LogPath = Join-Path $LogDirectory "$Name.txt"
    & python -m ax_to_postgres_etl.pipelines.dds_cli @Arguments 2>&1 |
        Tee-Object -FilePath $LogPath
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed. See $LogPath"
    }
}

function Assert-SafeRuntime {
    Write-Host "`n=== safety_gate ==="
    $Safety = Invoke-PsqlScalar @"
SELECT concat_ws('|',
    (SELECT count(*) FROM etl.load_run WHERE status IN ('created','running')),
    (SELECT count(*) FROM pg_stat_progress_create_index
     WHERE relid IN ('raw_ax.salestable'::regclass, 'dds.sales_order'::regclass)),
    (SELECT count(*) FROM pg_stat_progress_vacuum
     WHERE relid IN ('raw_ax.salestable'::regclass, 'dds.sales_order'::regclass)),
    (SELECT count(*) FROM pg_stat_activity
     WHERE datname=current_database() AND pid<>pg_backend_pid()
       AND xact_start IS NOT NULL AND now()-xact_start > interval '5 minutes')
);
"@
    $Parts = $Safety.Split('|')
    if ($Parts.Count -ne 4) { throw "Unexpected safety result: $Safety" }

    $ActiveRuns = [int]$Parts[0]
    $IndexBuilds = [int]$Parts[1]
    $Vacuums = [int]$Parts[2]
    $LongTransactions = [int]$Parts[3]
    Write-Host "active_etl_runs=$ActiveRuns index_builds=$IndexBuilds vacuums=$Vacuums long_transactions=$LongTransactions"

    if ($ActiveRuns -gt 0 -or $IndexBuilds -gt 0 -or $Vacuums -gt 0 -or $LongTransactions -gt 0) {
        throw 'Safety gate blocked the modifying run.'
    }

    $DataDirectory = Invoke-PsqlScalar "SHOW data_directory;"
    $DriveName = [IO.Path]::GetPathRoot($DataDirectory).TrimEnd('\').TrimEnd(':')
    if ($DriveName) {
        $Drive = Get-PSDrive -Name $DriveName
        $FreeGB = [math]::Round($Drive.Free / 1GB, 2)
        Write-Host "data_directory=$DataDirectory free_gb=$FreeGB"
        if ($FreeGB -lt $MinimumFreeSpaceGB) {
            throw "Only $FreeGB GB free; required minimum is $MinimumFreeSpaceGB GB."
        }
    }
}

$RuntimeSql = Join-Path $LogDirectory 'runtime_baseline.sql'
@"
\set ON_ERROR_STOP on
\pset pager off
BEGIN READ ONLY;
SELECT pid, application_name, state, wait_event_type, wait_event,
       now()-xact_start AS xact_age, now()-query_start AS query_age,
       left(query, 300) AS query
FROM pg_stat_activity
WHERE datname=current_database() AND pid<>pg_backend_pid()
ORDER BY query_start;
SELECT * FROM pg_stat_progress_create_index;
SELECT * FROM pg_stat_progress_vacuum;
SELECT schemaname, relname, n_live_tup, n_dead_tup,
       last_analyze, last_autoanalyze, last_vacuum, last_autovacuum
FROM pg_stat_user_tables
WHERE (schemaname, relname) IN (
  ('raw_ax','purchtable'), ('dds','purchase_order'),
  ('raw_ax','salestable'), ('dds','sales_order'),
  ('raw_ax','wmspickingroute'), ('dds','picking_route'),
  ('raw_ax','lfl_scspacktask'), ('dds','pack_task')
)
ORDER BY schemaname, relname;
SELECT wal_records, wal_fpi, wal_bytes, wal_buffers_full, stats_reset
FROM pg_stat_wal;
SELECT * FROM pg_stat_checkpointer;
ROLLBACK;
"@ | Set-Content -LiteralPath $RuntimeSql -Encoding UTF8

Push-Location $ProjectRoot
try {
    Invoke-PsqlFile '00_runtime_baseline' $RuntimeSql
    Invoke-PsqlFile '01_purchase_order_reconciliation' (
        Join-Path $ProjectRoot 'monitoring\reconcile_purchase_order.sql'
    )

    Invoke-PythonStage '02_sales_order_preflight' @(
        '--mode', 'preflight', '--stage', 'sales_order',
        '--batch-size', "$SalesOrderBatchSize", '--count-mode', 'estimate'
    )

    if ($ExecuteSalesOrderFull) {
        Assert-SafeRuntime
        Invoke-PythonStage '03_sales_order_full' @(
            '--mode', 'full', '--stage', 'sales_order',
            '--batch-size', "$SalesOrderBatchSize", '--count-mode', 'estimate'
        )
    }
    else {
        Write-Warning 'sales_order full was not executed. Re-run with -ExecuteSalesOrderFull after reviewing the safety baseline.'
    }

    Invoke-PythonStage '04_sales_order_validate_only' @(
        '--mode', 'validate-only', '--stage', 'sales_order',
        '--batch-size', "$SalesOrderBatchSize", '--count-mode', 'estimate'
    )
    Invoke-PsqlFile '05_sales_order_reconciliation' (
        Join-Path $ProjectRoot 'monitoring\reconcile_sales_order.sql'
    )

    Invoke-PythonStage '06_picking_route_validate_only' @(
        '--mode', 'validate-only', '--stage', 'picking_route',
        '--batch-size', '100000', '--count-mode', 'estimate'
    )
    Invoke-PsqlFile '07_picking_route_reconciliation' (
        Join-Path $ProjectRoot 'monitoring\reconcile_picking_route.sql'
    )

    Invoke-PythonStage '08_pack_task_validate_only' @(
        '--mode', 'validate-only', '--stage', 'pack_task',
        '--batch-size', '100000', '--count-mode', 'estimate'
    )
    Invoke-PsqlFile '09_pack_task_reconciliation' (
        Join-Path $ProjectRoot 'monitoring\reconcile_pack_task.sql'
    )

    Invoke-PythonStage '10_full_preflight_baseline' @(
        '--mode', 'preflight', '--batch-size', '100000', '--count-mode', 'estimate'
    )
}
finally {
    Pop-Location
}

Write-Host "`nCompleted. Logs: $LogDirectory"
