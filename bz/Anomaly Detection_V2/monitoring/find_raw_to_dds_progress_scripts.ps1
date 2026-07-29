param(
    [string]$ProjectRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2",
    [string]$OutputDir = "",
    [switch]$IncludeArchives
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    throw "Project directory not found: $ProjectRoot"
}

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $ProjectRoot "logs"
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$csvFile = Join-Path $OutputDir "raw_to_dds_progress_scripts_$timestamp.csv"
$txtFile = Join-Path $OutputDir "raw_to_dds_progress_scripts_$timestamp.txt"

# File extensions worth inspecting.
$extensions = @(
    ".ps1", ".py", ".sql", ".yaml", ".yml",
    ".md", ".txt", ".json", ".ini", ".toml"
)

# Direct indicators that a file monitors RAW -> DDS execution/progress.
$checks = [ordered]@{
    "RAW to DDS"          = "raw.?to.?dds|RAW\s*[-=]?>\s*DDS|RAW\s*→\s*DDS"
    "ETL load_run"        = "etl\.load_run|load_run"
    "ETL load_chunk"      = "etl\.load_chunk|load_chunk"
    "Completed chunks"    = "completed_chunks|total_chunks|failed_chunks|pending_chunks|running_chunks"
    "Chunk status"        = "chunk_id|chunk_no|chunk_status|status\s*=\s*['""]?(running|completed|failed|pending)"
    "Rows counters"       = "rows_read|rows_staged|rows_inserted|rows_updated|rows_conflicted|target_row_count|source_row_count"
    "Heartbeat"           = "heartbeat_at|heartbeat|stale run|stale chunk"
    "Progress percent"    = "progress_pct|progress_percent|percent_complete|percentage|%\s*(complete|loaded)|loaded_pct"
    "ETA and speed"       = "rows_per_second|rows/sec|MB/min|elapsed|ETA|estimated.*time|speed"
    "Target row count"    = "COUNT\(\*\).*dds\.|pg_stat_user_tables|n_live_tup|reltuples"
    "DDS stage"           = "--stage|stage_name|picking_route|serial_mark|pack_task|order_trans"
    "Pipeline status"     = "--mode\s+status|mode.?status|PIPELINE STATUS|dds_cli"
    "Activity monitor"    = "pg_stat_activity|application_name|wait_event|query_age|transaction_age"
    "WAL monitor"         = "pg_stat_wal|wal_bytes|wal_records|wal_buffers_full"
    "Resume/checkpoint"   = "resume|checkpoint|last_processed_key|range_start_bigint|range_end_bigint"
    "CSV/log output"      = "Export-Csv|Set-Content|Add-Content|Tee-Object|csvFile|logFile|logs\\"
}

$excludeDirs = @(
    "\.git\", "\.venv\", "\venv\", "\__pycache__\",
    "\node_modules\", "\.pytest_cache\", "\.mypy_cache\",
    "\data\"
)

if (-not $IncludeArchives) {
    $excludeDirs += "\archive\"
}

$files = Get-ChildItem -LiteralPath $ProjectRoot -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $extensions -contains $_.Extension.ToLowerInvariant()
    } |
    Where-Object {
        $full = $_.FullName.ToLowerInvariant()
        -not ($excludeDirs | Where-Object { $full.Contains($_.ToLowerInvariant()) })
    }

Write-Host ("=" * 90)
Write-Host "SEARCH FOR RAW -> DDS PROGRESS / MONITORING SCRIPTS"
Write-Host "Project: $ProjectRoot"
Write-Host "Files to inspect: $($files.Count)"
Write-Host ("=" * 90)

