# ==========================================
# Очистка Python cache
# ==========================================

$projectPath = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\ax_to_postgres_etl"

Set-Location $projectPath

Write-Host "Cleaning Python cache..."

# удалить __pycache__
Get-ChildItem -Path $projectPath `
    -Recurse `
    -Directory `
    -Filter "__pycache__" `
    -ErrorAction SilentlyContinue |
Remove-Item -Recurse -Force


# удалить *.pyc
Get-ChildItem -Path $projectPath `
    -Recurse `
    -Filter "*.pyc" `
    -ErrorAction SilentlyContinue |
Remove-Item -Force


Write-Host "Cache cleared"