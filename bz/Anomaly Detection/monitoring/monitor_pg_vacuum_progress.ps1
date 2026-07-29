# monitor_pg_vacuum_progress.ps1

[CmdletBinding()]
param(
    [string]$PsqlPath = "C:\Program Files\PostgreSQL\17\bin\psql.exe",

    [string]$HostName = "localhost",

    [ValidateRange(1, 65535)]
    [int]$Port = 5432,

    [string]$Database = "wms_analysis",

    [string]$User = "postgres",

    [string]$TableName = "raw_ax.alk_markserial",

    [ValidateRange(1, 86400)]
    [int]$IntervalSeconds = 5,

    [string]$OutputDir = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\data",

    [switch]$StopWhenFinished
)

$ErrorActionPreference = "Stop"

# Для корректного отображения русского текста.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

$SafeTableName = $TableName -replace '[^a-zA-Z0-9_.-]', '_'
$SafeTableName = $SafeTableName -replace '\.', '_'

$CsvFile = Join-Path `
    $OutputDir `
    "pg_vacuum_progress_${SafeTableName}_${Timestamp}.csv"

$ErrorFile = Join-Path `
    $OutputDir `
    "pg_vacuum_progress_${SafeTableName}_${Timestamp}_errors.log"

$CsvHeader = @(
    "sampled_at"
    "status"
    "pid"
    "table_name"
    "phase"
    "heap_blks_total"
    "heap_blks_scanned"
    "heap_blks_vacuumed"
    "scan_percent"
    "vacuum_percent"
    "index_vacuum_count"
    "max_dead_tuple_bytes"
    "dead_tuple_bytes"
    "num_dead_item_ids"
) -join ","

