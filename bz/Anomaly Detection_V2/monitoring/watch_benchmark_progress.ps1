#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$HostName = "localhost",
    [int]$Port = 5432,
    [string]$Database = "wms_analysis",
    [string]$UserName = "postgres",

    [string]$PsqlPath = "C:\Program Files\PostgreSQL\17\bin\psql.exe",

    [int]$RefreshSeconds = 10,

    [string]$ProjectDir =
        "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# UTF-8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# Load password from .env
if ([string]::IsNullOrWhiteSpace($env:PGPASSWORD)) {
    $envFile = Join-Path $ProjectDir ".env"
    if (Test-Path -LiteralPath $envFile) {
        $passwordLine = Get-Content -LiteralPath $envFile |
            Where-Object {
                $_ -match '^\s*DB_PASSWORD\s*=' -and
                $_ -notmatch '^\s*#'
            } |
            Select-Object -First 1
        if ($passwordLine) {
            $password = ($passwordLine -split '=', 2)[1].Trim()
            $password = $password.Trim('"').Trim("'")
            $env:PGPASSWORD = $password
        }
    }
}

function Invoke-Psql {
    param([string]$Sql)

    $arguments = @(
        "--host=$HostName"
        "--port=$Port"
        "--username=$UserName"
        "--dbname=$Database"
        "--no-psqlrc"
        "--tuples-only"
        "--no-align"
        "--quiet"
        "-c", $Sql
    )

    $stdout = & $PsqlPath @arguments 2>&1
    $exitCode = $LASTEXITCODE

    return [PSCustomObject]@{
        ExitCode = $exitCode
        StdOut   = ($stdout | Out-String).Trim()
    }
}

function Get-Number {
    param([string]$Text)
    $lines = $Text -split "\r?\n" |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -match '^\d+$' }
    if ($lines.Count -gt 0) {
        return [long]$lines[-1]
    }
    return $null
}

function Format-Number {
    param([long]$Number)
    return $Number.ToString("N0")
}

function Format-Size {
    param([long]$Bytes)
    if ($Bytes -gt 1TB) { return "{0:N2} TB" -f ($Bytes / 1TB) }
    if ($Bytes -gt 1GB) { return "{0:N2} GB" -f ($Bytes / 1GB) }
    if ($Bytes -gt 1MB) { return "{0:N2} MB" -f ($Bytes / 1MB) }
    if ($Bytes -gt 1KB) { return "{0:N2} KB" -f ($Bytes / 1KB) }
    return "$Bytes bytes"
}

# Previous snapshot for speed calculation
$prevCount = $null
$prevTime = $null

