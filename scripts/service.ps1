# access-assistant Web API service manager (uv virtualenv)
#
# Usage:
#   .\scripts\service.ps1 start    # install deps + daemon supervisor + auto-restart
#   .\scripts\service.ps1 stop     # stop supervisor and worker
#   .\scripts\service.ps1 restart  # stop, install deps, then start
#   .\scripts\service.ps1 install  # install/update dependencies only (uv sync)
#   .\scripts\service.ps1 status   # show running state
#   .\scripts\service.ps1 logs     # tail worker log
#   .\scripts\service.ps1 run      # foreground (no supervisor)
#   .\scripts\service.ps1 once     # background single run (no auto-restart)

param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'logs', 'run', 'once', 'install', '_supervise')]
    [string]$Command = 'status'
)

$ErrorActionPreference = 'Stop'

$RootDir = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $RootDir '.run'
$PidFile = Join-Path $RunDir 'access-assistant-web.pid'
$SupervisorPidFile = Join-Path $RunDir 'access-assistant-web.supervisor.pid'
$LogFile = Join-Path $RunDir 'access-assistant-web.log'
$SupervisorLogFile = Join-Path $RunDir 'access-assistant-web.supervisor.log'
$RestartDelay = if ($env:RESTART_DELAY) { [int]$env:RESTART_DELAY } else { 3 }
$MaxRestarts = if ($env:MAX_RESTARTS) { [int]$env:MAX_RESTARTS } else { 0 }

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Host $line
}

function Ensure-RunDir {
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
}

function Test-ProcessRunning([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Read-PidFile([string]$Path) {
    if (-not (Test-Path $Path)) { return 0 }
    $raw = (Get-Content -Path $Path -Raw -ErrorAction SilentlyContinue)
    if ([string]::IsNullOrWhiteSpace($raw)) { return 0 }
    [int]$parsed = 0
    if ([int]::TryParse($raw.Trim(), [ref]$parsed)) { return $parsed }
    return 0
}

function Stop-ProcessTree([int]$ProcessId, [string]$Label) {
    if (-not (Test-ProcessRunning $ProcessId)) { return }
    Write-Log "stopping $Label (pid $ProcessId)"
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try {
        cmd.exe /c "taskkill /PID $ProcessId /T" 1>$null 2>$null
        for ($i = 0; $i -lt 20; $i++) {
            if (-not (Test-ProcessRunning $ProcessId)) { return }
            Start-Sleep -Milliseconds 500
        }
        Write-Log "force killing $Label tree (pid $ProcessId)"
        cmd.exe /c "taskkill /PID $ProcessId /T /F" 1>$null 2>$null
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Get-WebPort {
    if ($env:SKILLS_WEB_PORT) {
        return [int]$env:SKILLS_WEB_PORT
    }
    $envFile = Join-Path $RootDir '.env'
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match '^\s*SKILLS_WEB_PORT\s*=\s*"?(\d+)"?\s*$') {
                return [int]$Matches[1]
            }
        }
    }
    return 8000
}

function Get-PidsListeningOnPort([int]$Port) {
    $pids = New-Object 'System.Collections.Generic.HashSet[int]'
    $pattern = ":$Port\s"
    netstat -ano | Select-String $pattern | ForEach-Object {
        if ($_.Line -match 'LISTENING\s+(\d+)\s*$') {
            [void]$pids.Add([int]$Matches[1])
        }
    }
    return @($pids)
}

function Get-AccessAssistantWorkerPids {
    $marker = 'access-assistant-web'
    $pids = New-Object 'System.Collections.Generic.HashSet[int]'
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$marker*" } |
        ForEach-Object { [void]$pids.Add($_.ProcessId) }
    return @($pids)
}

function Stop-OrphanWorkers {
    foreach ($workerPid in Get-AccessAssistantWorkerPids) {
        Stop-ProcessTree $workerPid 'orphan worker'
    }
    $ports = @([int](Get-WebPort))
    if ($ports -notcontains 8000) { $ports += 8000 }
    foreach ($port in $ports) {
        foreach ($listenerPid in Get-PidsListeningOnPort $port) {
            if ($listenerPid -le 0) { continue }
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$listenerPid" -ErrorAction SilentlyContinue
            if ($proc -and $proc.CommandLine -like '*access-assistant-web*') {
                Stop-ProcessTree $listenerPid "listener on port $port"
            }
        }
    }
}

function Ensure-Uv {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'uv not found in PATH. Install uv or add it to PATH.'
    }
}

function Install-Deps {
    Ensure-Uv
    Write-Log 'installing dependencies: uv sync'
    Push-Location $RootDir
    try {
        uv sync | Out-Host
    } finally {
        Pop-Location
    }
}

function Prepare-Runtime {
    Install-Deps
}

function Stop-All {
    Ensure-RunDir
    $workerPid = Read-PidFile $PidFile
    $supervisorPid = Read-PidFile $SupervisorPidFile

    if ($workerPid -gt 0) {
        Stop-ProcessTree $workerPid 'worker'
    }
    if ($supervisorPid -gt 0) {
        Stop-ProcessTree $supervisorPid 'supervisor'
    }
    Remove-Item -Force $PidFile, $SupervisorPidFile -ErrorAction SilentlyContinue
    Stop-OrphanWorkers
    Write-Log 'stopped'
}