function Write-ErrorLog {
    param(
        [Parameter(Mandatory)]
        [string]$Message
    )

    $logLine = "{0} {1}" -f `
        (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), `
        $Message

    Add-Content `
        -Path $ErrorFile `
        -Value $logLine `
        -Encoding UTF8
}

try {
    # -----------------------------------------------------------------
    # Предварительные проверки
    # -----------------------------------------------------------------

    if (-not (Test-Path -LiteralPath $PsqlPath -PathType Leaf)) {
        throw "psql.exe не найден: $PsqlPath"
    }

    New-Item `
        -ItemType Directory `
        -Path $OutputDir `
        -Force |
        Out-Null

    $CsvHeader |
        Set-Content `
            -Path $CsvFile `
            -Encoding UTF8

    New-Item `
        -ItemType File `
        -Path $ErrorFile `
        -Force |
        Out-Null

    # Защита литерала перед подстановкой в SQL.
    $EscapedTableName = $TableName.Replace("'", "''")

    # Запрос всегда возвращает одну строку.
    # После завершения VACUUM будет записан статус NOT_FOUND.
    $Query = @"
WITH requested AS (
    SELECT '$EscapedTableName'::regclass AS relid
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
            THEN round(
                p.heap_blks_scanned * 100.0 /
                p.heap_blks_total,
                2
            )
        END AS scan_percent,
        CASE
            WHEN p.heap_blks_total > 0
            THEN round(
                p.heap_blks_vacuumed * 100.0 /
                p.heap_blks_total,
                2
            )
        END AS vacuum_percent,
        p.index_vacuum_count,
        p.max_dead_tuple_bytes,
        p.dead_tuple_bytes,
        p.num_dead_item_ids
    FROM pg_stat_progress_vacuum AS p
    INNER JOIN requested AS r
        ON r.relid = p.relid
)
SELECT
    to_char(
        clock_timestamp(),
        'YYYY-MM-DD HH24:MI:SS.MS'
    ) AS sampled_at,
    CASE
        WHEN vp.pid IS NULL THEN 'NOT_FOUND'
        ELSE 'RUNNING'
    END AS status,
    vp.pid,
    r.relid::regclass AS table_name,
    vp.phase,
    vp.heap_blks_total,
    vp.heap_blks_scanned,
    vp.heap_blks_vacuumed,
    vp.scan_percent,
    vp.vacuum_percent,
    vp.index_vacuum_count,
    vp.max_dead_tuple_bytes,
    vp.dead_tuple_bytes,
    vp.num_dead_item_ids
FROM requested AS r
LEFT JOIN vacuum_progress AS vp
    ON vp.relid = r.relid;
"@

    # -----------------------------------------------------------------
    # Проверка подключения и существования таблицы
    # -----------------------------------------------------------------

    $PreflightQuery = @"
SELECT
    current_database() AS database_name,
    current_user AS user_name,
    '$EscapedTableName'::regclass AS table_name;
"@

    $PreflightResult = & $PsqlPath `
        "--host=$HostName" `
        "--port=$Port" `
        "--username=$User" `
        "--dbname=$Database" `
        "--no-psqlrc" `
        "--quiet" `
        "--tuples-only" `
        "--no-align" `
        "--set=ON_ERROR_STOP=1" `
        "--command=$PreflightQuery" 2>> $ErrorFile

    if ($LASTEXITCODE -ne 0) {
        throw "Предварительная проверка PostgreSQL завершилась с кодом $LASTEXITCODE."
    }

    Write-Host ""
    Write-Host "Мониторинг VACUUM запущен."
    Write-Host "Таблица:  $TableName"
    Write-Host "Интервал: $IntervalSeconds секунд"
    Write-Host "CSV:      $CsvFile"
    Write-Host "Ошибки:   $ErrorFile"
    Write-Host "Остановка: Ctrl+C"

    if ($StopWhenFinished) {
        Write-Host "После исчезновения операции VACUUM мониторинг завершится автоматически."
    }

    Write-Host ""

    $SampleNumber = 0
    $ConsecutiveNotFound = 0
    $ConsecutiveErrors = 0
    $MaxConsecutiveErrors = 10

    while ($true) {
        $CycleStartedAt = Get-Date
        $SampleNumber++

        $StandardErrorFile = Join-Path `
            $env:TEMP `
            "pg_vacuum_monitor_stderr_$PID.txt"

        try {
            Remove-Item `
                -LiteralPath $StandardErrorFile `
                -Force `
                -ErrorAction SilentlyContinue

            $Result = & $PsqlPath `
                "--host=$HostName" `
                "--port=$Port" `
                "--username=$User" `
                "--dbname=$Database" `
                "--no-psqlrc" `
                "--quiet" `
                "--csv" `
                "--tuples-only" `
                "--set=ON_ERROR_STOP=1" `
                "--command=$Query" 2> $StandardErrorFile

            $PsqlExitCode = $LASTEXITCODE

            if ($PsqlExitCode -ne 0) {
                $ConsecutiveErrors++

                $ErrorText = ""

                if (Test-Path -LiteralPath $StandardErrorFile) {
                    $ErrorText = Get-Content `
                        -LiteralPath $StandardErrorFile `
                        -Raw `
                        -ErrorAction SilentlyContinue
                }

                $Message = "psql завершился с кодом $PsqlExitCode."

                if (-not [string]::IsNullOrWhiteSpace($ErrorText)) {
                    $Message += " $($ErrorText.Trim())"
                }

                Write-Warning $Message
                Write-ErrorLog -Message $Message

                if ($ConsecutiveErrors -ge $MaxConsecutiveErrors) {
                    throw "Достигнут предел последовательных ошибок: $MaxConsecutiveErrors."
                }
            }
            else {
                $ConsecutiveErrors = 0

                $Rows = @(
                    $Result |
                        Where-Object {
                            -not [string]::IsNullOrWhiteSpace($_)
                        }
                )

                foreach ($Row in $Rows) {
                    Add-Content `
                        -Path $CsvFile `
                        -Value $Row `
                        -Encoding UTF8
                }

                if ($Rows.Count -eq 0) {
                    Write-Warning "PostgreSQL не вернул строку результата."
                    Write-ErrorLog `
                        -Message "Запрос выполнен успешно, но результат пуст."
                }
                else {
                    # Разбираем строку только для вывода краткого статуса.
                    $ParsedRow = $Rows[0] |
                        ConvertFrom-Csv -Header @(
                            "sampled_at"
                            "status"
                            "pid"
                            "table_name"
                            "phase"
                            "heap_blks_total"
                            "heap_blks_scanned"
                            "heap_blks_vacuumed"
                            "scan_percent"
                            "vacuum_percent"
                            "index_vacuum_count"
                            "max_dead_tuple_bytes"
                            "dead_tuple_bytes"
                            "num_dead_item_ids"
                        )

                    if ($ParsedRow.status -eq "RUNNING") {
                        $ConsecutiveNotFound = 0

                        $ConsoleLine = (
                            "{0} | PID {1} | {2} | scan {3}% | vacuum {4}% | indexes {5}"
                        ) -f `
                            (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), `
                            $ParsedRow.pid, `
                            $ParsedRow.phase, `
                            $ParsedRow.scan_percent, `
                            $ParsedRow.vacuum_percent, `
                            $ParsedRow.index_vacuum_count

                        Write-Host $ConsoleLine
                    }
                    else {
                        $ConsecutiveNotFound++

                        Write-Host (
                            "{0} | VACUUM для {1} не найден | проверка {2}"
                        ) -f `
                            (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), `
                            $TableName, `
                            $ConsecutiveNotFound
                    }

                    # Две последовательные проверки защищают от одиночного
                    # кратковременного сбоя чтения статистики.
                    if (
                        $StopWhenFinished -and
                        $ConsecutiveNotFound -ge 2
                    ) {
                        Write-Host ""
                        Write-Host "Операция VACUUM больше не отображается в pg_stat_progress_vacuum."
                        break
                    }
                }
            }
        }
        catch {
            Write-ErrorLog -Message $_.Exception.Message
            throw
        }
        finally {
            Remove-Item `
                -LiteralPath $StandardErrorFile `
                -Force `
                -ErrorAction SilentlyContinue
        }

        # Компенсация времени выполнения psql, чтобы интервал меньше дрейфовал.
        $CycleDurationSeconds = (
            (Get-Date) - $CycleStartedAt
        ).TotalSeconds

        $SleepSeconds = $IntervalSeconds - $CycleDurationSeconds

        if ($SleepSeconds -gt 0) {
            Start-Sleep `
                -Milliseconds ([int]($SleepSeconds * 1000))
        }
    }
}
catch {
    $FatalMessage = $_.Exception.Message

    Write-Host ""
    Write-Error "Мониторинг завершён с ошибкой: $FatalMessage"

    try {
        Write-ErrorLog -Message "Критическая ошибка: $FatalMessage"
    }
    catch {
        # Лог может быть ещё недоступен, например при ошибке каталога.
    }

    exit 1
}
finally {
    Write-Host ""
    Write-Host "Мониторинг остановлен."

    if ($CsvFile -and (Test-Path -LiteralPath $CsvFile)) {
        Write-Host "Результат сохранён:"
        Write-Host $CsvFile
    }

    if ($ErrorFile -and (Test-Path -LiteralPath $ErrorFile)) {
        Write-Host "Журнал ошибок:"
        Write-Host $ErrorFile
    }

    Remove-Item `
        Env:\PGPASSWORD `
        -ErrorAction SilentlyContinue
}