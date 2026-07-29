$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# ============================================================
# SETTINGS
# ============================================================

$PsqlPath = "C:\Program Files\PostgreSQL\17\bin\psql.exe"

$DbHost = "localhost"
$DbPort = "5432"
$Database = "wms_analysis"
$DbUser = "postgres"

$SourceTable = "WMSORDERTRANS"

$OutputDir = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\logs"
$LogFile = Join-Path $OutputDir "scheduler.log"

# 0 = automatically select the latest run_id.
# Set a specific value, for example 19, to export a fixed run.
$ManualRunId = 0

# ============================================================
# PREPARATION
# ============================================================

if (-not (Test-Path -LiteralPath $PsqlPath)) {
    throw "psql.exe not found: $PsqlPath"
}

if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

Start-Transcript -Path $LogFile -Append

try {
    Write-Host "============================================================"
    Write-Host "ETL export started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "============================================================"

    # ========================================================
    # DETERMINE RUN ID
    # ========================================================

    if ($ManualRunId -gt 0) {
        $RunId = [string]$ManualRunId
    }
    else {
        $RunIdSql = @"
SELECT run_id
FROM etl.load_run
WHERE UPPER(source_table) = UPPER('$SourceTable')
ORDER BY
    CASE
        WHEN status IN ('running', 'completed_with_errors', 'failed') THEN 0
        WHEN status = 'completed' THEN 1
        ELSE 2
    END,
    started_at DESC NULLS LAST,
    created_at DESC NULLS LAST,
    run_id DESC
LIMIT 1;
"@

        $RunId = (
            $RunIdSql |
            & $PsqlPath `
                -h $DbHost `
                -p $DbPort `
                -U $DbUser `
                -d $Database `
                -t `
                -A `
                -v ON_ERROR_STOP=1
        ).Trim()

        if ($LASTEXITCODE -ne 0) {
            throw "Failed to determine run_id. psql exit code: $LASTEXITCODE"
        }

        if ([string]::IsNullOrWhiteSpace($RunId)) {
            throw "No run_id found for source table: $SourceTable"
        }
    }

    Write-Host "Selected run_id: $RunId"

    # ========================================================
    # OUTPUT FILE
    # ========================================================

    $Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputFile = Join-Path $OutputDir "completed_${RunId}_${Timestamp}.csv"
    $TempFile = "$OutputFile.tmp"

    if (Test-Path -LiteralPath $TempFile) {
        Remove-Item -LiteralPath $TempFile -Force
    }

    # ========================================================
    # EXPORT QUERY
    # ========================================================

    $ExportSql = @"
COPY (
    WITH completed AS (
        SELECT
            c.run_id,
            c.chunk_no,
            c.status,
            c.attempt_count,
            c.rows_read,
            c.rows_staged,
            c.rows_inserted,
            c.rows_updated,
            c.rows_conflicted,
            c.started_at,
            c.completed_at,
            CASE
                WHEN c.started_at IS NOT NULL
                 AND c.completed_at IS NOT NULL
                 AND c.completed_at > c.started_at
                THEN EXTRACT(
                    EPOCH FROM (
                        c.completed_at - c.started_at
                    )
                )::numeric
                ELSE NULL
            END AS duration_seconds
        FROM etl.load_chunk c
        WHERE c.run_id = $RunId
          AND c.status = 'completed'
    )
    SELECT
        run_id,
        chunk_no,
        status,
        attempt_count,
        rows_read,
        rows_staged,
        rows_inserted,
        rows_updated,
        rows_conflicted,
        started_at,
        completed_at,
        duration_seconds * INTERVAL '1 second' AS duration,
        CASE
            WHEN duration_seconds > 0
            THEN ROUND(
                rows_read::numeric / duration_seconds,
                0
            )
            ELSE NULL
        END AS rows_per_second
    FROM completed
    ORDER BY chunk_no
) TO STDOUT WITH (
    FORMAT CSV,
    HEADER TRUE,
    ENCODING 'UTF8'
);
"@

    Write-Host "Output file: $OutputFile"

    $ExportSql |
        & $PsqlPath `
            -h $DbHost `
            -p $DbPort `
            -U $DbUser `
            -d $Database `
            -v ON_ERROR_STOP=1 `
            -o $TempFile

    if ($LASTEXITCODE -ne 0) {
        throw "Export failed. psql exit code: $LASTEXITCODE"
    }

    if (-not (Test-Path -LiteralPath $TempFile)) {
        throw "Temporary output file was not created: $TempFile"
    }

    $FileSize = (Get-Item -LiteralPath $TempFile).Length

    if ($FileSize -le 0) {
        throw "Export file is empty: $TempFile"
    }

    Move-Item `
        -LiteralPath $TempFile `
        -Destination $OutputFile `
        -Force

    $CsvRowCount = (
        Get-Content -LiteralPath $OutputFile |
        Measure-Object -Line
    ).Lines - 1

    Write-Host "Export completed successfully"
    Write-Host "CSV rows: $CsvRowCount"
    Write-Host "File size: $FileSize bytes"
    Write-Host "Created file: $OutputFile"
}
catch {
    Write-Error $_

    if (
        $null -ne $TempFile -and
        (Test-Path -LiteralPath $TempFile)
    ) {
        Remove-Item -LiteralPath $TempFile -Force
    }

    exit 1
}
finally {
    Write-Host "Finished: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Stop-Transcript
}

exit 0