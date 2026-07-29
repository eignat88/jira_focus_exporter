param(
    [string]$PsqlPath =
        "C:\Program Files\PostgreSQL\17\bin\psql.exe",

    [string]$HostName =
        "localhost",

    [string]$Port =
        "5432",

    [string]$Database =
        "wms_analysis",

    [string]$User =
        "postgres",


    [int64]$SourceRows =
        151817640,


    [string]$SchemaName =
        "benchmark",


    [string]$TableName =
        "alk_markserial_test",


    [int]$IntervalSeconds =
        60,


    [string]$OutputDir =
        "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\data"
)



$ErrorActionPreference = "Stop"



# ==========================================================
# Init
# ==========================================================

New-Item `
    -ItemType Directory `
    -Path $OutputDir `
    -Force |
    Out-Null



$timestamp =
    Get-Date -Format "yyyyMMdd_HHmmss"



$CsvFile =
    Join-Path `
        $OutputDir `
        "raw_dds_load_monitor_$timestamp.csv"



# ==========================================================
# PostgreSQL query
# NO COUNT(*)
# NO FULL SCAN
# ==========================================================

$query = @"

WITH table_stat AS
(
    SELECT
        n_live_tup AS loaded_rows
    FROM pg_stat_user_tables
    WHERE
        schemaname = '$SchemaName'
    AND
        relname = '$TableName'
),


size_stat AS
(
    SELECT

        pg_total_relation_size(
            '$SchemaName.$TableName'
        )
        AS total_bytes,


        pg_indexes_size(
            '$SchemaName.$TableName'
        )
        AS index_bytes
),


wal_stat AS
(
    SELECT
        wal_bytes,
        wal_records,
        wal_buffers_full
    FROM pg_stat_wal
),


activity AS
(
    SELECT

        count(*) FILTER
        (
            WHERE query ILIKE '%INSERT%'
        )
        AS insert_active,


        max(
            clock_timestamp() - query_start
        )
        FILTER
        (
            WHERE query ILIKE '%INSERT%'
        )
        AS insert_runtime

    FROM pg_stat_activity
),


vacuum_stat AS
(
    SELECT
        count(*) AS vacuum_running
    FROM pg_stat_progress_vacuum
)


SELECT

clock_timestamp() AS timestamp,


COALESCE(
    t.loaded_rows,
    0
)
AS loaded_rows,


s.total_bytes,

s.index_bytes,


w.wal_bytes,

w.wal_records,

w.wal_buffers_full,


a.insert_active,

a.insert_runtime,


v.vacuum_running


FROM table_stat t

CROSS JOIN size_stat s

CROSS JOIN wal_stat w

CROSS JOIN activity a

CROSS JOIN vacuum_stat v;


"@



# ==========================================================
# CSV header
# ==========================================================

"timestamp;loaded_rows;progress_percent;rows_per_sec;eta_minutes;total_bytes;index_bytes;wal_bytes;wal_records;wal_buffers_full;insert_active;insert_runtime;vacuum_running" |
Set-Content `
    -Path $CsvFile `
    -Encoding UTF8



Write-Host ""
Write-Host "============================================="
Write-Host "RAW -> DDS LOAD MONITOR"
Write-Host "============================================="
Write-Host ""
Write-Host "Table:"
Write-Host "$SchemaName.$TableName"
Write-Host ""
Write-Host "Source rows:"
Write-Host $SourceRows
Write-Host ""
Write-Host "CSV:"
Write-Host $CsvFile
Write-Host ""
Write-Host "Interval:"
Write-Host "$IntervalSeconds sec"
Write-Host ""
Write-Host "Stop: Ctrl+C"
Write-Host ""



[int64]$PreviousRows = 0

$PreviousTime =
    Get-Date



# ==========================================================
# Loop
# ==========================================================

while($true)
{

    try
    {


        $raw =
            & $PsqlPath `
                -h $HostName `
                -p $Port `
                -U $User `
                -d $Database `
                --csv `
                --tuples-only `
                -c $query



        if($LASTEXITCODE -ne 0)
        {
            Write-Warning "psql error"

            Start-Sleep `
                -Seconds $IntervalSeconds

            continue
        }



        # Fix PowerShell array

        $line =
            ($raw -join "").Trim()



        if(
            [string]::IsNullOrWhiteSpace($line)
        )
        {
            Write-Warning "Empty result"

            Start-Sleep `
                -Seconds $IntervalSeconds

            continue
        }



        $data =
            $line |
            ConvertFrom-Csv `
            -Header `
            @(
                "timestamp",
                "loaded_rows",
                "total_bytes",
                "index_bytes",
                "wal_bytes",
                "wal_records",
                "wal_buffers_full",
                "insert_active",
                "insert_runtime",
                "vacuum_running"
            )



        $data =
            $data |
            Select-Object -First 1



        [int64]$LoadedRows =
            $data.loaded_rows



        $Now =
            Get-Date



        [double]$RowsPerSec = 0

        [double]$EtaMinutes = 0



        if($PreviousRows -gt 0)
        {

            $seconds =
                (
                    $Now -
                    $PreviousTime
                ).TotalSeconds



            if($seconds -gt 0)
            {

                $RowsPerSec =
                    (
                        $LoadedRows -
                        $PreviousRows
                    )
                    /
                    $seconds



                $Remaining =
                    $SourceRows -
                    $LoadedRows



                if($RowsPerSec -gt 0)
                {

                    $EtaMinutes =
                        $Remaining /
                        $RowsPerSec /
                        60

                }

            }

        }



        $Progress =
            $LoadedRows *
            100 /
            $SourceRows



        $csvLine =
        @(
            $data.timestamp

            $LoadedRows

            (
                $Progress.ToString(
                    "0.00",
                    [Globalization.CultureInfo]::InvariantCulture
                )
            )

            (
                $RowsPerSec.ToString(
                    "0.00",
                    [Globalization.CultureInfo]::InvariantCulture
                )
            )

            (
                $EtaMinutes.ToString(
                    "0.00",
                    [Globalization.CultureInfo]::InvariantCulture
                )
            )

            $data.total_bytes

            $data.index_bytes

            $data.wal_bytes

            $data.wal_records

            $data.wal_buffers_full

            $data.insert_active

            $data.insert_runtime

            $data.vacuum_running

        ) -join ";"



        Add-Content `
            -Path $CsvFile `
            -Value $csvLine `
            -Encoding UTF8



        Write-Host (

            "{0} Loaded {1:N0} rows | {2:N2}% | {3:N0} rows/sec | ETA {4:N1} min | INSERT {5} | VACUUM {6}" -f

            (
                Get-Date -Format "HH:mm:ss"
            ),

            $LoadedRows,

            $Progress,

            $RowsPerSec,

            $EtaMinutes,

            $data.insert_active,

            $data.vacuum_running

        )



        $PreviousRows =
            $LoadedRows


        $PreviousTime =
            $Now

    }
    catch
    {

        Write-Warning $_.Exception.Message

    }



    Start-Sleep `
        -Seconds $IntervalSeconds

}