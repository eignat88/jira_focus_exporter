param(
    [string]$Mode = "focus",
    [string]$Issue = "",
    [string]$Project = "",
    [int]$Days = 0,
    [int]$StaleDays = 0
)

$ProjectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonPath = Join-Path $ProjectPath ".venv\Scripts\python.exe"
$MainPath = Join-Path $ProjectPath "main.py"

Set-Location $ProjectPath

$args = @($MainPath, "--mode", $Mode)

if ($Issue) { $args += "--issue", $Issue }
if ($Project) { $args += "--project", $Project }
if ($Days -gt 0) { $args += "--days", $Days }
if ($StaleDays -gt 0) { $args += "--stale-days", $StaleDays }

& $PythonPath @args
exit $LASTEXITCODE
