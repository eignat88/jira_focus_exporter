[CmdletBinding()]
param(
    [string]$PsqlPath = "C:\Program Files\PostgreSQL\17\bin\psql.exe",
    [string]$HostName = "localhost",
    [int]$Port = 5432,
    [string]$Database = "wms_analysis",
    [string]$UserName = "postgres",
    [int]$BatchSize = 500000,
    [string]$SqlFile = ".\order_trans_integration\050_load_dds_order_trans.sql",
    [string]$LogDirectory = ".\logs\2"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $PsqlPath)) {
    throw "psql.exe не найден: $PsqlPath"
}

if (-not (Test-Path -LiteralPath $SqlFile)) {
    throw "SQL-файл не найден: $SqlFile"
}

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$CsvFile = Join-Path $LogDirectory "load_dds_order_trans_$Timestamp.csv"

$Results = [System.Collections.Generic.List[object]]::new()

function Invoke-PsqlScalar {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Sql
    )

    $Output = & $PsqlPath `
        -X `
        -h $HostName `
        -p $Port `
        -U $UserName `
        -d $Database `
        -At `
        -v ON_ERROR_STOP=1 `
        -c $Sql 2>&1

    if ($LASTEXITCODE -ne 0) {
        throw ($Output -join [Environment]::NewLine)
    }

    $Lines = @(
        $Output |
            ForEach-Object { [string]$_ } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )

    if ($Lines.Count -eq 0) {
        return ""
    }

    return $Lines[-1].Trim()
}

Write-Host "============================================================"
Write-Host "FULL DDS ORDER_TRANS LOAD"
Write-Host "Batch size: $BatchSize"
Write-Host "CSV: $CsvFile"
Write-Host "Stop: Ctrl+C"
Write-Host "============================================================"

while ($true) {
    $LastLoadedRecId = Invoke-PsqlScalar @"
SELECT COALESCE(max(rec_id), 0)::text
FROM dds.order_trans;
"@

    if ($LastLoadedRecId -notmatch '^\d+$') {
        throw "Некорректный max(rec_id): [$LastLoadedRecId]"
    }

    $Bounds = Invoke-PsqlScalar @"
WITH batch AS (
    SELECT recid_bigint
    FROM stage_ax.wmsordertrans_normalized
    WHERE recid_bigint > $LastLoadedRecId
    ORDER BY recid_bigint
    LIMIT $BatchSize
)
SELECT concat_ws(
    ';',
    min(recid_bigint),
    max(recid_bigint) + 1,
    count(*)
)
FROM batch;
"@

    if ([string]::IsNullOrWhiteSpace($Bounds) -or $Bounds -eq ";;0") {
        Write-Host ""
        Write-Host "Новых строк для загрузки нет."
        Write-Host "Полная загрузка завершена."
        break
    }

    $Parts = @($Bounds -split ";")

    if ($Parts.Count -ne 3) {
        throw "Не удалось разобрать границы чанка: [$Bounds]"
    }

    $FromKey = $Parts[0].Trim()
    $ToKey = $Parts[1].Trim()
    $SourceRows = [int64]$Parts[2].Trim()

    if (
        $FromKey -notmatch '^\d+$' -or
        $ToKey -notmatch '^\d+$' -or
        $SourceRows -le 0
    ) {
        throw "Некорректный диапазон: FromKey=$FromKey ToKey=$ToKey Rows=$SourceRows"
    }

    Write-Host ""
    Write-Host "------------------------------------------------------------"
    Write-Host "Chunk: [$FromKey, $ToKey)"
    Write-Host "Rows:  $SourceRows"
    Write-Host "------------------------------------------------------------"

    $WalBefore = [decimal](Invoke-PsqlScalar @"
SELECT wal_bytes::text
FROM pg_stat_wal;
"@)

    $StartedAt = Get-Date

    try {
        & $PsqlPath `
            -X `
            -h $HostName `
            -p $Port `
            -U $UserName `
            -d $Database `
            -v ON_ERROR_STOP=1 `
            -v "from_key=$FromKey" `
            -v "to_key=$ToKey" `
            -f $SqlFile

        if ($LASTEXITCODE -ne 0) {
            throw "psql завершился с кодом $LASTEXITCODE"
        }

        $FinishedAt = Get-Date
        $DurationSeconds = [math]::Round(
            ($FinishedAt - $StartedAt).TotalSeconds,
            2
        )

        $WalAfter = [decimal](Invoke-PsqlScalar @"
SELECT wal_bytes::text
FROM pg_stat_wal;
"@)

        $WalDeltaMB = [math]::Round(
            [double]($WalAfter - $WalBefore) / 1MB,
            2
        )

        $TargetRows = [int64](Invoke-PsqlScalar @"
SELECT count(*)::text
FROM dds.order_trans
WHERE rec_id >= $FromKey
  AND rec_id < $ToKey;
"@)

        $RowsPerSecond = if ($DurationSeconds -gt 0) {
            [math]::Round($TargetRows / $DurationSeconds, 2)
        }
        else {
            0
        }

        $Status = if ($TargetRows -eq $SourceRows) {
            "COMPLETED"
        }
        else {
            "ROW_COUNT_MISMATCH"
        }

        $Results.Add([pscustomobject][ordered]@{
            timestamp        = $FinishedAt.ToString("yyyy-MM-dd HH:mm:ss")
            status           = $Status
            from_key         = $FromKey
            to_key           = $ToKey
            source_rows      = $SourceRows
            target_rows      = $TargetRows
            duration_seconds = $DurationSeconds
            rows_per_second  = $RowsPerSecond
            wal_delta_mb     = $WalDeltaMB
            error            = ""
        }) | Out-Null

        $Results |
            Export-Csv `
                -LiteralPath $CsvFile `
                -Delimiter ";" `
                -NoTypeInformation `
                -Encoding UTF8

        Write-Host "Status:          $Status"
        Write-Host "Target rows:     $TargetRows"
        Write-Host "Duration, sec:   $DurationSeconds"
        Write-Host "Rows/sec:        $RowsPerSecond"
        Write-Host "WAL delta, MB:   $WalDeltaMB"

        if ($Status -ne "COMPLETED") {
            throw "Количество строк staging и DDS не совпало."
        }
    }
    catch {
        $FinishedAt = Get-Date

        $Results.Add([pscustomobject][ordered]@{
            timestamp        = $FinishedAt.ToString("yyyy-MM-dd HH:mm:ss")
            status           = "FAILED"
            from_key         = $FromKey
            to_key           = $ToKey
            source_rows      = $SourceRows
            target_rows      = ""
            duration_seconds = [math]::Round(
                ($FinishedAt - $StartedAt).TotalSeconds,
                2
            )
            rows_per_second  = ""
            wal_delta_mb     = ""
            error            = $_.Exception.Message
        }) | Out-Null

        $Results |
            Export-Csv `
                -LiteralPath $CsvFile `
                -Delimiter ";" `
                -NoTypeInformation `
                -Encoding UTF8

        throw
    }
}

Write-Host ""
Write-Host "============================================================"
Write-Host "DDS ORDER_TRANS LOAD FINISHED"
Write-Host "CSV: $CsvFile"
Write-Host "============================================================"
