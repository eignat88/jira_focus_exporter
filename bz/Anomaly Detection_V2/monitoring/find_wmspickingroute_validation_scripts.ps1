# find_wmspickingroute_validation_scripts_fixed.ps1
# Read-only search for existing WMSPICKINGROUTE / picking_route validation scripts.

param(
    [string]$ProjectRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    throw "Project directory not found: $ProjectRoot"
}

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$OutputDir = Join-Path $ProjectRoot "logs"
$OutputCsv = Join-Path $OutputDir "wmspickingroute_existing_scripts_$Timestamp.csv"
$OutputTxt = Join-Path $OutputDir "wmspickingroute_existing_scripts_$Timestamp.txt"

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$ExcludedParts = @(
    "\.git\",
    "\.venv\",
    "\venv\",
    "\__pycache__\",
    "\node_modules\",
    "\archive\",
    "\data\",
    "\logs\"
)

$AllowedExtensions = @(
    ".sql",
    ".ps1",
    ".py",
    ".yaml",
    ".yml",
    ".json",
    ".md",
    ".ipynb",
    ".txt"
)

$Patterns = [ordered]@{
    "WMSPICKINGROUTE"       = '(?i)\bWMSPICKINGROUTE\b'
    "RAW table"             = '(?i)raw_ax\.wmspickingroute'
    "DDS table"             = '(?i)dds\.picking_route'
    "Business key"          = '(?i)\bpickingrouteid\b|\bpicking_route_id\b'
    "Chunk key"             = '(?i)\brecid_bigint\b'
    "Source index"          = '(?i)idx_wmspickingroute_recid_bigint'
    "Target unique index"   = '(?i)ux_picking_route_picking_route_id'
    "Duplicate check"       = '(?i)having\s+count\s*\(\s*\*\s*\)\s*>\s*1|duplicate'
    "Null check"            = '(?i)is\s+null|null_recid|null_picking|empty_picking'
    "Row count"             = '(?i)count\s*\(\s*\*\s*\)|n_live_tup|n_tup_ins'
    "EXPLAIN"               = '(?i)\bEXPLAIN\b'
    "Index diagnostics"     = '(?i)pg_index|pg_indexes|indisvalid|indisready'
    "Activity diagnostics"  = '(?i)pg_stat_activity|pg_stat_progress_copy'
    "Vacuum diagnostics"    = '(?i)pg_stat_progress_vacuum|autovacuum'
    "RAW to DDS preflight"  = '(?i)preflight.*picking_route|stage\s+picking_route'
    "RAW to DDS validation" = '(?i)raw_rows|dds_rows|row_difference|validation_result'
}

Write-Host ("=" * 80)
Write-Host "Search for WMSPICKINGROUTE validation scripts"
Write-Host "Project: $ProjectRoot"
Write-Host ("=" * 80)

$Files = Get-ChildItem -LiteralPath $ProjectRoot -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $File = $_
        $Allowed = $AllowedExtensions -contains $File.Extension.ToLowerInvariant()

        $Excluded = $false
        foreach ($Part in $ExcludedParts) {
            if ($File.FullName -like "*$Part*") {
                $Excluded = $true
                break
            }
        }

        $Allowed -and -not $Excluded
    }

Write-Host "Files to inspect: $($Files.Count)"
Write-Host

$Results = New-Object System.Collections.Generic.List[object]

