#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$HostName = "localhost",
    [int]$Port = 5432,
    [string]$Database = "wms_analysis",
    [string]$UserName = "postgres",

    [string]$PsqlPath = "C:\Program Files\PostgreSQL\17\bin\psql.exe",

    [int]$IntervalSeconds = 60,

    [string]$ProjectDir = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection",

    [string]$OutputDir = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\data"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------
# Проверки
# ---------------------------------------------------------------------

if (-not (Test-Path -LiteralPath $PsqlPath)) {
    throw "psql.exe не найден: $PsqlPath"
}

if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$csvPath = Join-Path $OutputDir "serial_mark_load_monitor_$timestamp.csv"

# ---------------------------------------------------------------------
# Пароль PostgreSQL
# Приоритет:
# 1. PGPASSWORD;
# 2. DB_PASSWORD из .env;
# 3. ручной ввод.
# ---------------------------------------------------------------------

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

            if (
                ($password.StartsWith('"') -and $password.EndsWith('"')) -or
                ($password.StartsWith("'") -and $password.EndsWith("'"))
            ) {
                $password = $password.Substring(1, $password.Length - 2)
            }

            $env:PGPASSWORD = $password
        }
    }
}

if ([string]::IsNullOrWhiteSpace($env:PGPASSWORD)) {

    $securePassword = Read-Host `
        "Введите пароль PostgreSQL для пользователя $UserName" `
        -AsSecureString

    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR(
        $securePassword
    )

    try {
        $env:PGPASSWORD =
            [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

# ---------------------------------------------------------------------
# SQL
# Одной строкой возвращаем показатели таблицы и WAL.
# ---------------------------------------------------------------------

$sql = @"
SELECT
    clock_timestamp() AS captured_at,

    pg_relation_size('dds.serial_mark') AS table_bytes,
    pg_indexes_size('dds.serial_mark') AS index_bytes,
    pg_total_relation_size('dds.serial_mark') AS total_bytes,

    pg_size_pretty(
        pg_relation_size('dds.serial_mark')
    ) AS table_size,

    pg_size_pretty(
        pg_indexes_size('dds.serial_mark')
    ) AS index_size,

    pg_size_pretty(
        pg_total_relation_size('dds.serial_mark')
    ) AS total_size,

    w.wal_records,
    w.wal_fpi,
    w.wal_bytes,
    w.wal_buffers_full,
    w.wal_write,
    w.wal_sync

FROM pg_stat_wal w;
"@

$psqlArgs = @(
    "-X",
    "-w",
    "-h", $HostName,
    "-p", $Port.ToString(),
    "-U", $UserName,
    "-d", $Database,
    "-v", "ON_ERROR_STOP=1",
    "-A",
    "-t",
    "-F", "|",
    "-c", $sql
)

# ---------------------------------------------------------------------
# Переменные предыдущего замера
# ---------------------------------------------------------------------

$previousWalBytes = $null
$previousWalRecords = $null
$previousWalBuffersFull = $null
$previousTotalBytes = $null
$previousTimestamp = $null

$iteration = 0

Write-Host ""
Write-Host "Мониторинг dds.serial_mark запущен"
Write-Host "Интервал: $IntervalSeconds секунд"
Write-Host "CSV: $csvPath"
Write-Host "Для остановки нажмите Ctrl+C"
Write-Host ""

try {

    while ($true) {

        $iteration++

        try {

            $rawResult = & $PsqlPath @psqlArgs 2>&1

            if ($LASTEXITCODE -ne 0) {
                throw "Ошибка psql: $($rawResult -join [Environment]::NewLine)"
            }

            $line = (
                $rawResult |
                Where-Object {
                    -not [string]::IsNullOrWhiteSpace($_)
                } |
                Select-Object -Last 1
            ).ToString()

            $values = $line -split '\|'

            if ($values.Count -ne 13) {
                throw "Ожидалось 13 полей, получено: $($values.Count). Строка: $line"
            }

            $capturedAt = [datetime]$values[0]

            $tableBytes = [int64]$values[1]
            $indexBytes = [int64]$values[2]
            $totalBytes = [int64]$values[3]

            $tableSize = $values[4]
            $indexSize = $values[5]
            $totalSize = $values[6]

            $walRecords = [int64]$values[7]
            $walFpi = [int64]$values[8]
            $walBytes = [decimal]$values[9]
            $walBuffersFull = [int64]$values[10]
            $walWrite = [int64]$values[11]
            $walSync = [int64]$values[12]

            # ---------------------------------------------------------
            # Расчёт дельт
            # ---------------------------------------------------------

            $elapsedSeconds = $null
            $deltaWalBytes = $null
            $deltaWalRecords = $null
            $deltaWalBuffersFull = $null
            $deltaTotalBytes = $null
            $walMbPerMin = $null
            $tableGrowthMbPerMin = $null

            if ($null -ne $previousTimestamp) {

                $elapsedSeconds = (
                    $capturedAt - $previousTimestamp
                ).TotalSeconds

                if ($elapsedSeconds -gt 0) {

                    $deltaWalBytes = $walBytes - $previousWalBytes

                    $deltaWalRecords =
                        $walRecords - $previousWalRecords

                    $deltaWalBuffersFull =
                        $walBuffersFull - $previousWalBuffersFull

                    $deltaTotalBytes =
                        $totalBytes - $previousTotalBytes

                    $walMbPerMin = [math]::Round(
                        (
                            ([double]$deltaWalBytes / 1MB) /
                            $elapsedSeconds
                        ) * 60,
                        2
                    )

                    $tableGrowthMbPerMin = [math]::Round(
                        (
                            ([double]$deltaTotalBytes / 1MB) /
                            $elapsedSeconds
                        ) * 60,
                        2
                    )
                }
            }

            # ---------------------------------------------------------
            # Объект результата
            # ---------------------------------------------------------

            $record = [PSCustomObject]@{
                timestamp                 = $capturedAt.ToString(
                    "yyyy-MM-dd HH:mm:ss.fff"
                )

                iteration                 = $iteration
                elapsed_seconds           = $elapsedSeconds

                table_bytes               = $tableBytes
                index_bytes               = $indexBytes
                total_bytes               = $totalBytes

                table_size                = $tableSize
                index_size                = $indexSize
                total_size                = $totalSize

                delta_total_bytes         = $deltaTotalBytes
                table_growth_mb_per_min   = $tableGrowthMbPerMin

                wal_records               = $walRecords
                wal_fpi                   = $walFpi
                wal_bytes                 = $walBytes
                wal_buffers_full          = $walBuffersFull
                wal_write                 = $walWrite
                wal_sync                  = $walSync

                delta_wal_records         = $deltaWalRecords
                delta_wal_bytes           = $deltaWalBytes
                delta_wal_buffers_full    = $deltaWalBuffersFull

                wal_mb_per_min            = $walMbPerMin
            }

            # ---------------------------------------------------------
            # CSV
            # ---------------------------------------------------------

            if (-not (Test-Path -LiteralPath $csvPath)) {
                $record |
                    Export-Csv `
                        -LiteralPath $csvPath `
                        -NoTypeInformation `
                        -Encoding UTF8
            }
            else {
                $record |
                    Export-Csv `
                        -LiteralPath $csvPath `
                        -NoTypeInformation `
                        -Encoding UTF8 `
                        -Append
            }

            # ---------------------------------------------------------
            # Вывод на экран
            # ---------------------------------------------------------

            Write-Host "============================================================"
            Write-Host "Замер:       $($record.timestamp)"
            Write-Host "Итерация:    $iteration"
            Write-Host ""
            Write-Host "dds.serial_mark"
            Write-Host "  Таблица:   $tableSize"
            Write-Host "  Индексы:   $indexSize"
            Write-Host "  Всего:     $totalSize"

            if ($null -ne $deltaTotalBytes) {
                $deltaTotalMb = [math]::Round(
                    [double]$deltaTotalBytes / 1MB,
                    2
                )

                Write-Host "  Прирост:   $deltaTotalMb MB"
                Write-Host "  Скорость:  $tableGrowthMbPerMin MB/min"
            }

            Write-Host ""
            Write-Host "WAL"
            Write-Host "  wal_records:       $walRecords"
            Write-Host "  wal_fpi:           $walFpi"
            Write-Host "  wal_bytes:         $walBytes"
            Write-Host "  wal_buffers_full:  $walBuffersFull"
            Write-Host "  wal_write:         $walWrite"
            Write-Host "  wal_sync:          $walSync"

            if ($null -ne $deltaWalBytes) {

                $deltaWalMb = [math]::Round(
                    [double]$deltaWalBytes / 1MB,
                    2
                )

                Write-Host ""
                Write-Host "Дельта за интервал"
                Write-Host "  WAL:               $deltaWalMb MB"
                Write-Host "  WAL records:       $deltaWalRecords"
                Write-Host "  WAL buffers full:  $deltaWalBuffersFull"
                Write-Host "  Скорость WAL:      $walMbPerMin MB/min"
            }

            Write-Host ""
            Write-Host "CSV: $csvPath"

            # ---------------------------------------------------------
            # Сохраняем текущие показатели
            # ---------------------------------------------------------

            $previousTimestamp = $capturedAt
            $previousWalBytes = $walBytes
            $previousWalRecords = $walRecords
            $previousWalBuffersFull = $walBuffersFull
            $previousTotalBytes = $totalBytes
        }
        catch {
            Write-Warning "Ошибка замера: $($_.Exception.Message)"
        }

        Start-Sleep -Seconds $IntervalSeconds
    }
}
finally {

    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue

    Write-Host ""
    Write-Host "Мониторинг завершён."
    Write-Host "CSV сохранён:"
    Write-Host $csvPath
}