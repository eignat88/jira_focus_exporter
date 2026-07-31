[CmdletBinding()]
param(
    [ValidateSet("diagnose", "alter-dds", "validate-recid", "create-index", "validate-ready")]
    [string]$Step = "diagnose",
    [string]$PsqlPath = "C:\Program Files\PostgreSQL\17\bin\psql.exe",
    [string]$HostName = "localhost",
    [int]$Port = 5432,
    [string]$Database = "wms_analysis",
    [string]$User = "postgres"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2"
$MonitoringDir = Join-Path $ProjectRoot "monitoring"
$LogDir = Join-Path $ProjectRoot "logs\3"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$Map = @{
    "diagnose"       = "01_diagnose_sales_order_mapping.sql"
    "alter-dds"      = "02_add_source_recid_to_dds.sql"
    "validate-recid" = "03_validate_salestable_recid.sql"
    "create-index"   = "04_create_salestable_recid_bigint_index.sql"
    "validate-ready" = "05_validate_sales_order_ready.sql"
}

$SqlFile = Join-Path $MonitoringDir $Map[$Step]
if (-not (Test-Path $SqlFile)) {
    throw "SQL file not found: $SqlFile"
}

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir "sales_order_prepare_${Step}_${Timestamp}.log"

Write-Host "Step: $Step"
Write-Host "SQL:  $SqlFile"
Write-Host "Log:  $LogFile"

& $PsqlPath `
    -X `
    -v ON_ERROR_STOP=1 `
    -h $HostName `
    -p $Port `
    -U $User `
    -d $Database `
    -f $SqlFile 2>&1 |
    Tee-Object -FilePath $LogFile

if ($LASTEXITCODE -ne 0) {
    throw "psql failed with exit code $LASTEXITCODE"
}
