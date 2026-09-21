param(
    [ValidateSet('Start','Open','Status','StopWorker')][string]$Action='Open',
    [string]$PlatformRoot='E:\jvjv\local-platform'
)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$queue=Join-Path $repoRoot '.local/mqtt-queue'
$worker=Join-Path $PSScriptRoot 'bridge.py'
$recordPath=Join-Path $queue 'worker.json'
function Get-Worker {
    if(-not (Test-Path -LiteralPath $recordPath)) { return $null }
    $record=Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    $process=Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$record.pid)"
    if(-not $process) { return $null }
    if(-not $process.CommandLine.Contains($worker) -or -not $process.CommandLine.Contains($queue)) {
        throw 'Recorded process identity differs; no process changed'
    }
    $started=[DateTimeOffset]::FromUnixTimeSeconds([long][Math]::Floor($record.started_at)).UtcDateTime
    $created=([datetime]$process.CreationDate).ToUniversalTime()
    if($created -gt $started.AddSeconds(2) -or ($started-$created).TotalMinutes -gt 5) {
        throw 'Recorded process creation time differs; no process changed'
    }
    return $record
}
$existing=Get-Worker
if($Action -eq 'Status') {
    if($existing) { Write-Output "Komodo Windows worker PID $($existing.pid)" }
    else { Write-Output 'Komodo Windows worker is offline' }
    exit 0
}
if($Action -eq 'StopWorker') {
    if(-not $existing) { exit 0 }
    # Worker finishes already accepted tasks before honoring this request.
    [IO.File]::WriteAllText((Join-Path $queue 'stop'),'stop when idle')
    Write-Output 'Graceful stop requested; active releases will finish'
    exit 0
}
docker compose --env-file (Join-Path $repoRoot '.local/komodo.env') -f (Join-Path $PSScriptRoot 'compose.yaml') up -d --pull never
if($LASTEXITCODE -ne 0) { throw 'Komodo Compose startup failed' }
if(-not $existing) {
    if(Test-Path -LiteralPath (Join-Path $queue 'stop')) { Remove-Item -LiteralPath (Join-Path $queue 'stop') }
    $python=(Get-Command python -CommandType Application | Select-Object -First 1).Source
    $arguments=@('-X','utf8',('"'+$worker+'"'),'--root',('"'+$PlatformRoot+'"'),'--queue',('"'+$queue+'"'))
    $process=Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $queue 'worker.out.log') -RedirectStandardError (Join-Path $queue 'worker.err.log')
    $ready=$false
    for($i=0;$i -lt 60;$i++) {
        if($process.HasExited) { throw 'Worker exited; inspect .local/mqtt-queue/worker.err.log' }
        if(Test-Path -LiteralPath $recordPath) {
            $record=Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
            if([int]$record.pid -eq $process.Id) { $ready=$true; break }
        }
        Start-Sleep -Milliseconds 250
    }
    if(-not $ready) { throw 'Worker readiness timed out' }
}
if($Action -eq 'Open') { Start-Process 'http://127.0.0.1:28793' }
Write-Output 'Komodo: http://127.0.0.1:28793'
