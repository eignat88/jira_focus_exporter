param(
    [int]$TargetPid = 21440,
    [int]$IntervalSeconds = 15,
    [int]$DurationMinutes = 0,
    [string]$ProjectRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2",
    [string]$OutputFile = "",
    [string]$PsqlPath = "C:\Program Files\PostgreSQL\17\bin\psql.exe",
    [string]$HostName = "localhost",
    [int]$Port = 5432,
    [string]$Database = "wms_analysis",
    [string]$UserName = "postgres"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PsqlPath)) {
    $psqlCommand = Get-Command psql.exe -ErrorAction SilentlyContinue
    if ($psqlCommand) {
        $PsqlPath = $psqlCommand.Source
    }
    else {
        throw "psql.exe not found. Checked: $PsqlPath"
    }
}

if ([string]::IsNullOrWhiteSpace($OutputFile)) {
    $dataDir = Join-Path $ProjectRoot "data"
    if (-not (Test-Path $dataDir)) {
        New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    }

    $OutputFile = Join-Path `
        $dataDir `
        ("wmspickingroute_update_monitor_{0}.csv" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
}
else {
    $outputDir = Split-Path $OutputFile -Parent
    if ($outputDir -and -not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    }
}

# Load DB_PASSWORD from project .env when PGPASSWORD is not already set.
if ([string]::IsNullOrWhiteSpace($env:PGPASSWORD)) {
    $envFile = Join-Path $ProjectRoot ".env"

    if (Test-Path $envFile) {
        $passwordLine = Get-Content $envFile |
            Where-Object { $_ -match '^\s*DB_PASSWORD\s*=' } |
            Select-Object -First 1

        if ($passwordLine) {
            $password = ($passwordLine -split "=", 2)[1].Trim().Trim('"').Trim("'")
            if (-not [string]::IsNullOrWhiteSpace($password)) {
                $env:PGPASSWORD = $password
            }
        }
    }
}

$header = @(
    "timestamp",
    "pid",
    "state",
    "wait_event_type",
    "wait_event",
    "query_seconds",
    "wal_records",
    "wal_bytes",
    "wal_buffers_full",
    "heap_bytes",
    "index_bytes",
    "total_bytes"
) -join ","

Set-Content -Path $OutputFile -Value $header -Encoding UTF8

$startedAt = Get-Date
$sampleNo = 0

Write-Host "============================================================"
Write-Host "WMSPICKINGROUTE UPDATE MONITOR"
Write-Host "PID:       $TargetPid"
Write-Host "Interval:  $IntervalSeconds sec"
Write-Host "Duration:  $(if ($DurationMinutes -gt 0) { "$DurationMinutes min" } else { "until Ctrl+C or PID ends" })"
Write-Host "CSV:       $OutputFile"
Write-Host "Stop:      Ctrl+C"
Write-Host "============================================================"

while ($true) {
    $sampleStartedAt = Get-Date
    $sampleNo++

    if ($DurationMinutes -gt 0) {
        $elapsedMinutes = ((Get-Date) - $startedAt).TotalMinutes
        if ($elapsedMinutes -ge $DurationMinutes) {
            Write-Host "Duration reached: $DurationMinutes min"
            break
        }
    }

    $sql = @"
SELECT
    to_char(clock_timestamp(), 'YYYY-MM-DD HH24:MI:SS.MS TZH:TZM') AS timestamp,
    a.pid,
    COALESCE(a.state, ''),
    COALESCE(a.wait_event_type, ''),
    COALESCE(a.wait_event, ''),
    EXTRACT(EPOCH FROM (clock_timestamp() - a.query_start))::bigint AS query_seconds,
    w.wal_records,
    w.wal_bytes,
    w.wal_buffers_full,
    pg_relation_size('raw_ax.wmspickingroute') AS heap_bytes,
    pg_indexes_size('raw_ax.wmspickingroute') AS index_bytes,
    pg_total_relation_size('raw_ax.wmspickingroute') AS total_bytes
FROM pg_stat_activity AS a
CROSS JOIN pg_stat_wal AS w
WHERE a.pid = $TargetPid;
"@

    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    $result = & $PsqlPath `
        -h $HostName `
        -p $Port `
        -U $UserName `
        -d $Database `
        -X `
        -q `
        -A `
        -t `
        -F "|" `
        -v ON_ERROR_STOP=1 `
        -c $sql 2>&1

    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldErrorActionPreference

    if ($exitCode -ne 0) {
        Write-Host ""
        Write-Host "psql error, ExitCode=$exitCode"
        $result | ForEach-Object { Write-Host $_ }
        throw "Monitoring query failed."
    }

    $line = $result |
        Where-Object { $_ -match '^\d{4}-\d{2}-\d{2} ' -and $_ -match '\|' } |
        Select-Object -Last 1

    if (-not $line) {
        Write-Host ""
        Write-Host "PID $TargetPid is no longer present in pg_stat_activity."
        Write-Host "Monitoring stopped."
        break
    }

    $parts = $line.Trim() -split "\|", 12

    if ($parts.Count -ne 12) {
        Write-Host "Unexpected result: $line"
        throw "Unexpected number of columns returned by psql."
    }

    $escaped = foreach ($value in $parts) {
        '"' + ($value -replace '"', '""') + '"'
    }

    Add-Content `
        -Path $OutputFile `
        -Value ($escaped -join ",") `
        -Encoding UTF8

    Write-Host (
        "[{0}] sample={1} pid={2} state={3} wait={4}/{5} query={6}s wal_bytes={7} total_bytes={8}" -f `
        $parts[0],
        $sampleNo,
        $parts[1],
        $parts[2],
        $parts[3],
        $parts[4],
        $parts[5],
        $parts[7],
        $parts[11]
    )

    $sampleElapsedSeconds = ((Get-Date) - $sampleStartedAt).TotalSeconds
    $sleepSeconds = [math]::Max(
        0,
        $IntervalSeconds - [math]::Ceiling($sampleElapsedSeconds)
    )

    if ($sleepSeconds -gt 0) {
        Start-Sleep -Seconds $sleepSeconds
    }
}

Write-Host ""
Write-Host "============================================================"
Write-Host "MONITORING FINISHED"
Write-Host "Samples: $sampleNo"
Write-Host "CSV:     $OutputFile"
Write-Host "============================================================"
