param(
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"

$AppName = "YT Automator"
$HostName = "127.0.0.1"
$Port = 8000
$Url = "http://${HostName}:${Port}"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Root "logs"
$LogFile = Join-Path $LogDir "launcher.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-LauncherLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$stamp  $Message" | Add-Content -Path $LogFile -Encoding UTF8
}

function Test-AppRunning {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Open-App {
    if (-not $NoOpen) {
        Start-Process $Url
    }
}

Set-Location $Root
Write-LauncherLog "Launcher invoked from $Root"

if (Test-AppRunning) {
    if ($NoOpen) {
        Write-Host "$AppName is already running at $Url"
    } else {
        Write-Host "$AppName is already running. Opening $Url"
    }
    Write-LauncherLog "App already running at $Url"
    Open-App
    exit 0
}

$portOwner = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($portOwner) {
    $processName = "unknown"
    try {
        $processName = (Get-Process -Id $portOwner.OwningProcess -ErrorAction Stop).ProcessName
    } catch {}

    $message = "Port $Port is already in use by PID $($portOwner.OwningProcess) ($processName), but $AppName did not respond at $Url."
    Write-Host $message
    Write-LauncherLog $message
    if (-not $NoOpen) {
        Read-Host "Press Enter to close"
    }
    exit 1
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $message = "Python was not found on PATH. Install Python or add it to PATH, then run this launcher again."
    Write-Host $message
    Write-LauncherLog $message
    if (-not $NoOpen) {
        Read-Host "Press Enter to close"
    }
    exit 1
}

Write-Host "Starting $AppName..."
Write-LauncherLog "Starting server at $Url"

$serverOutLog = Join-Path $LogDir "server.out.log"
$serverErrLog = Join-Path $LogDir "server.err.log"
$args = @("-m", "uvicorn", "app:app", "--host", $HostName, "--port", "$Port")

Start-Process `
    -WindowStyle Hidden `
    -FilePath $python.Source `
    -ArgumentList $args `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $serverOutLog `
    -RedirectStandardError $serverErrLog

$deadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Milliseconds 500
    if (Test-AppRunning) {
        if ($NoOpen) {
            Write-Host "$AppName started at $Url"
        } else {
            Write-Host "$AppName started. Opening $Url"
        }
        Write-LauncherLog "App started at $Url"
        Open-App
        exit 0
    }
} while ((Get-Date) -lt $deadline)

$message = "$AppName did not respond within 30 seconds. Check $serverOutLog and $serverErrLog"
Write-Host $message
Write-LauncherLog $message
if (-not $NoOpen) {
    Read-Host "Press Enter to close"
}
exit 1
