param(
    [string]$PsqlPath = "C:\Program Files\PostgreSQL\17\bin\psql.exe",
    [string]$HostName = "localhost",
    [string]$Database = "wms_analysis",
    [string]$User = "postgres",

    [string]$TableName = "raw_ax.alk_markserial",
    [string]$KeyColumn = "recid",

    [string]$LowerBound = "5637144576",
    [string]$UpperBound = "5637644576",

    [string]$OutputDir = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\data"
)


$ErrorActionPreference = "Stop"


New-Item `
    -ItemType Directory `
    -Path $OutputDir `
    -Force | Out-Null


$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

$csvFile = Join-Path `
    $OutputDir `
    "raw_dds_chunk_analysis_$timestamp.csv"


$query = @"

SELECT
    'environment' AS section,
    'timestamp' AS metric,
    clock_timestamp()::text AS value

UNION ALL

SELECT
    'table',
    'table_name',
    '$TableName'


UNION ALL

SELECT
    'chunk',
    'range',
    '$LowerBound - $UpperBound'


UNION ALL


SELECT
    'plan_text',
    'explain',
    replace(
        string_agg(plan,' | '),
        E'\n',
        ' '
    )
FROM (
    EXPLAIN
    SELECT
        recid
    FROM raw_ax.alk_markserial
    WHERE recid >= '$LowerBound'
      AND recid < '$UpperBound'
) x(plan)


UNION ALL


SELECT
    'chunk_test',
    'rows',
    count(*)::text
FROM raw_ax.alk_markserial
WHERE recid >= '$LowerBound'
AND recid < '$UpperBound'


UNION ALL


SELECT
    'chunk_test',
    'min_recid',
    min(recid)
FROM raw_ax.alk_markserial
WHERE recid >= '$LowerBound'
AND recid < '$UpperBound'


UNION ALL


SELECT
    'chunk_test',
    'max_recid',
    max(recid)
FROM raw_ax.alk_markserial
WHERE recid >= '$LowerBound'
AND recid < '$UpperBound'


UNION ALL


SELECT
    'index',
    'index_definition',
    indexdef
FROM pg_indexes
WHERE schemaname='raw_ax'
AND tablename='alk_markserial'


;

"@



$analyzeQuery = @"

EXPLAIN (ANALYZE, BUFFERS, VERBOSE)
SELECT
    recid::bigint AS recid
FROM raw_ax.alk_markserial
WHERE recid >= '$LowerBound'
AND recid < '$UpperBound';


"@



function Run-Psql($sql)
{

    & $PsqlPath `
        -h $HostName `
        -d $Database `
        -U $User `
        --csv `
        --tuples-only `
        -c $sql

}



"section,metric,value" |
Set-Content `
    -Path $csvFile `
    -Encoding UTF8



Write-Host ""
Write-Host "RAW -> DDS chunk analysis"
Write-Host "Table: $TableName"
Write-Host "Range:"
Write-Host "$LowerBound - $UpperBound"
Write-Host ""



$result = Run-Psql $query


$result |
Add-Content `
    -Path $csvFile `
    -Encoding UTF8



Write-Host ""
Write-Host "Running EXPLAIN ANALYZE..."
Write-Host "This scans only one chunk."
Write-Host ""


$analyzeResult = Run-Psql $analyzeQuery


foreach($line in $analyzeResult)
{
    Add-Content `
        -Path $csvFile `
        -Value (
            "analyze,plan," +
            ($line -replace '"','""')
        )
}



Write-Host ""
Write-Host "Completed."
Write-Host ""
Write-Host "CSV:"
Write-Host $csvFile