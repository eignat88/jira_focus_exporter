$ProjectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonPath = Join-Path $ProjectPath ".venv\Scripts\python.exe"
$MainPath = Join-Path $ProjectPath "main.py"

Set-Location $ProjectPath

& $PythonPath $MainPath
exit $LASTEXITCODE