function Start-WorkerForeground {
    Prepare-Runtime
    Push-Location $RootDir
    try {
        uv run access-assistant-web
    } finally {
        Pop-Location
    }
}

function Start-UvWebProcess {
    $escapedLog = $LogFile.Replace("'", "''")
    $escapedRoot = $RootDir.Replace("'", "''")
    $command = "Set-Location -LiteralPath '$escapedRoot'; uv run access-assistant-web *>> '$escapedLog'"
    return Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-Command', $command) `
        -WorkingDirectory $RootDir `
        -PassThru
}

function Start-WorkerBackground {
    Prepare-Runtime
    Ensure-RunDir
    $proc = Start-UvWebProcess
    Set-Content -Path $PidFile -Value $proc.Id -NoNewline
    Write-Log "worker started in background (pid $($proc.Id), log $LogFile)"
}

function Write-SupervisorLog([string]$Message) {
    Ensure-RunDir
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $SupervisorLogFile -Value $line
}

function Start-SupervisorLoop {
    Prepare-Runtime
    Ensure-RunDir
    $restarts = 0
    Write-SupervisorLog "supervisor started (pid $PID, restart_delay=${RestartDelay}s, max_restarts=$(if ($MaxRestarts -eq 0) { 'unlimited' } else { $MaxRestarts }))"

    try {
        while ($true) {
            Write-SupervisorLog 'starting worker: uv run access-assistant-web'
            $proc = Start-UvWebProcess
            Set-Content -Path $PidFile -Value $proc.Id -NoNewline
            Wait-Process -Id $proc.Id -ErrorAction SilentlyContinue

            Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
            Write-SupervisorLog 'worker exited'

            if ($MaxRestarts -ne 0 -and $restarts -ge $MaxRestarts) {
                Write-SupervisorLog "max restarts ($MaxRestarts) reached, supervisor exiting"
                break
            }

            $restarts++
            Write-SupervisorLog "restarting in ${RestartDelay}s (restart count=$restarts)"
            Start-Sleep -Seconds $RestartDelay
        }
    } finally {
        $workerPid = Read-PidFile $PidFile
        if ($workerPid -gt 0) {
            Stop-ProcessTree $workerPid 'worker'
        }
        Stop-OrphanWorkers
        Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
    }
}

function Start-Daemon {
    Ensure-RunDir
    $supervisorPid = Read-PidFile $SupervisorPidFile
    if (Test-ProcessRunning $supervisorPid) {
        Write-Log "already running (supervisor pid $supervisorPid)"
        return
    }

    Remove-Item -Force $SupervisorPidFile, $PidFile -ErrorAction SilentlyContinue

    $supervisorScript = @"
Set-Location '$RootDir'
& '$PSCommandPath' _supervise
"@

    $tempScript = Join-Path $RunDir 'access-assistant-supervisor.ps1'
    Set-Content -Path $tempScript -Value $supervisorScript -Encoding UTF8

    $proc = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $tempScript) `
        -WorkingDirectory $RootDir `
        -PassThru

    Set-Content -Path $SupervisorPidFile -Value $proc.Id -NoNewline
    Start-Sleep -Seconds 1
    Write-Log "daemon started (supervisor pid $($proc.Id))"
    Write-Log "worker log: $LogFile"
    Write-Log "supervisor log: $SupervisorLogFile"
}

function Show-Status {
    Ensure-RunDir
    $workerPid = Read-PidFile $PidFile
    $supervisorPid = Read-PidFile $SupervisorPidFile
    $webPort = Get-WebPort

    if (Test-ProcessRunning $supervisorPid) {
        Write-Host "supervisor: running (pid $supervisorPid)"
    } else {
        Write-Host 'supervisor: stopped'
    }

    if (Test-ProcessRunning $workerPid) {
        Write-Host "worker: running (pid $workerPid)"
    } else {
        Write-Host 'worker: stopped'
    }

    $orphans = Get-AccessAssistantWorkerPids
    if ($orphans.Count -gt 0 -and -not (Test-ProcessRunning $workerPid)) {
        Write-Host "orphan worker(s): running (pids $($orphans -join ', '))"
    }

    $ports = @($webPort)
    if ($ports -notcontains 8000) { $ports += 8000 }
    $listeners = @()
    foreach ($port in $ports) {
        $listeners += Get-PidsListeningOnPort $port
    }
    $listeners = $listeners | Select-Object -Unique
    if ($listeners.Count -gt 0) {
        Write-Host "port listener(s): $($listeners -join ', ') (checked $($ports -join ', '))"
    } else {
        Write-Host "port(s) $($ports -join ', '): free"
    }

    Write-Host "log: $LogFile"
    Write-Host "supervisor log: $SupervisorLogFile"
}

function Show-Logs {
    Ensure-RunDir
    if (-not (Test-Path $LogFile)) {
        New-Item -ItemType File -Path $LogFile | Out-Null
    }
    Get-Content -Path $LogFile -Wait -Tail 50
}

Push-Location $RootDir
try {
    switch ($Command) {
        'start' { Start-Daemon }
        'stop' { Stop-All }
        'restart' { Stop-All; Start-Daemon }
        'install' { Install-Deps; Write-Log 'dependencies installed' }
        'status' { Show-Status }
        'logs' { Show-Logs }
        'run' { Start-WorkerForeground }
        'once' { Start-WorkerBackground }
        '_supervise' { Start-SupervisorLoop }
    }
} finally {
    Pop-Location
}
