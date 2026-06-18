$ErrorActionPreference = "Stop"

$AppName = "YT Automator"
$Port = 8000
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Root "logs"
$LogFile = Join-Path $LogDir "launcher.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-LauncherLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$stamp  $Message" | Add-Content -Path $LogFile -Encoding UTF8
}

$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listeners) {
    $message = "$AppName is not currently listening on port $Port."
    Write-Host $message
    Write-LauncherLog $message
    exit 0
}

$stopped = @()
foreach ($listener in $listeners | Sort-Object OwningProcess -Unique) {
    $owningPid = $listener.OwningProcess
    try {
        $proc = Get-Process -Id $owningPid -ErrorAction Stop
        Stop-Process -Id $owningPid -Force -ErrorAction Stop
        $stopped += "$($proc.ProcessName) (PID $owningPid)"
    } catch {
        $stopped += "PID $owningPid"
    }
}

$message = "Stopped ${AppName} on port ${Port}: " + ($stopped -join ", ")
Write-Host $message
Write-LauncherLog $message