foreach ($File in $Files) {
    try {
        $Content = Get-Content -LiteralPath $File.FullName -Raw -Encoding UTF8 -ErrorAction Stop
    }
    catch {
        try {
            $Content = Get-Content -LiteralPath $File.FullName -Raw -ErrorAction Stop
        }
        catch {
            continue
        }
    }

    if ([string]::IsNullOrWhiteSpace($Content)) {
        continue
    }

    $MatchedChecks = New-Object System.Collections.Generic.List[string]
    $Score = 0

    foreach ($Entry in $Patterns.GetEnumerator()) {
        if ($Content -match $Entry.Value) {
            $MatchedChecks.Add($Entry.Key)

            switch ($Entry.Key) {
                "WMSPICKINGROUTE"       { $Score += 5 }
                "RAW table"             { $Score += 6 }
                "DDS table"             { $Score += 6 }
                "Business key"          { $Score += 3 }
                "Chunk key"             { $Score += 4 }
                "Duplicate check"       { $Score += 4 }
                "Null check"            { $Score += 3 }
                "EXPLAIN"               { $Score += 3 }
                "Index diagnostics"     { $Score += 3 }
                "RAW to DDS validation" { $Score += 5 }
                default                 { $Score += 1 }
            }
        }
    }

    $HasMainReference =
        ($Content -match '(?i)WMSPICKINGROUTE') -or
        ($Content -match '(?i)raw_ax\.wmspickingroute') -or
        ($Content -match '(?i)dds\.picking_route') -or
        ($Content -match '(?i)idx_wmspickingroute_recid_bigint')

    if (-not $HasMainReference) {
        continue
    }

    $RelativePath = $File.FullName.Substring($ProjectRoot.Length).TrimStart("\")

    switch -Regex ($File.Extension.ToLowerInvariant()) {
        "\.sql"   { $Category = "SQL"; break }
        "\.ps1"   { $Category = "PowerShell"; break }
        "\.py"    { $Category = "Python"; break }
        "\.ipynb" { $Category = "Notebook"; break }
        "\.ya?ml" { $Category = "Configuration"; break }
        default   { $Category = "Documentation"; break }
    }

    if (
        ($MatchedChecks -contains "RAW to DDS validation") -or
        ($MatchedChecks -contains "Duplicate check") -or
        ($MatchedChecks -contains "Null check")
    ) {
        $Purpose = "Validation"
    }
    elseif (
        ($MatchedChecks -contains "Activity diagnostics") -or
        ($MatchedChecks -contains "Vacuum diagnostics")
    ) {
        $Purpose = "Monitoring"
    }
    elseif (
        ($MatchedChecks -contains "EXPLAIN") -or
        ($MatchedChecks -contains "Index diagnostics")
    ) {
        $Purpose = "Performance diagnostics"
    }
    elseif ($MatchedChecks -contains "RAW to DDS preflight") {
        $Purpose = "Preflight"
    }
    else {
        $Purpose = "Configuration or ETL logic"
    }

    $Results.Add(
        [pscustomobject]@{
            Score         = $Score
            Category      = $Category
            Purpose       = $Purpose
            FileName      = $File.Name
            RelativePath  = $RelativePath
            FullPath      = $File.FullName
            Modified      = $File.LastWriteTime
            SizeKB        = [math]::Round($File.Length / 1KB, 2)
            MatchedChecks = ($MatchedChecks -join "; ")
        }
    )
}

$SortedResults = @(
    $Results |
        Sort-Object `
            @{ Expression = "Score"; Descending = $true },
            @{ Expression = "Modified"; Descending = $true }
)

if ($SortedResults.Count -eq 0) {
    Write-Warning "No matching scripts found."

    @"
Checked: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Project: $ProjectRoot

No WMSPICKINGROUTE validation scripts found.
"@ | Set-Content -LiteralPath $OutputTxt -Encoding UTF8

    Write-Host "Report: $OutputTxt"
    exit 0
}

$SortedResults |
    Export-Csv -LiteralPath $OutputCsv -Delimiter ";" -NoTypeInformation -Encoding UTF8

$ReportLines = New-Object System.Collections.Generic.List[string]
$ReportLines.Add("Checked: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$ReportLines.Add("Project: $ProjectRoot")
$ReportLines.Add("Files found: $($SortedResults.Count)")
$ReportLines.Add("")

foreach ($Item in $SortedResults) {
    $ReportLines.Add(("=" * 80))
    $ReportLines.Add("Score:       $($Item.Score)")
    $ReportLines.Add("Category:    $($Item.Category)")
    $ReportLines.Add("Purpose:     $($Item.Purpose)")
    $ReportLines.Add("File:        $($Item.RelativePath)")
    $ReportLines.Add("Modified:    $($Item.Modified)")
    $ReportLines.Add("Checks:      $($Item.MatchedChecks)")
}

$ReportLines | Set-Content -LiteralPath $OutputTxt -Encoding UTF8

Write-Host "Matching files found: $($SortedResults.Count)"
Write-Host

$SortedResults |
    Select-Object Score, Category, Purpose, RelativePath, Modified, MatchedChecks |
    Format-Table -AutoSize -Wrap

Write-Host
Write-Host "CSV: $OutputCsv"
Write-Host "TXT: $OutputTxt"
Write-Host
Write-Host "Top 5 files:"

$SortedResults |
    Select-Object -First 5 |
    ForEach-Object {
        Write-Host ("[{0}] {1}" -f $_.Score, $_.FullPath)
    }