try {
    while ($true) {

        $now = Get-Date
        $header = $now.ToString("yyyy-MM-dd HH:mm:ss")

        # Clear screen
        Clear-Host

        Write-Host "============================================================"
        Write-Host " BENCHMARK ALK_MARKSERIAL - PROGRESS MONITOR"
        Write-Host " $header"
        Write-Host "============================================================"
        Write-Host ""

        # ----------------------------------------------------------
        # 1. Row counts
        # ----------------------------------------------------------

        $benchResult = Invoke-Psql -Sql "SELECT n_live_tup FROM pg_stat_user_tables WHERE schemaname = 'benchmark' AND relname = 'alk_markserial_test';"
        $benchCount = $null
        if ($benchResult.ExitCode -eq 0) {
            $benchCount = Get-Number -Text $benchResult.StdOut
        }

        $rawResult = Invoke-Psql -Sql "SELECT n_live_tup FROM pg_stat_user_tables WHERE schemaname = 'raw_ax' AND relname = 'alk_markserial';"
        $rawCount = $null
        if ($rawResult.ExitCode -eq 0) {
            $rawCount = Get-Number -Text $rawResult.StdOut
        }

        $ddsResult = Invoke-Psql -Sql "SELECT n_live_tup FROM pg_stat_user_tables WHERE schemaname = 'dds' AND relname = 'serial_mark';"
        $ddsCount = $null
        if ($ddsResult.ExitCode -eq 0) {
            $ddsCount = Get-Number -Text $ddsResult.StdOut
        }

        Write-Host "--- Row Counts ---"
        Write-Host ""

        if ($rawCount) {
            Write-Host ("  raw_ax.alk_markserial:       {0}" -f (Format-Number $rawCount))
        }
        if ($benchCount) {
            $pct = if ($rawCount -and $rawCount -gt 0) { [math]::Round(100 * $benchCount / $rawCount, 1) } else { 0 }
            Write-Host ("  benchmark.alk_markserial_test: {0}  ({1}%)" -f (Format-Number $benchCount), $pct)
        }
        if ($ddsCount) {
            Write-Host ("  dds.serial_mark:              {0}" -f (Format-Number $ddsCount))
        }

        # ----------------------------------------------------------
        # 2. Speed calculation
        # ----------------------------------------------------------

        Write-Host ""
        Write-Host "--- Speed ---"
        Write-Host ""

        if ($benchCount -and $prevCount -and $prevTime) {
            $elapsed = ($now - $prevTime).TotalSeconds
            if ($elapsed -gt 0) {
                $diff = $benchCount - $prevCount
                $speed = [math]::Round($diff / $elapsed, 0)
                $eta = if ($speed -gt 0 -and $rawCount) {
                    $remaining = $rawCount - $benchCount
                    $etaSeconds = $remaining / $speed
                    $ts = [TimeSpan]::FromSeconds($etaSeconds)
                    if ($ts.TotalHours -ge 1) {
                        "{0}h {1}m" -f [math]::Floor($ts.TotalHours), $ts.Minutes
                    } else {
                        "{0}m {1}s" -f $ts.Minutes, $ts.Seconds
                    }
                } else { "N/A" }

                Write-Host ("  Delta:    {0} rows" -f (Format-Number $diff))
                Write-Host ("  Speed:    {0} rows/s" -f (Format-Number $speed))
                Write-Host ("  ETA:      $eta")
            }
        }
        else {
            Write-Host "  Calculating..."
        }

        $prevCount = $benchCount
        $prevTime = $now

        # ----------------------------------------------------------
        # 3. Active queries
        # ----------------------------------------------------------

        Write-Host ""
        Write-Host "--- Active Queries ---"
        Write-Host ""

        $activeResult = Invoke-Psql -Sql @"
SELECT
    pid,
    state,
    wait_event_type,
    wait_event,
    EXTRACT(EPOCH FROM clock_timestamp() - query_start)::int AS runtime_sec,
    LEFT(query, 80)
FROM pg_stat_activity
WHERE state = 'active'
  AND pid <> pg_backend_pid()
  AND query NOT LIKE '%pg_stat_activity%'
ORDER BY query_start;
"@

        if ($activeResult.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($activeResult.StdOut)) {
            $activeLines = $activeResult.StdOut -split "\r?\n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
            foreach ($line in $activeLines) {
                $parts = $line -split '\|' | ForEach-Object { $_.Trim() }
                if ($parts.Count -ge 6) {
                    $pid = $parts[0]
                    $state = $parts[1]
                    $waitType = $parts[2]
                    $waitEvent = $parts[3]
                    $runtime = $parts[4]
                    $query = $parts[5]

                    $runtimeFmt = if ([int]$runtime -ge 3600) {
                        "{0}h{1}m" -f [math]::Floor([int]$runtime / 3600), ([int]$runtime % 3600 / 60)
                    } elseif ([int]$runtime -ge 60) {
                        "{0}m{1}s" -f [math]::Floor([int]$runtime / 60), ([int]$runtime % 60)
                    } else {
                        "${runtime}s"
                    }

                    Write-Host ("  PID {0} | {1} | {2}/{3} | {4}" -f $pid, $state, $waitType, $waitEvent, $runtimeFmt)
                    Write-Host ("    {0}" -f $query)
                }
            }
        }
        else {
            Write-Host "  (none)"
        }

        # ----------------------------------------------------------
        # 4. Table sizes
        # ----------------------------------------------------------

        Write-Host ""
        Write-Host "--- Table Sizes ---"
        Write-Host ""

        $sizeResult = Invoke-Psql -Sql @"
SELECT
    pg_size_pretty(pg_relation_size('benchmark.alk_markserial_test')) AS table_size,
    pg_size_pretty(pg_indexes_size('benchmark.alk_markserial_test')) AS index_size,
    pg_size_pretty(pg_total_relation_size('benchmark.alk_markserial_test')) AS total_size;
"@

        if ($sizeResult.ExitCode -eq 0) {
            $parts = $sizeResult.StdOut -split '\|' | ForEach-Object { $_.Trim() }
            if ($parts.Count -ge 3) {
                Write-Host ("  Table:  {0}" -f $parts[0])
                Write-Host ("  Index:  {0}" -f $parts[1])
                Write-Host ("  Total:  {0}" -f $parts[2])
            }
        }

        # ----------------------------------------------------------
        # 5. Last checkpoint
        # ----------------------------------------------------------

        Write-Host ""
        Write-Host "--- Last Chunk ---"
        Write-Host ""

        $statePath = Join-Path $ProjectDir "data\benchmark_alk_markserial_state.json"
        if (Test-Path -LiteralPath $statePath) {
            $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            Write-Host ("  Last completed: [{0}, {1})" -f $state.last_completed_from, $state.last_completed_to)
            Write-Host ("  Next checkpoint: {0}" -f $state.next_checkpoint)
            Write-Host ("  Inserted rows:  {0}" -f (Format-Number $state.inserted_rows))
            Write-Host ("  Completed at:   {0}" -f $state.completed_at)
        }
        else {
            Write-Host "  (no state file)"
        }

        # ----------------------------------------------------------
        # 6. Last CSV log entry
        # ----------------------------------------------------------

        Write-Host ""
        Write-Host "--- Last CSV Entry ---"
        Write-Host ""

        $csvDir = Join-Path $ProjectDir "data"
        $latestCsv = Get-ChildItem -Path $csvDir -Filter "benchmark_alk_markserial_resume_*.csv" |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1

        if ($latestCsv) {
            $lastLine = Get-Content -LiteralPath $latestCsv.FullName -Tail 1
            if ($lastLine) {
                $fields = $lastLine -split ',' | ForEach-Object { $_.Trim('"') }
                if ($fields.Count -ge 5) {
                    Write-Host ("  File:    {0}" -f $latestCsv.Name)
                    Write-Host ("  Time:    {0}" -f $fields[0])
                    Write-Host ("  Chunk:   [{0}, {1})" -f $fields[1], $fields[2])
                    Write-Host ("  Rows:    {0}" -f $fields[3])
                    Write-Host ("  Status:  {0}" -f $fields[7])
                }
            }
        }
        else {
            Write-Host "  (no CSV files)"
        }

        # ----------------------------------------------------------
        # Footer
        # ----------------------------------------------------------

        Write-Host ""
        Write-Host "============================================================"
        Write-Host " Refresh: ${RefreshSeconds}s | Ctrl+C to stop"
        Write-Host "============================================================"

        Start-Sleep -Seconds $RefreshSeconds
    }
}
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
