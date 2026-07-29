# Watch DDS Progress v2 - Compact dashboard
# Open in separate terminal while loading

$ProjectRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection"
$PsqlPath = "C:\Program Files\PostgreSQL\17\bin\psql.exe"

if (-not (Test-Path $PsqlPath)) {
    $PsqlPath = "psql"
}

function Get-ProgressBar {
    param([float]$Percent, [int]$Width = 30)
    
    $filled = [math]::Round($Width * $Percent / 100)
    $empty = $Width - $filled
    
    $bar = "[" + ("█" * $filled) + ("░" * $empty) + "] {0:N2}%" -f $Percent
    return $bar
}

Write-Host "DDS Progress Monitor v2 - Press Ctrl+C to stop"
Write-Host ""

while ($true) {
    Clear-Host
    
    $timestamp = Get-Date -Format "dd.MM.yyyy HH:mm:ss"
    Write-Host "=" * 60
    Write-Host "DDS PROGRESS MONITOR"
    Write-Host "Last update: $timestamp MSK"
    Write-Host "=" * 60
    Write-Host ""
    
    # Get pipeline status
    $query = @"
SELECT 
    run_id, status, current_stage_no, current_stage_name,
    completed_stages, total_stages,
    total_processed_rows, total_source_rows, total_progress_pct
FROM etl.pipeline_run 
WHERE pipeline_name = 'DDS_POPULATE' 
ORDER BY run_id DESC LIMIT 1;
"@
    
    $result = & $PsqlPath -h localhost -p 5432 -U postgres -d wms_analysis -t -A -F "|" -c $query 2>$null
    
    if ($result) {
        $parts = $result -split '\|'
        $runId = $parts[0]
        $status = $parts[1]
        $currentStage = $parts[2]
        $currentStageName = $parts[3]
        $completedStages = $parts[4]
        $totalStages = $parts[5]
        $processedRows = [long]$parts[6]
        $sourceRows = [long]$parts[7]
        $progressPct = [float]$parts[8]
        
        Write-Host "Run ID: $runId"
        Write-Host "Pipeline: DDS_POPULATE"
        Write-Host "Status: $status"
        Write-Host ""
        
        # Pipeline progress
        $pipelineBar = Get-ProgressBar -Percent $progressPct
        Write-Host "PIPELINE"
        Write-Host $pipelineBar
        Write-Host "Этапов: $completedStages / $totalStages"
        Write-Host "Строк: $($processedRows.ToString('N0')) / $($sourceRows.ToString('N0'))"
        Write-Host ""
        
        # Current stage
        if ($currentStage -and $currentStageName) {
            $stageQuery = @"
SELECT 
    processed_rows, source_rows, progress_pct, 
    rows_per_second, eta_seconds, completed_batches, total_batches,
    heartbeat_at
FROM etl.stage_progress 
WHERE run_id = $runId AND stage_no = $currentStage;
"@
            $stageResult = & $PsqlPath -h localhost -p 5432 -U postgres -d wms_analysis -t -A -F "|" -c $stageQuery 2>$null
            
            if ($stageResult) {
                $sParts = $stageResult -split '\|'
                $sProcessed = [long]$sParts[0]
                $sSource = [long]$sParts[1]
                $sProgress = [float]$sParts[2]
                $sSpeed = [math]::Round([float]$sParts[3])
                $sEta = $sParts[4]
                $sCompletedBatches = $sParts[5]
                $sTotalBatches = $sParts[6]
                $sHeartbeat = $sParts[7]
                
                $stageBar = Get-ProgressBar -Percent $sProgress
                Write-Host "CURRENT STAGE"
                Write-Host "Этап $currentStage/$totalStages`: $currentStageName"
                Write-Host $stageBar
                Write-Host "Пакетов: $sCompletedBatches / $sTotalBatches"
                Write-Host "Строки: $($sProcessed.ToString('N0')) / $($sSource.ToString('N0'))"
                Write-Host "Скорость: $($sSpeed.ToString('N0')) строк/с"
                
                if ($sEta -and $sEta -gt 0) {
                    $etaTime = (Get-Date).AddSeconds($sEta)
                    Write-Host "ETA: $($etaTime.ToString('HH:mm')) MSK"
                }
                
                if ($sHeartbeat) {
                    Write-Host "Heartbeat: $sHeartbeat"
                }
            }
        }
    } else {
        Write-Host "No active pipeline found"
    }
    
    Write-Host ""
    Write-Host "Press Ctrl+C to stop. Next update in 10 seconds..."
    
    Start-Sleep -Seconds 10
}
