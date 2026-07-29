#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$HostName = "localhost",
    [int]$Port = 5432,
    [string]$Database = "wms_analysis",
    [string]$UserName = "postgres",

    [string]$PsqlPath = "C:\Program Files\PostgreSQL\17\bin\psql.exe",

    [long]$ResumeFrom = 5757444576,
    [long]$BatchSize = 500000,

    [string]$ProjectDir =
        "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection",

    [string]$OutputDir =
        "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\data"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# UTF-8 encoding for Windows PowerShell 5.1
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

if (-not (Test-Path -LiteralPath $PsqlPath)) {
    throw "psql.exe not found: $PsqlPath"
}

if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

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

if ([string]::IsNullOrWhiteSpace($env:PGPASSWORD)) {
    throw "PostgreSQL password not found in PGPASSWORD or .env"
}

$runTimestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$csvPath = Join-Path `
    $OutputDir `
    "benchmark_alk_markserial_resume_$runTimestamp.csv"

$statePath = Join-Path `
    $OutputDir `
    "benchmark_alk_markserial_state.json"

# -------------------------------------------------------------------
# Invoke-Psql: Execute SQL and return structured result
# -------------------------------------------------------------------
function Invoke-Psql {
    param(
        [string]$Sql,
        [string]$File
    )

    $arguments = @(
        "--host=$HostName"
        "--port=$Port"
        "--username=$UserName"
        "--dbname=$Database"
        "--no-psqlrc"
        "--tuples-only"
        "--no-align"
        "--quiet"
        "--set=ON_ERROR_STOP=1"
    )

    if ($Sql) {
        $arguments += @("-c", $Sql)
    }

    if ($File) {
        $arguments += @("--file=$File")
    }

    $stdout = & $PsqlPath @arguments 2>&1
    $exitCode = $LASTEXITCODE

    $stdoutText = ($stdout | Out-String).Trim()

    return [PSCustomObject]@{
        ExitCode = $exitCode
        StdOut   = $stdoutText
        Lines    = @(
            $stdoutText -split "\r?\n" |
                ForEach-Object { $_.Trim() } |
                Where-Object { $_ -ne "" }
        )
    }
}

# -------------------------------------------------------------------
# Extract numeric value from psql output
# -------------------------------------------------------------------
function Get-NumericUpDown {
    param([string[]]$Lines)

    $numericLines = @(
        $Lines | Where-Object { $_ -match '^\d+$' }
    )

    if ($numericLines.Count -eq 0) {
        return $null
    }

    return [long]$numericLines[-1]
}

