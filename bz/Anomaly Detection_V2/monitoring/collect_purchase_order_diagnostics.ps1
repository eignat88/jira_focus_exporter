[CmdletBinding()]
param(
    [string]$PgHost = "localhost",
    [ValidateRange(1, 65535)]
    [int]$PgPort = 5432,
    [string]$Database = "wms_analysis",
    [string]$PgUser = "postgres",
    [string]$PsqlPath = "psql.exe",
    [string]$SqlFile = "",
    [string]$LogDirectory = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Do not use $PSScriptRoot in param() default expressions: when the script is
# started through powershell.exe -File, it can still be empty at that point.
$ScriptDirectory = Split-Path -Parent $PSCommandPath

if ([string]::IsNullOrWhiteSpace($SqlFile)) {
    $SqlFile = Join-Path $ScriptDirectory "purchase_order_diagnostic.sql"
}

if ([string]::IsNullOrWhiteSpace($LogDirectory)) {
    $LogDirectory = Join-Path $ScriptDirectory "..\logs\4"
}

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$CsvFile = Join-Path $LogDirectory "purchase_order_diagnostic_$Timestamp.csv"
$LogFile = Join-Path $LogDirectory "purchase_order_diagnostic_$Timestamp.log"
$PsqlOutputFile = Join-Path $env:TEMP "purchase_order_diagnostic_$Timestamp.tmp.csv"
$PsqlErrorFile = Join-Path $env:TEMP "purchase_order_diagnostic_$Timestamp.stderr.log"

function Write-RunLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,
        [ValidateSet("INFO", "WARNING", "ERROR")]
        [string]$Level = "INFO"
    )

    $Line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $Line
    Add-Content -LiteralPath $LogFile -Value $Line -Encoding UTF8
}

