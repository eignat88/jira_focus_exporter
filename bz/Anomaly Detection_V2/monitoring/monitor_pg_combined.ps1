[CmdletBinding()]
param(
    [string]$PsqlPath = "C:\Program Files\PostgreSQL\17\bin\psql.exe",
    [string]$HostName = "localhost",
    [ValidateRange(1, 65535)]
    [int]$Port = 5432,
    [string]$Database = "wms_analysis",
    [string]$User = "postgres",
    [string]$TableName = "raw_ax.alk_markserial",
    [int]$PidToMonitor = 21808,
    [ValidateRange(1, 86400)]
    [int]$IntervalSeconds = 5,
    [string]$OutputDir = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\data",
    [switch]$StopWhenVacuumFinished,
    [ValidateRange(1, 100)]
    [int]$NotFoundChecksBeforeStop = 2,
    [ValidateRange(1, 100)]
    [int]$MaxConsecutiveErrors = 10
)

$ErrorActionPreference = "Stop"

function Write-Log {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $Path -Value $line -Encoding UTF8
}

function Convert-ToNullableDouble {
    param([object]$Value)

    if ($null -eq $Value) {
        return $null
    }

    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $null
    }

    $result = 0.0
    if ([double]::TryParse(
        $text,
        [System.Globalization.NumberStyles]::Any,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$result
    )) {
        return $result
    }

    return $null
}

function Get-Delta {
    param(
        [Nullable[double]]$Current,
        [Nullable[double]]$Previous
    )

    if ($null -eq $Current -or $null -eq $Previous) {
        return $null
    }

    $delta = $Current - $Previous

    # Statistics may have been reset.
    if ($delta -lt 0) {
        return $null
    }

    return $delta
}