# -------------------------------------------------------------------
# Write CSV record
# -------------------------------------------------------------------
function Write-CsvRecord {
    param([PSCustomObject]$Record)

    if (-not (Test-Path -LiteralPath $csvPath)) {
        $Record |
            Export-Csv `
                -LiteralPath $csvPath `
                -NoTypeInformation `
                -Encoding UTF8
    }
    else {
        $Record |
            Export-Csv `
                -LiteralPath $csvPath `
                -NoTypeInformation `
                -Encoding UTF8 `
                -Append
    }
}

# -------------------------------------------------------------------
# Verify chunk status via read-only query
# -------------------------------------------------------------------
function Verify-Chunk {
    param(
        [long]$ChunkFrom,
        [long]$ChunkTo
    )

    $verifySql = @"
WITH source_chunk AS (
    SELECT COUNT(*) AS source_rows
    FROM raw_ax.alk_markserial
    WHERE recid >= '$ChunkFrom'
      AND recid <  '$ChunkTo'
),
target_chunk AS (
    SELECT COUNT(*) AS target_rows
    FROM benchmark.alk_markserial_test
    WHERE recid >= $ChunkFrom
      AND recid <  $ChunkTo
)
SELECT
    source_rows,
    target_rows,
    source_rows - target_rows AS missing_rows,
    CASE
        WHEN source_rows = target_rows THEN 'COMPLETE'
        WHEN target_rows = 0 THEN 'NOT_LOADED'
        ELSE 'PARTIAL'
    END AS chunk_status
FROM source_chunk, target_chunk;
"@

    $result = Invoke-Psql -Sql $verifySql

    if ($result.ExitCode -ne 0) {
        return [PSCustomObject]@{
            SourceRows  = -1
            TargetRows  = -1
            MissingRows = -1
            Status      = "VERIFY_ERROR"
        }
    }

    # Parse: source_rows|target_rows|missing_rows|chunk_status
    $parts = ($result.StdOut -split '\|') | ForEach-Object { $_.Trim() }

    if ($parts.Count -lt 4) {
        return [PSCustomObject]@{
            SourceRows  = -1
            TargetRows  = -1
            MissingRows = -1
            Status      = "PARSE_ERROR"
        }
    }

    return [PSCustomObject]@{
        SourceRows  = [long]$parts[0]
        TargetRows  = [long]$parts[1]
        MissingRows = [long]$parts[2]
        Status      = $parts[3]
    }
}

# -------------------------------------------------------------------
# Atomic checkpoint save
# -------------------------------------------------------------------
function Save-Checkpoint {
    param(
        [long]$LastCompletedFrom,
        [long]$LastCompletedTo,
        [long]$NextCheckpoint,
        [long]$InsertedRows,
        [string]$Status
    )

    $state = @{
        stage               = "benchmark_alk_markserial"
        last_completed_from = $LastCompletedFrom
        last_completed_to   = $LastCompletedTo
        next_checkpoint     = $NextCheckpoint
        status              = $Status
        inserted_rows       = $InsertedRows
        completed_at        = (Get-Date).ToString("o")
    }

    $stateJson = $state | ConvertTo-Json -Depth 5
    $tempPath = "$statePath.tmp"

    [System.IO.File]::WriteAllText(
        $tempPath,
        $stateJson,
        [System.Text.Encoding]::UTF8
    )

    Move-Item `
        -LiteralPath $tempPath `
        -Destination $statePath `
        -Force
}

# ===================================================================
# MAIN
# ===================================================================

try {

    # ---------------------------------------------------------------
    # Check for active loading
    # ---------------------------------------------------------------

    $activeSql = @"
SELECT COUNT(*)
FROM pg_stat_activity
WHERE state <> 'idle'
  AND pid <> pg_backend_pid()
  AND query ILIKE '%INSERT INTO benchmark.alk_markserial_test%';
"@

    $activeResult = Invoke-Psql -Sql $activeSql

    if ($activeResult.ExitCode -eq 0) {
        $activeCount = Get-NumericUpDown -Lines $activeResult.Lines
        if ($activeCount -and $activeCount -gt 0) {
            throw "INSERT into benchmark.alk_markserial_test is already running."
        }
    }

    # ---------------------------------------------------------------
    # Determine checkpoint
    # ---------------------------------------------------------------

    if ($PSBoundParameters.ContainsKey("ResumeFrom")) {
        $currentLower = $ResumeFrom
        $checkpointSource = "command_line"
    }
    else {
        # Load from state file if exists
        if (Test-Path -LiteralPath $statePath) {
            $savedState = Get-Content -LiteralPath $statePath -Raw |
                ConvertFrom-Json
            if ($savedState.next_checkpoint) {
                $currentLower = [long]$savedState.next_checkpoint
                $checkpointSource = "state_file"
            }
        }

        if (-not $checkpointSource) {
            # Auto-detect from table
            $checkpointSql = @"
SELECT COALESCE(MAX(recid) + 1, $ResumeFrom)
FROM benchmark.alk_markserial_test;
"@
            $cpResult = Invoke-Psql -Sql $checkpointSql

            if ($cpResult.ExitCode -eq 0) {
                $currentLower = Get-NumericUpDown -Lines $cpResult.Lines
                if (-not $currentLower) {
                    $currentLower = $ResumeFrom
                }
            }
            else {
                $currentLower = $ResumeFrom
            }
            $checkpointSource = "auto"
        }
    }

    Write-Host ""
    Write-Host "============================================================"
    Write-Host "BENCHMARK ALK_MARKSERIAL RESUME LOADER"
    Write-Host "============================================================"
    Write-Host "Checkpoint source: $checkpointSource"
    Write-Host "Checkpoint value:  $currentLower"
    Write-Host "Batch size:        $BatchSize"
    Write-Host "CSV:               $csvPath"
    Write-Host "State:             $statePath"
    Write-Host "Stop:              Ctrl+C"
    Write-Host "============================================================"
    Write-Host ""

    # ---------------------------------------------------------------
    # Main loop
    # ---------------------------------------------------------------

    while ($true) {

        $lower = $currentLower
        $upper = $lower + $BatchSize

        $startedAt = Get-Date

        Write-Host "------------------------------------------------------------"
        Write-Host "Chunk: [$lower, $upper)"
        Write-Host "Start: $($startedAt.ToString('yyyy-MM-dd HH:mm:ss'))"

        $sql = @"
BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '15min';

WITH inserted AS (
    INSERT INTO benchmark.alk_markserial_test (
        recid,
        gtin,
        serialnumber,
        itemid,
        markcode,
        createddatetime,
        modifieddatetime,
        createdby,
        modifiedby,
        loaded_at
    )
    SELECT
        r.recid::bigint,
        r.gtin,
        r.serialid,
        r.itemid,
        r.markcode,
        NULLIF(BTRIM(r.createddatetime), '')::timestamptz,
        NULLIF(BTRIM(r.modifieddatetime), '')::timestamptz,
        r.createdby,
        r.modifiedby,
        clock_timestamp()
    FROM raw_ax.alk_markserial r
    WHERE r.recid >= '$lower'
      AND r.recid <  '$upper'
    ORDER BY r.recid
    ON CONFLICT (recid) DO NOTHING
    RETURNING 1
)
SELECT COUNT(*)
FROM inserted;

COMMIT;
"@

        $result = Invoke-Psql -Sql $sql

        $finishedAt = Get-Date
        $durationSeconds = ($finishedAt - $startedAt).TotalSeconds

        # -----------------------------------------------------------
        # Parse result
        # -----------------------------------------------------------

        $insertedRows = $null
        $chunkStatus = "UNKNOWN"

        if ($result.ExitCode -ne 0) {
            # SQL failed
            $chunkStatus = "SQL_ERROR"
            Write-Host "FAILED (exit code: $($result.ExitCode))"
            Write-Host "stderr: $($result.StdOut)"

            $record = [PSCustomObject]@{
                timestamp        = $finishedAt.ToString("yyyy-MM-dd HH:mm:ss")
                lower_bound      = $lower
                upper_bound      = $upper
                inserted_rows    = 0
                duration_seconds = [math]::Round($durationSeconds, 3)
                checkpoint_source = $checkpointSource
                source_rows      = 0
                target_rows_after = 0
                missing_rows     = 0
                chunk_status     = $chunkStatus
                psql_exit_code   = $result.ExitCode
                error_message    = $result.StdOut
            }

            Write-CsvRecord -Record $record
            throw "SQL failed with exit code $($result.ExitCode)"
        }

        # Extract numeric value
        $insertedRows = Get-NumericUpDown -Lines $result.Lines

        if ($null -ne $insertedRows) {
            $chunkStatus = "SUCCESS"
            Write-Host "Inserted: $insertedRows"
        }
        else {
            # Uncertain result - verify chunk
            Write-Host "Result uncertain, verifying chunk..."
            $chunkStatus = "UNKNOWN"

            $verify = Verify-Chunk -ChunkFrom $lower -ChunkTo $upper

            Write-Host "Verify: source=$($verify.SourceRows) target=$($verify.TargetRows) missing=$($verify.MissingRows) status=$($verify.Status)"

            if ($verify.Status -eq "COMPLETE") {
                $insertedRows = $verify.TargetRows
                $chunkStatus = "VERIFIED_COMPLETE"
            }
            elseif ($verify.Status -eq "NOT_LOADED") {
                $insertedRows = 0
                $chunkStatus = "NOT_LOADED"
            }
            else {
                $insertedRows = 0
                $chunkStatus = "PARTIAL"
            }
        }

        Write-Host "Duration: $([math]::Round($durationSeconds, 2))s"

        # -----------------------------------------------------------
        # CSV log
        # -----------------------------------------------------------

        # Get target count after insert
        $targetCountResult = Invoke-Psql -Sql "SELECT COUNT(*) FROM benchmark.alk_markserial_test WHERE recid >= $lower AND recid < $upper;"
        $targetAfter = 0
        if ($targetCountResult.ExitCode -eq 0) {
            $targetAfter = Get-NumericUpDown -Lines $targetCountResult.Lines
            if (-not $targetAfter) { $targetAfter = 0 }
        }

        $record = [PSCustomObject]@{
            timestamp        = $finishedAt.ToString("yyyy-MM-dd HH:mm:ss")
            lower_bound      = $lower
            upper_bound      = $upper
            inserted_rows    = $insertedRows
            duration_seconds = [math]::Round($durationSeconds, 3)
            checkpoint_source = $checkpointSource
            source_rows      = 0
            target_rows_after = $targetAfter
            missing_rows     = 0
            chunk_status     = $chunkStatus
            psql_exit_code   = $result.ExitCode
            error_message    = ""
        }

        Write-CsvRecord -Record $record

        # -----------------------------------------------------------
        # Update checkpoint (only after confirmation)
        # -----------------------------------------------------------

        if ($chunkStatus -eq "SUCCESS" -or
            $chunkStatus -eq "VERIFIED_COMPLETE") {

            Save-Checkpoint `
                -LastCompletedFrom $lower `
                -LastCompletedTo $upper `
                -NextCheckpoint $upper `
                -InsertedRows $insertedRows `
                -Status "completed"

            Write-Host "Checkpoint updated: $upper"
        }
        elseif ($chunkStatus -eq "NOT_LOADED") {
            Write-Host "Chunk not loaded, stopping."
            break
        }
        elseif ($chunkStatus -eq "PARTIAL") {
            Write-Host "Chunk partial, stopping. Resume will retry this chunk."
            break
        }

        # -----------------------------------------------------------
        # Move to next chunk
        # -----------------------------------------------------------

        if ($insertedRows -eq 0) {

            # Check if there are more rows in RAW
            $nextSql = @"
SELECT recid
FROM raw_ax.alk_markserial
WHERE recid >= '$upper'
ORDER BY recid
LIMIT 1;
"@

            $nextResult = Invoke-Psql -Sql $nextSql

            if ($nextResult.ExitCode -ne 0 -or
                [string]::IsNullOrWhiteSpace($nextResult.StdOut)) {

                Write-Host ""
                Write-Host "No more rows in RAW."
                Write-Host "Benchmark loading completed."
                break
            }

            $nextNumeric = Get-NumericUpDown -Lines $nextResult.Lines

            if (-not $nextNumeric) {
                Write-Host ""
                Write-Host "No more rows in RAW."
                Write-Host "Benchmark loading completed."
                break
            }

            Write-Host "Next RAW RECID: $nextNumeric"
            $currentLower = $nextNumeric
        }
        else {
            $currentLower = $upper
        }
    }
}
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