function Invoke-PsqlScalar {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Query
    )

    $Result = & $PsqlPath `
        -X `
        --no-password `
        --host=$PgHost `
        --port=$PgPort `
        --dbname=$Database `
        --username=$PgUser `
        --tuples-only `
        --no-align `
        --quiet `
        --set=ON_ERROR_STOP=1 `
        --command=$Query 2>&1

    if ($LASTEXITCODE -ne 0) {
        throw "psql scalar query failed: $($Result -join [Environment]::NewLine)"
    }

    return (($Result | Select-Object -First 1).ToString()).Trim()
}

try {
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    Set-Content -LiteralPath $LogFile -Value "" -Encoding UTF8

    Write-RunLog "Starting read-only purchase_order diagnostics."
    Write-RunLog "PostgreSQL: ${PgHost}:${PgPort}/${Database}; user: $PgUser"

    if (-not (Test-Path -LiteralPath $SqlFile -PathType Leaf)) {
        throw "SQL file not found: $SqlFile"
    }

    $PsqlCommand = Get-Command $PsqlPath -ErrorAction Stop
    $PsqlPath = $PsqlCommand.Source
    Write-RunLog "psql: $PsqlPath"
    Write-RunLog "SQL file: $SqlFile"

    & $PsqlPath `
        -X `
        --no-password `
        --host=$PgHost `
        --port=$PgPort `
        --dbname=$Database `
        --username=$PgUser `
        --csv `
        --quiet `
        --set=ON_ERROR_STOP=1 `
        --file=$SqlFile `
        --output=$PsqlOutputFile 2> $PsqlErrorFile

    $PsqlExitCode = $LASTEXITCODE

    if (Test-Path -LiteralPath $PsqlErrorFile) {
        $PsqlMessages = @(Get-Content -LiteralPath $PsqlErrorFile -ErrorAction SilentlyContinue)
        foreach ($Message in $PsqlMessages) {
            if (-not [string]::IsNullOrWhiteSpace($Message)) {
                Write-RunLog $Message "WARNING"
            }
        }
    }

    if ($PsqlExitCode -ne 0) {
        throw "psql finished with exit code $PsqlExitCode. See log: $LogFile"
    }

    if (-not (Test-Path -LiteralPath $PsqlOutputFile -PathType Leaf)) {
        throw "psql did not create the temporary CSV file."
    }

    $Rows = @(Import-Csv -LiteralPath $PsqlOutputFile)
    if ($Rows.Count -eq 0) {
        throw "Diagnostic query returned no rows."
    }

    # PostgreSQL has no built-in function that returns free filesystem bytes.
    # If PostgreSQL is local, add free space from the Windows drive that hosts data_directory.
    try {
        $DataDirectory = Invoke-PsqlScalar -Query "SELECT current_setting('data_directory');"
        $DriveRoot = [System.IO.Path]::GetPathRoot($DataDirectory)

        if ([string]::IsNullOrWhiteSpace($DriveRoot)) {
            throw "Cannot determine the drive root from data_directory: $DataDirectory"
        }

        $DriveName = $DriveRoot.TrimEnd('\').TrimEnd(':')
        $Drive = Get-PSDrive -Name $DriveName -PSProvider FileSystem -ErrorAction Stop
        $FreeBytes = [int64]$Drive.Free
        $UsedBytes = [int64]$Drive.Used
        $TotalBytes = $FreeBytes + $UsedBytes
        $FreeGiB = [math]::Round($FreeBytes / 1GB, 2)
        $FreePercent = if ($TotalBytes -gt 0) {
            [math]::Round(($FreeBytes / $TotalBytes) * 100, 2)
        } else {
            0
        }

        $Rows += [pscustomobject]@{
            diagnostic_time = (Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff zzz")
            section_order   = "14"
            section         = "FILESYSTEM"
            object_name     = $DriveRoot
            metric          = "free_disk_space"
            status          = if ($FreePercent -lt 10) { "WARNING_LOW_SPACE" } else { "INFO" }
            value           = "$FreeGiB GiB"
            details         = (@{
                data_directory = $DataDirectory
                free_bytes     = $FreeBytes
                used_bytes     = $UsedBytes
                total_bytes    = $TotalBytes
                free_percent   = $FreePercent
            } | ConvertTo-Json -Compress)
        }

        Write-RunLog "Filesystem free space: $FreeGiB GiB ($FreePercent%) on $DriveRoot"
    }
    catch {
        $DiskError = $_.Exception.Message
        $Rows += [pscustomobject]@{
            diagnostic_time = (Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff zzz")
            section_order   = "14"
            section         = "FILESYSTEM"
            object_name     = "data_directory"
            metric          = "free_disk_space"
            status          = "NOT_AVAILABLE"
            value           = ""
            details         = (@{ error = $DiskError } | ConvertTo-Json -Compress)
        }
        Write-RunLog "Could not determine Windows free disk space: $DiskError" "WARNING"
    }

    $Rows | Export-Csv -LiteralPath $CsvFile -NoTypeInformation -Encoding UTF8

    $OverallStatus = ($Rows | Where-Object {
        $_.section -eq "SUMMARY" -and $_.metric -eq "overall_status"
    } | Select-Object -First 1).status

    $MissingColumns = @($Rows | Where-Object {
        $_.section -eq "REQUIRED_SOURCE_COLUMNS" -and $_.status -eq "MISSING"
    }).Count

    $WaitingLocks = @($Rows | Where-Object {
        $_.section -eq "TABLE_LOCKS" -and $_.status -eq "WAITING"
    }).Count

    Write-RunLog "Overall status: $OverallStatus"
    Write-RunLog "Missing required source columns: $MissingColumns"
    Write-RunLog "Waiting table locks: $WaitingLocks"
    Write-RunLog "CSV created: $CsvFile"
    Write-RunLog "Diagnostics completed successfully."

    Write-Host ""
    Write-Host "Result: $OverallStatus" -ForegroundColor Cyan
    Write-Host "CSV:    $CsvFile" -ForegroundColor Green
    Write-Host "Log:    $LogFile" -ForegroundColor Green
}
catch {
    $ErrorMessage = $_.Exception.Message

    if (-not (Test-Path -LiteralPath $LogDirectory)) {
        New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    }
    if (-not (Test-Path -LiteralPath $LogFile)) {
        Set-Content -LiteralPath $LogFile -Value "" -Encoding UTF8
    }

    Write-RunLog $ErrorMessage "ERROR"
    Write-Error $ErrorMessage
    exit 1
}
finally {
    Remove-Item -LiteralPath $PsqlOutputFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PsqlErrorFile -Force -ErrorAction SilentlyContinue
}