$results = foreach ($file in $files) {
    try {
        $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 -ErrorAction Stop
    }
    catch {
        try {
            $content = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction Stop
        }
        catch {
            continue
        }
    }

    $matchedChecks = New-Object System.Collections.Generic.List[string]
    $score = 0

    foreach ($entry in $checks.GetEnumerator()) {
        if ($content -match $entry.Value) {
            $matchedChecks.Add($entry.Key)

            switch ($entry.Key) {
                "ETL load_run"      { $score += 8 }
                "ETL load_chunk"    { $score += 10 }
                "Completed chunks"  { $score += 10 }
                "Rows counters"     { $score += 8 }
                "Heartbeat"         { $score += 6 }
                "Progress percent"  { $score += 8 }
                "ETA and speed"     { $score += 6 }
                "Pipeline status"   { $score += 8 }
                "Activity monitor"  { $score += 4 }
                "WAL monitor"       { $score += 3 }
                "Resume/checkpoint" { $score += 4 }
                "CSV/log output"    { $score += 3 }
                default             { $score += 2 }
            }
        }
    }

    # Require at least one strong progress indicator.
    $strongMatch = (
        $matchedChecks.Contains("ETL load_chunk") -or
        $matchedChecks.Contains("Completed chunks") -or
        $matchedChecks.Contains("Rows counters") -or
        $matchedChecks.Contains("Progress percent") -or
        $matchedChecks.Contains("Pipeline status")
    )

    if ($strongMatch) {
        $relativePath = $file.FullName.Substring($ProjectRoot.Length).TrimStart("\")
        $category = switch ($file.Extension.ToLowerInvariant()) {
            ".ps1" { "PowerShell" }
            ".py"  { "Python" }
            ".sql" { "SQL" }
            ".yaml" { "Configuration" }
            ".yml"  { "Configuration" }
            ".md"  { "Documentation" }
            default { "Other" }
        }

        $purpose = if (
            $matchedChecks.Contains("Completed chunks") -and
            $matchedChecks.Contains("Rows counters")
        ) {
            "Chunk progress monitor"
        }
        elseif ($matchedChecks.Contains("Pipeline status")) {
            "Pipeline status"
        }
        elseif ($matchedChecks.Contains("Activity monitor")) {
            "PostgreSQL activity monitor"
        }
        else {
            "Progress-related"
        }

        [pscustomobject]@{
            Score          = $score
            Category       = $category
            Purpose        = $purpose
            RelativePath   = $relativePath
            FullPath       = $file.FullName
            Modified       = $file.LastWriteTime
            SizeKB         = [math]::Round($file.Length / 1KB, 2)
            MatchedChecks  = ($matchedChecks -join "; ")
        }
    }
}

$results = @(
    $results | Sort-Object -Property `
        @{ Expression = "Score"; Descending = $true }, `
        @{ Expression = "Modified"; Descending = $true }
)

$results |
    Export-Csv -LiteralPath $csvFile -NoTypeInformation -Encoding UTF8

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("=" * 100)
$lines.Add("SEARCH FOR RAW -> DDS PROGRESS / MONITORING SCRIPTS")
$lines.Add("Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$lines.Add("Project: $ProjectRoot")
$lines.Add("Files inspected: $($files.Count)")
$lines.Add("Matching files: $($results.Count)")
$lines.Add("=" * 100)
$lines.Add("")

if ($results.Count -eq 0) {
    $lines.Add("No relevant scripts found.")
}
else {
    $tableText = $results |
        Select-Object Score, Category, Purpose, RelativePath, Modified, SizeKB, MatchedChecks |
        Format-Table -AutoSize |
        Out-String -Width 500

    $lines.Add($tableText.TrimEnd())
    $lines.Add("")
    $lines.Add("TOP CANDIDATES")
    $lines.Add("-" * 100)

    foreach ($item in ($results | Select-Object -First 10)) {
        $lines.Add("[$($item.Score)] $($item.FullPath)")
        $lines.Add("    Purpose: $($item.Purpose)")
        $lines.Add("    Checks:  $($item.MatchedChecks)")
    }
}

$lines.Add("")
$lines.Add("CSV: $csvFile")
$lines.Add("TXT: $txtFile")

$lines | Set-Content -LiteralPath $txtFile -Encoding UTF8

Write-Host ""
Write-Host "Matching files found: $($results.Count)"
Write-Host ""

if ($results.Count -gt 0) {
    $results |
        Select-Object Score, Category, Purpose, RelativePath, Modified, MatchedChecks |
        Format-Table -AutoSize
}

Write-Host ""
Write-Host "CSV: $csvFile"
Write-Host "TXT: $txtFile"

if ($results.Count -gt 0) {
    Write-Host ""
    Write-Host "Top 5 files:"
    $results |
        Select-Object -First 5 |
        ForEach-Object {
            Write-Host "[$($_.Score)] $($_.FullPath)"
        }
}
