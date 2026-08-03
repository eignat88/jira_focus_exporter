param(
    [string]$PgHost   = "localhost",
    [int]$PgPort      = 5432,
    [string]$Database = "wms_analysis",
    [string]$PgUser   = "postgres"
)

$ErrorActionPreference = "Stop"

$ProjectDir = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2"
$LogDir = Join-Path $ProjectDir "logs"

# При необходимости укажите точный путь к psql.exe.
$PsqlExe = "C:\Program Files\PostgreSQL\17\bin\psql.exe"

if (-not (Test-Path $PsqlExe)) {
    throw "Не найден psql.exe: $PsqlExe"
}

if (-not (Test-Path $LogDir)) {
    New-Item -Path $LogDir -ItemType Directory -Force | Out-Null
}

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

$AllColumnsFile = Join-Path $LogDir "purchtable_all_columns_$Timestamp.csv"
$CandidateColumnsFile = Join-Path $LogDir "purchtable_mapping_candidates_$Timestamp.csv"
$ReportFile = Join-Path $LogDir "purchtable_preflight_diagnostics_$Timestamp.log"

$AllColumnsSql = @"
SELECT
    ordinal_position,
    column_name,
    data_type,
    udt_name,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'raw_ax'
  AND table_name = 'purchtable'
ORDER BY ordinal_position;
"@

$CandidateColumnsSql = @"
SELECT
    ordinal_position,
    column_name,
    data_type,
    udt_name,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'raw_ax'
  AND table_name = 'purchtable'
  AND (
       column_name ILIKE '%vend%'
    OR column_name ILIKE '%account%'
    OR column_name ILIKE '%date%'
    OR column_name ILIKE '%purch%'
    OR column_name ILIKE '%delivery%'
    OR column_name ILIKE '%created%'
  )
ORDER BY ordinal_position;
"@

$TableInfoSql = @"
SELECT
    current_database() AS database_name,
    current_user AS database_user,
    to_regclass('raw_ax.purchtable') AS source_table,
    pg_size_pretty(
        pg_total_relation_size('raw_ax.purchtable')
    ) AS total_size;
"@

try {
    @(
        "PURCHTABLE COLUMN DIAGNOSTICS"
        "Started:  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        "Host:     $PgHost"
        "Port:     $PgPort"
        "Database: $Database"
        "User:     $PgUser"
        ""
        "=== CONNECTION AND TABLE ==="
    ) | Set-Content -Path $ReportFile -Encoding UTF8

    & $PsqlExe `
        --host=$PgHost `
        --port=$PgPort `
        --username=$PgUser `
        --dbname=$Database `
        --set=ON_ERROR_STOP=1 `
        --command=$TableInfoSql 2>&1 |
        Tee-Object -FilePath $ReportFile -Append

    if ($LASTEXITCODE -ne 0) {
        throw "Ошибка проверки подключения или таблицы. Код psql: $LASTEXITCODE"
    }

    & $PsqlExe `
        --host=$PgHost `
        --port=$PgPort `
        --username=$PgUser `
        --dbname=$Database `
        --set=ON_ERROR_STOP=1 `
        --csv `
        --command=$AllColumnsSql |
        Set-Content -Path $AllColumnsFile -Encoding UTF8

    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось получить список колонок. Код psql: $LASTEXITCODE"
    }

    & $PsqlExe `
        --host=$PgHost `
        --port=$PgPort `
        --username=$PgUser `
        --dbname=$Database `
        --set=ON_ERROR_STOP=1 `
        --csv `
        --command=$CandidateColumnsSql |
        Set-Content -Path $CandidateColumnsFile -Encoding UTF8

    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось получить колонки-кандидаты. Код psql: $LASTEXITCODE"
    }

    @(
        ""
        "=== RESULT FILES ==="
        "All columns:        $AllColumnsFile"
        "Mapping candidates: $CandidateColumnsFile"
        ""
        "Completed: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        "Status: COMPLETED"
    ) | Add-Content -Path $ReportFile -Encoding UTF8

    Write-Host ""
    Write-Host "Проверка завершена успешно." -ForegroundColor Green
    Write-Host "Все колонки:       $AllColumnsFile"
    Write-Host "Кандидаты mapping: $CandidateColumnsFile"
    Write-Host "Общий лог:         $ReportFile"
}
catch {
    @(
        ""
        "Failed: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        "Status: FAILED"
        "Error: $($_.Exception.Message)"
    ) | Add-Content -Path $ReportFile -Encoding UTF8

    Write-Error $_.Exception.Message
    exit 1
}