if (-not (Test-Path -LiteralPath $PsqlPath -PathType Leaf)) {
    throw "psql.exe not found: $PsqlPath"
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$safeTableName = ($TableName -replace '[^a-zA-Z0-9_.-]', '_') -replace '\.', '_'

$csvFile = Join-Path $OutputDir "pg_combined_monitor_${safeTableName}_${timestamp}.csv"
$errorFile = Join-Path $OutputDir "pg_combined_monitor_${safeTableName}_${timestamp}_errors.log"

$escapedTableName = $TableName.Replace("'", "''")

$query = @"
WITH requested AS (
    SELECT '$escapedTableName'::regclass AS relid
),
activity AS (
    SELECT
        a.pid,
        a.backend_type,
        a.datname,
        a.usename,
        a.application_name,
        a.state,
        a.wait_event_type,
        a.wait_event,
        a.backend_start,
        a.xact_start,
        a.query_start,
        EXTRACT(EPOCH FROM (clock_timestamp() - a.query_start))::numeric(20,3)
            AS query_runtime_seconds,
        EXTRACT(EPOCH FROM (clock_timestamp() - a.xact_start))::numeric(20,3)
            AS xact_runtime_seconds,
        COALESCE(array_to_string(pg_blocking_pids(a.pid), ';'), '') AS blocking_pids,
        replace(
            replace(left(a.query, 500), chr(13), ' '),
            chr(10),
            ' '
        ) AS current_query
    FROM pg_stat_activity AS a
    WHERE a.pid = $PidToMonitor
),
vacuum_progress AS (
    SELECT
        p.pid,
        p.relid,
        p.phase,
        p.heap_blks_total,
        p.heap_blks_scanned,
        p.heap_blks_vacuumed,
        CASE
            WHEN p.heap_blks_total > 0
            THEN round(p.heap_blks_scanned * 100.0 / p.heap_blks_total, 2)
        END AS scan_percent,
        CASE
            WHEN p.heap_blks_total > 0
            THEN round(p.heap_blks_vacuumed * 100.0 / p.heap_blks_total, 2)
        END AS vacuum_percent,
        p.index_vacuum_count,
        p.max_dead_tuple_bytes,
        p.dead_tuple_bytes,
        p.num_dead_item_ids
    FROM pg_stat_progress_vacuum AS p
    INNER JOIN requested AS r
        ON r.relid = p.relid
),
table_stats AS (
    SELECT
        s.relid,
        s.n_live_tup,
        s.n_dead_tup,
        s.n_mod_since_analyze,
        s.last_vacuum,
        s.last_autovacuum,
        s.vacuum_count,
        s.autovacuum_count,
        s.last_analyze,
        s.last_autoanalyze,
        s.analyze_count,
        s.autoanalyze_count
    FROM pg_stat_user_tables AS s
    INNER JOIN requested AS r
        ON r.relid = s.relid
),
wal_stats AS (
    SELECT
        wal_records,
        wal_fpi,
        wal_bytes,
        wal_buffers_full,
        wal_write,
        wal_sync,
        wal_write_time,
        wal_sync_time,
        stats_reset
    FROM pg_stat_wal
),
checkpointer_stats AS (
    SELECT
        num_timed,
        num_requested,
        restartpoints_timed,
        restartpoints_req,
        restartpoints_done,
        write_time,
        sync_time,
        buffers_written,
        stats_reset
    FROM pg_stat_checkpointer
)
SELECT
    to_char(clock_timestamp(), 'YYYY-MM-DD HH24:MI:SS.MS') AS sampled_at,
    r.relid::regclass::text AS table_name,

    CASE WHEN vp.pid IS NULL THEN 'NOT_FOUND' ELSE 'RUNNING' END AS vacuum_status,
    vp.pid AS vacuum_pid,
    vp.phase,
    vp.heap_blks_total,
    vp.heap_blks_scanned,
    vp.heap_blks_vacuumed,
    vp.scan_percent,
    vp.vacuum_percent,
    vp.index_vacuum_count,
    vp.max_dead_tuple_bytes,
    vp.dead_tuple_bytes,
    vp.num_dead_item_ids,

    COALESCE(a.pid, $PidToMonitor) AS monitored_pid,
    COALESCE(a.state, 'NOT_FOUND') AS pid_state,
    a.backend_type,
    a.wait_event_type,
    a.wait_event,
    a.query_runtime_seconds,
    a.xact_runtime_seconds,
    a.blocking_pids,
    a.backend_start,
    a.query_start,
    a.current_query,

    ts.n_live_tup,
    ts.n_dead_tup,
    ts.n_mod_since_analyze,
    ts.last_vacuum,
    ts.last_autovacuum,
    ts.vacuum_count,
    ts.autovacuum_count,
    ts.last_analyze,
    ts.last_autoanalyze,
    ts.analyze_count,
    ts.autoanalyze_count,

    ws.wal_records,
    ws.wal_fpi,
    ws.wal_bytes,
    ws.wal_buffers_full,
    ws.wal_write,
    ws.wal_sync,
    ws.wal_write_time,
    ws.wal_sync_time,
    ws.stats_reset AS wal_stats_reset,

    cs.num_timed AS checkpoints_timed,
    cs.num_requested AS checkpoints_requested,
    cs.restartpoints_timed,
    cs.restartpoints_req,
    cs.restartpoints_done,
    cs.write_time AS checkpoint_write_time_ms,
    cs.sync_time AS checkpoint_sync_time_ms,
    cs.buffers_written AS checkpoint_buffers_written,
    cs.stats_reset AS checkpointer_stats_reset
FROM requested AS r
LEFT JOIN vacuum_progress AS vp
    ON vp.relid = r.relid
LEFT JOIN activity AS a
    ON a.pid = $PidToMonitor
LEFT JOIN table_stats AS ts
    ON ts.relid = r.relid
CROSS JOIN wal_stats AS ws
CROSS JOIN checkpointer_stats AS cs;
"@

$columnNames = @(
    "sampled_at",
    "table_name",
    "vacuum_status",
    "vacuum_pid",
    "phase",
    "heap_blks_total",
    "heap_blks_scanned",
    "heap_blks_vacuumed",
    "scan_percent",
    "vacuum_percent",
    "index_vacuum_count",
    "max_dead_tuple_bytes",
    "dead_tuple_bytes",
    "num_dead_item_ids",
    "monitored_pid",
    "pid_state",
    "backend_type",
    "wait_event_type",
    "wait_event",
    "query_runtime_seconds",
    "xact_runtime_seconds",
    "blocking_pids",
    "backend_start",
    "query_start",
    "current_query",
    "n_live_tup",
    "n_dead_tup",
    "n_mod_since_analyze",
    "last_vacuum",
    "last_autovacuum",
    "vacuum_count",
    "autovacuum_count",
    "last_analyze",
    "last_autoanalyze",
    "analyze_count",
    "autoanalyze_count",
    "wal_records",
    "wal_fpi",
    "wal_bytes",
    "wal_buffers_full",
    "wal_write",
    "wal_sync",
    "wal_write_time",
    "wal_sync_time",
    "wal_stats_reset",
    "checkpoints_timed",
    "checkpoints_requested",
    "restartpoints_timed",
    "restartpoints_req",
    "restartpoints_done",
    "checkpoint_write_time_ms",
    "checkpoint_sync_time_ms",
    "checkpoint_buffers_written",
    "checkpointer_stats_reset"
)

$preflightQuery = @"
SELECT
    current_database() AS database_name,
    current_user AS user_name,
    '$escapedTableName'::regclass::text AS table_name,
    current_setting('server_version') AS server_version;
"@

$preflight = & $PsqlPath `
    "--host=$HostName" `
    "--port=$Port" `
    "--username=$User" `
    "--dbname=$Database" `
    "--no-psqlrc" `
    "--quiet" `
    "--tuples-only" `
    "--no-align" `
    "--set=ON_ERROR_STOP=1" `
    "--command=$preflightQuery" 2>> $errorFile

if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL preflight failed. See: $errorFile"
}

