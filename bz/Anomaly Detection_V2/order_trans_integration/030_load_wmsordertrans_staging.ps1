#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$HostName = 'localhost',
    [int]$Port = 5432,
    [string]$Database = 'wms_analysis',
    [string]$UserName = 'postgres',
    [string]$PgPassword = '123',
    [string]$PsqlPath = 'C:\Program Files\PostgreSQL\17\bin\psql.exe',
    [int]$BatchSize = 500000,
    [string]$OutputDirectory = 'D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\logs\2',
    [string]$CheckpointFile = 'D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\logs\2\wmsordertrans_staging_checkpoint.json',
    [int]$StatementTimeoutMinutes = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $PsqlPath)) { throw "psql.exe not found: $PsqlPath" }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$csvPath = Join-Path $OutputDirectory "load_wmsordertrans_staging_$timestamp.csv"
$rows = [System.Collections.Generic.List[object]]::new()

function Invoke-PsqlText {
    param([Parameter(Mandatory)][string]$Sql)
    $temp = Join-Path ([IO.Path]::GetTempPath()) ("wmsordertrans_{0}.sql" -f ([guid]::NewGuid().ToString('N')))
    [IO.File]::WriteAllText($temp, $Sql, [Text.UTF8Encoding]::new($false))
    if ($PgPassword) { $env:PGPASSWORD = $PgPassword }
    try {
        $out = & $PsqlPath -X -h $HostName -p $Port -U $UserName -d $Database `
            -v ON_ERROR_STOP=1 -A -t -q -f $temp 2>&1
        if ($LASTEXITCODE -ne 0) { throw (($out | ForEach-Object {[string]$_}) -join "`n") }
        return (($out | ForEach-Object {[string]$_}) -join "`n").Trim()
    }
    finally {
        Remove-Item $temp -Force -ErrorAction SilentlyContinue
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
}

function Save-Checkpoint([string]$LastRecid, [long]$TotalRows) {
    [pscustomobject]@{
        last_recid = $LastRecid
        total_rows = $TotalRows
        updated_at = (Get-Date).ToString('o')
        batch_size = $BatchSize
    } | ConvertTo-Json | Set-Content -LiteralPath $CheckpointFile -Encoding UTF8
}

# Mandatory safety gate: staging objects must exist.
$objects = Invoke-PsqlText @"
BEGIN READ ONLY;
SET LOCAL statement_timeout='60s';
SELECT concat_ws('|',
    to_regclass('raw_ax.wmsordertrans')::text,
    to_regclass('stage_ax.wmsordertrans_normalized')::text,
    (SELECT data_type FROM information_schema.columns
     WHERE table_schema='raw_ax' AND table_name='wmsordertrans' AND column_name='recid')
);
ROLLBACK;
"@
if ($objects -notmatch '^raw_ax\.wmsordertrans\|stage_ax\.wmsordertrans_normalized\|text$') {
    throw "Preflight failed: $objects"
}

# Mandatory one-time exact gate. It may scan the existing RECID index,
# but it prevents incorrect resume/order when text values are not clean fixed-width numbers.
Write-Host 'Running exact RECID safety validation (read-only)...'
$quality = Invoke-PsqlText @"
BEGIN READ ONLY;
SET LOCAL statement_timeout='${StatementTimeoutMinutes}min';
SELECT concat_ws('|',
    count(*) FILTER (WHERE recid IS NULL),
    count(*) FILTER (WHERE recid IS NOT NULL AND btrim(recid) = ''),
    count(*) FILTER (WHERE recid IS NOT NULL AND btrim(recid) <> '' AND btrim(recid) !~ '^[0-9]+$'),
    count(*) FILTER (WHERE recid IS NOT NULL AND recid <> btrim(recid)),
    min(length(recid)),
    max(length(recid))
)
FROM raw_ax.wmsordertrans;
ROLLBACK;
"@
$qualityLine = ($quality -split "`r?`n" | Where-Object { $_ -match '^\d+\|\d+\|\d+\|\d+\|\d+\|\d+$' } | Select-Object -Last 1)
if (-not $qualityLine) { throw "Unexpected RECID validation result: $quality" }
$q = $qualityLine.Split('|')
$nullCount = [long]$q[0]
$emptyCount = [long]$q[1]
$nonNumericCount = [long]$q[2]
$trimMismatchCount = [long]$q[3]
$minLength = [int]$q[4]
$maxLength = [int]$q[5]
if ($nullCount -ne 0 -or $emptyCount -ne 0 -or $nonNumericCount -ne 0 -or $trimMismatchCount -ne 0 -or $minLength -ne $maxLength) {
    throw "RECID safety gate failed: null=$nullCount empty=$emptyCount non_numeric=$nonNumericCount spaces=$trimMismatchCount min_length=$minLength max_length=$maxLength. Text-key chunking is prohibited."
}
Write-Host "RECID safety gate passed: fixed length=$minLength, numeric, no spaces."

$lastRecid = ''
$totalRows = 0L
if (Test-Path -LiteralPath $CheckpointFile) {
    $state = Get-Content -LiteralPath $CheckpointFile -Raw | ConvertFrom-Json
    $lastRecid = [string]$state.last_recid
    $totalRows = [long]$state.total_rows
}

Write-Host "Starting staging load"
Write-Host "Checkpoint: '$lastRecid'"
Write-Host "Batch size: $BatchSize"
Write-Host "CSV: $csvPath"
Write-Host "Stop: Ctrl+C"

while ($true) {
    $started = Get-Date
    $escapedLast = $lastRecid.Replace("'", "''")

    $sql = @"
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='${StatementTimeoutMinutes}min';
WITH src AS (
    SELECT
        recid,
        orderid,
        inventtransid,
        itemid,
        inventdimid,
        qty,
        wms_givenqty,
        wms_defectqty,
        routeid,
        palletidpicked,
        modifieddatetime,
        createddatetime,
        dataareaid
    FROM raw_ax.wmsordertrans
    WHERE recid > '$escapedLast'
    ORDER BY recid
    LIMIT $BatchSize
), ins AS (
    INSERT INTO stage_ax.wmsordertrans_normalized
    (
        recid_bigint, source_recid, order_id, order_trans_id, item_id,
        invent_dim_id, qty, picked_qty, waste_qty, route_id, pallet_id,
        modified_datetime, created_datetime, data_area_id
    )
    SELECT
        btrim(recid)::bigint,
        recid,
        NULLIF(btrim(orderid), ''),
        NULLIF(btrim(inventtransid), ''),
        NULLIF(btrim(itemid), ''),
        NULLIF(btrim(inventdimid), ''),
        qty,
        wms_givenqty,
        wms_defectqty,
        NULLIF(btrim(routeid), ''),
        NULLIF(btrim(palletidpicked), ''),
        modifieddatetime,
        createddatetime,
        NULLIF(btrim(dataareaid), '')
    FROM src
    ON CONFLICT (recid_bigint) DO UPDATE
    SET source_recid = EXCLUDED.source_recid,
        order_id = EXCLUDED.order_id,
        order_trans_id = EXCLUDED.order_trans_id,
        item_id = EXCLUDED.item_id,
        invent_dim_id = EXCLUDED.invent_dim_id,
        qty = EXCLUDED.qty,
        picked_qty = EXCLUDED.picked_qty,
        waste_qty = EXCLUDED.waste_qty,
        route_id = EXCLUDED.route_id,
        pallet_id = EXCLUDED.pallet_id,
        modified_datetime = EXCLUDED.modified_datetime,
        created_datetime = EXCLUDED.created_datetime,
        data_area_id = EXCLUDED.data_area_id,
        loaded_at = clock_timestamp()
    RETURNING source_recid
)
SELECT concat_ws('|',
    (SELECT count(*) FROM src),
    COALESCE((SELECT max(recid) FROM src), ''),
    (SELECT count(*) FROM ins)
);
COMMIT;
"@

    try {
        $result = Invoke-PsqlText $sql
        $line = ($result -split "`r?`n" | Where-Object { $_ -match '^\d+\|' } | Select-Object -Last 1)
        if (-not $line) { throw "Unexpected psql result: $result" }
        $parts = $line.Split('|')
        $sourceRows = [long]$parts[0]
        $newLast = [string]$parts[1]
        $affectedRows = [long]$parts[2]

        if ($sourceRows -eq 0) { break }
        $totalRows += $sourceRows
        $elapsed = ((Get-Date) - $started).TotalSeconds
        $rows.Add([pscustomobject]@{
            timestamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
            from_recid = $lastRecid
            to_recid = $newLast
            source_rows = $sourceRows
            affected_rows = $affectedRows
            elapsed_seconds = [math]::Round($elapsed, 3)
            rows_per_second = if ($elapsed -gt 0) {[math]::Round($sourceRows/$elapsed, 2)} else {0}
            total_rows = $totalRows
            status = 'COMPLETED'
            error = ''
        }) | Out-Null
        $rows | Export-Csv -LiteralPath $csvPath -Delimiter ';' -NoTypeInformation -Encoding UTF8
        $lastRecid = $newLast
        Save-Checkpoint $lastRecid $totalRows
        Write-Host "Completed: rows=$sourceRows, checkpoint=$lastRecid, sec=$([math]::Round($elapsed,2))"
    }
    catch {
        $rows.Add([pscustomobject]@{
            timestamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
            from_recid = $lastRecid
            to_recid = ''
            source_rows = 0
            affected_rows = 0
            elapsed_seconds = [math]::Round(((Get-Date)-$started).TotalSeconds,3)
            rows_per_second = 0
            total_rows = $totalRows
            status = 'FAILED'
            error = $_.Exception.Message
        }) | Out-Null
        $rows | Export-Csv -LiteralPath $csvPath -Delimiter ';' -NoTypeInformation -Encoding UTF8
        throw
    }
}

Write-Host "Staging load completed. Total rows: $totalRows"
Write-Host "Checkpoint: $CheckpointFile"
Write-Host "CSV: $csvPath"
