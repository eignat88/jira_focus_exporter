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

    [string]$TableName =
        "benchmark.alk_markserial_test",

    [int64]$SourceRows =
        151817640,

    [int]$IntervalSeconds =
        15,

    [string]$OutputDir =
        "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\data"
)


$ErrorActionPreference = "Stop"


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
        "benchmark_progress_$timestamp.csv"



# ======================================================
# SQL
# ======================================================

$query = @"

SELECT

    clock_timestamp() AS timestamp,

    reltuples::bigint AS estimated_rows,

    pg_total_relation_size(
        '$TableName'
    ) AS total_bytes,


    pg_indexes_size(
        '$TableName'
    ) AS index_bytes


FROM pg_class

WHERE oid =
'$TableName'::regclass;

"@



# ======================================================
# CSV HEADER
# ======================================================

"timestamp;estimated_rows;progress_percent;rows_per_sec;eta_minutes;total_mb;index_mb" |
Set-Content `
    -Path $CsvFile `
    -Encoding UTF8



Write-Host ""
Write-Host "======================================"
Write-Host "BENCHMARK LOAD MONITOR"
Write-Host "======================================"
Write-Host ""

Write-Host "Table:"
Write-Host $TableName

Write-Host ""
Write-Host "Interval:"
Write-Host "$IntervalSeconds sec"

Write-Host ""
Write-Host "CSV:"
Write-Host $CsvFile

Write-Host ""
Write-Host "Stop Ctrl+C"
Write-Host ""



[int64]$PreviousRows = 0

$PreviousTime =
    Get-Date



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



        $line =
            ($raw -join "").Trim()



        if(
            [string]::IsNullOrWhiteSpace($line)
        )
        {
            Start-Sleep $IntervalSeconds
            continue
        }



        $data =
            $line |
            ConvertFrom-Csv `
            -Header `
            @(
                "timestamp",
                "estimated_rows",
                "total_bytes",
                "index_bytes"
            )



        $data =
            $data |
            Select-Object -First 1



        [int64]$Rows =
            $data.estimated_rows



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
                        $Rows -
                        $PreviousRows
                    )
                    /
                    $seconds



                $Remain =
                    $SourceRows -
                    $Rows



                if($RowsPerSec -gt 0)
                {

                    $EtaMinutes =
                        $Remain /
                        $RowsPerSec /
                        60

                }

            }

        }



        $Progress =
            $Rows *
            100 /
            $SourceRows



        $totalMB =
            [math]::Round(
                ([double]$data.total_bytes / 1MB),
                2
            )



        $indexMB =
            [math]::Round(
                ([double]$data.index_bytes / 1MB),
                2
            )



        $csvLine =
        @(
            $data.timestamp

            $Rows

            $Progress.ToString(
                "0.00",
                [Globalization.CultureInfo]::InvariantCulture
            )

            $RowsPerSec.ToString(
                "0",
                [Globalization.CultureInfo]::InvariantCulture
            )

            $EtaMinutes.ToString(
                "0.0",
                [Globalization.CultureInfo]::InvariantCulture
            )

            $totalMB

            $indexMB

        ) -join ";"



        Add-Content `
            -Path $CsvFile `
            -Value $csvLine `
            -Encoding UTF8



        Write-Host (

        "{0} Rows {1:N0} | {2:N2}% | {3:N0} rows/sec | ETA {4:N1} min | Size {5} MB" -f

        (
            Get-Date -Format "HH:mm:ss"
        ),

        $Rows,

        $Progress,

        $RowsPerSec,

        $EtaMinutes,

        $totalMB

        )



        $PreviousRows =
            $Rows


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