Write-Host ""
Write-Host "Combined PostgreSQL monitor started."
Write-Host "Table:    $TableName"
Write-Host "PID:      $PidToMonitor"
Write-Host "Interval: $IntervalSeconds seconds"
Write-Host "CSV:      $csvFile"
Write-Host "Errors:   $errorFile"
Write-Host "Stop:     Ctrl+C"
Write-Host ""

$previous = $null
$previousSampleTime = $null
$consecutiveErrors = 0
$consecutiveNotFound = 0
$sampleNumber = 0

try {
    while ($true) {
        $cycleStartedAt = Get-Date
        $tempErrorFile = Join-Path $env:TEMP "pg_combined_monitor_stderr_$PID.txt"

        try {
            Remove-Item -LiteralPath $tempErrorFile -Force -ErrorAction SilentlyContinue

            $result = & $PsqlPath `
                "--host=$HostName" `
                "--port=$Port" `
                "--username=$User" `
                "--dbname=$Database" `
                "--no-psqlrc" `
                "--quiet" `
                "--csv" `
                "--tuples-only" `
                "--set=ON_ERROR_STOP=1" `
                "--command=$query" 2> $tempErrorFile

            $exitCode = $LASTEXITCODE

            if ($exitCode -ne 0) {
                $consecutiveErrors++

                $errorText = ""
                if (Test-Path -LiteralPath $tempErrorFile) {
                    $errorText = Get-Content -LiteralPath $tempErrorFile -Raw -ErrorAction SilentlyContinue
                }

                $message = "psql failed with exit code $exitCode."
                if (-not [string]::IsNullOrWhiteSpace($errorText)) {
                    $message += " $($errorText.Trim())"
                }

                Write-Warning $message
                Write-Log -Path $errorFile -Message $message

                if ($consecutiveErrors -ge $MaxConsecutiveErrors) {
                    throw "Maximum consecutive errors reached: $MaxConsecutiveErrors."
                }
            }
            else {
                $consecutiveErrors = 0

                $rows = @(
                    $result |
                        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
                )

                if ($rows.Count -ne 1) {
                    throw "Expected one CSV row, received $($rows.Count)."
                }

                $current = $rows[0] | ConvertFrom-Csv -Header $columnNames
                $sampleNumber++

                $currentSampleTime = [datetime]::ParseExact(
                    $current.sampled_at,
                    "yyyy-MM-dd HH:mm:ss.fff",
                    [System.Globalization.CultureInfo]::InvariantCulture
                )

                $elapsedSeconds = $null
                if ($null -ne $previousSampleTime) {
                    $elapsedSeconds = ($currentSampleTime - $previousSampleTime).TotalSeconds
                    if ($elapsedSeconds -le 0) {
                        $elapsedSeconds = $null
                    }
                }

                $walBytes = Convert-ToNullableDouble $current.wal_bytes
                $walRecords = Convert-ToNullableDouble $current.wal_records
                $walBuffersFull = Convert-ToNullableDouble $current.wal_buffers_full
                $heapScanned = Convert-ToNullableDouble $current.heap_blks_scanned
                $heapVacuumed = Convert-ToNullableDouble $current.heap_blks_vacuumed
                $checkpointBuffers = Convert-ToNullableDouble $current.checkpoint_buffers_written
                $checkpointRequested = Convert-ToNullableDouble $current.checkpoints_requested
                $checkpointTimed = Convert-ToNullableDouble $current.checkpoints_timed

                $deltaWalBytes = $null
                $deltaWalRecords = $null
                $deltaWalBuffersFull = $null
                $deltaHeapScanned = $null
                $deltaHeapVacuumed = $null
                $deltaCheckpointBuffers = $null
                $deltaCheckpointRequested = $null
                $deltaCheckpointTimed = $null

                if ($null -ne $previous) {
                    $deltaWalBytes = Get-Delta `
                        -Current $walBytes `
                        -Previous (Convert-ToNullableDouble $previous.wal_bytes)

                    $deltaWalRecords = Get-Delta `
                        -Current $walRecords `
                        -Previous (Convert-ToNullableDouble $previous.wal_records)

                    $deltaWalBuffersFull = Get-Delta `
                        -Current $walBuffersFull `
                        -Previous (Convert-ToNullableDouble $previous.wal_buffers_full)

                    $deltaHeapScanned = Get-Delta `
                        -Current $heapScanned `
                        -Previous (Convert-ToNullableDouble $previous.heap_blks_scanned)

                    $deltaHeapVacuumed = Get-Delta `
                        -Current $heapVacuumed `
                        -Previous (Convert-ToNullableDouble $previous.heap_blks_vacuumed)

                    $deltaCheckpointBuffers = Get-Delta `
                        -Current $checkpointBuffers `
                        -Previous (Convert-ToNullableDouble $previous.checkpoint_buffers_written)

                    $deltaCheckpointRequested = Get-Delta `
                        -Current $checkpointRequested `
                        -Previous (Convert-ToNullableDouble $previous.checkpoints_requested)

                    $deltaCheckpointTimed = Get-Delta `
                        -Current $checkpointTimed `
                        -Previous (Convert-ToNullableDouble $previous.checkpoints_timed)
                }

                $walMbPerMinute = $null
                $walRecordsPerMinute = $null
                $heapScannedMbPerMinute = $null
                $heapVacuumedMbPerMinute = $null
                $checkpointMbPerMinute = $null

                if ($null -ne $elapsedSeconds) {
                    if ($null -ne $deltaWalBytes) {
                        $walMbPerMinute = [math]::Round(
                            ($deltaWalBytes / 1MB) * (60.0 / $elapsedSeconds),
                            3
                        )
                    }

                    if ($null -ne $deltaWalRecords) {
                        $walRecordsPerMinute = [math]::Round(
                            $deltaWalRecords * (60.0 / $elapsedSeconds),
                            3
                        )
                    }

                    if ($null -ne $deltaHeapScanned) {
                        $heapScannedMbPerMinute = [math]::Round(
                            (($deltaHeapScanned * 8192.0) / 1MB) * (60.0 / $elapsedSeconds),
                            3
                        )
                    }

                    if ($null -ne $deltaHeapVacuumed) {
                        $heapVacuumedMbPerMinute = [math]::Round(
                            (($deltaHeapVacuumed * 8192.0) / 1MB) * (60.0 / $elapsedSeconds),
                            3
                        )
                    }

                    if ($null -ne $deltaCheckpointBuffers) {
                        $checkpointMbPerMinute = [math]::Round(
                            (($deltaCheckpointBuffers * 8192.0) / 1MB) * (60.0 / $elapsedSeconds),
                            3
                        )
                    }
                }

                $outputRow = [ordered]@{
                    sample_number = $sampleNumber
                    elapsed_seconds = $elapsedSeconds

                    sampled_at = $current.sampled_at
                    table_name = $current.table_name

                    vacuum_status = $current.vacuum_status
                    vacuum_pid = $current.vacuum_pid
                    phase = $current.phase
                    heap_blks_total = $current.heap_blks_total
                    heap_blks_scanned = $current.heap_blks_scanned
                    heap_blks_vacuumed = $current.heap_blks_vacuumed
                    scan_percent = $current.scan_percent
                    vacuum_percent = $current.vacuum_percent
                    index_vacuum_count = $current.index_vacuum_count
                    max_dead_tuple_bytes = $current.max_dead_tuple_bytes
                    dead_tuple_bytes = $current.dead_tuple_bytes
                    num_dead_item_ids = $current.num_dead_item_ids

                    delta_heap_blks_scanned = $deltaHeapScanned
                    delta_heap_blks_vacuumed = $deltaHeapVacuumed
                    heap_scanned_mb_per_min = $heapScannedMbPerMinute
                    heap_vacuumed_mb_per_min = $heapVacuumedMbPerMinute

                    monitored_pid = $current.monitored_pid
                    pid_state = $current.pid_state
                    backend_type = $current.backend_type
                    wait_event_type = $current.wait_event_type
                    wait_event = $current.wait_event
                    query_runtime_seconds = $current.query_runtime_seconds
                    xact_runtime_seconds = $current.xact_runtime_seconds
                    blocking_pids = $current.blocking_pids
                    backend_start = $current.backend_start
                    query_start = $current.query_start
                    current_query = $current.current_query

                    n_live_tup = $current.n_live_tup
                    n_dead_tup = $current.n_dead_tup
                    n_mod_since_analyze = $current.n_mod_since_analyze
                    last_vacuum = $current.last_vacuum
                    last_autovacuum = $current.last_autovacuum
                    vacuum_count = $current.vacuum_count
                    autovacuum_count = $current.autovacuum_count
                    last_analyze = $current.last_analyze
                    last_autoanalyze = $current.last_autoanalyze
                    analyze_count = $current.analyze_count
                    autoanalyze_count = $current.autoanalyze_count

                    wal_records = $current.wal_records
                    wal_fpi = $current.wal_fpi
                    wal_bytes = $current.wal_bytes
                    wal_buffers_full = $current.wal_buffers_full
                    wal_write = $current.wal_write
                    wal_sync = $current.wal_sync
                    wal_write_time = $current.wal_write_time
                    wal_sync_time = $current.wal_sync_time
                    wal_stats_reset = $current.wal_stats_reset

                    delta_wal_bytes = $deltaWalBytes
                    delta_wal_records = $deltaWalRecords
                    delta_wal_buffers_full = $deltaWalBuffersFull
                    wal_mb_per_min = $walMbPerMinute
                    wal_records_per_min = $walRecordsPerMinute

                    checkpoints_timed = $current.checkpoints_timed
                    checkpoints_requested = $current.checkpoints_requested
                    restartpoints_timed = $current.restartpoints_timed
                    restartpoints_req = $current.restartpoints_req
                    restartpoints_done = $current.restartpoints_done
                    checkpoint_write_time_ms = $current.checkpoint_write_time_ms
                    checkpoint_sync_time_ms = $current.checkpoint_sync_time_ms
                    checkpoint_buffers_written = $current.checkpoint_buffers_written
                    checkpointer_stats_reset = $current.checkpointer_stats_reset

                    delta_checkpoint_buffers_written = $deltaCheckpointBuffers
                    delta_checkpoints_requested = $deltaCheckpointRequested
                    delta_checkpoints_timed = $deltaCheckpointTimed
                    checkpoint_mb_per_min = $checkpointMbPerMinute
                }

                $rowObject = [pscustomobject]$outputRow

                if (-not (Test-Path -LiteralPath $csvFile)) {
                    $rowObject |
                        Export-Csv `
                            -LiteralPath $csvFile `
                            -NoTypeInformation `
                            -Encoding UTF8
                }
                else {
                    $rowObject |
                        Export-Csv `
                            -LiteralPath $csvFile `
                            -NoTypeInformation `
                            -Encoding UTF8 `
                            -Append
                }

                if ($current.vacuum_status -eq "NOT_FOUND") {
                    $consecutiveNotFound++
                }
                else {
                    $consecutiveNotFound = 0
                }

                $consoleLine = (
                    "{0} | vacuum={1} | phase={2} | scan={3}% | heap={4}% | WAL={5} MB/min | wait={6}/{7}"
                ) -f `
                    $current.sampled_at, `
                    $current.vacuum_status, `
                    $current.phase, `
                    $current.scan_percent, `
                    $current.vacuum_percent, `
                    $walMbPerMinute, `
                    $current.wait_event_type, `
                    $current.wait_event

                Write-Host $consoleLine

                $previous = $current
                $previousSampleTime = $currentSampleTime

                if (
                    $StopWhenVacuumFinished -and
                    $consecutiveNotFound -ge $NotFoundChecksBeforeStop
                ) {
                    Write-Host ""
                    Write-Host "VACUUM is no longer present in pg_stat_progress_vacuum."
                    break
                }
            }
        }
        catch {
            Write-Log -Path $errorFile -Message $_.Exception.Message
            throw
        }
        finally {
            Remove-Item -LiteralPath $tempErrorFile -Force -ErrorAction SilentlyContinue
        }

        $cycleDurationSeconds = ((Get-Date) - $cycleStartedAt).TotalSeconds
        $sleepSeconds = $IntervalSeconds - $cycleDurationSeconds

        if ($sleepSeconds -gt 0) {
            Start-Sleep -Milliseconds ([int]($sleepSeconds * 1000))
        }
    }
}
finally {
    Write-Host ""
    Write-Host "Combined PostgreSQL monitor stopped."

    if (Test-Path -LiteralPath $csvFile) {
        Write-Host "CSV:"
        Write-Host $csvFile
    }

    if (Test-Path -LiteralPath $errorFile) {
        Write-Host "Error log:"
        Write-Host $errorFile
    }

    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
