# One-command demo launcher (Windows PowerShell)
# From project root, with venv already created:
#   .\scripts\launch_demo.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$env:MQTT_HOST = if ($env:MQTT_HOST) { $env:MQTT_HOST } else { "localhost" }
$env:MQTT_PORT = if ($env:MQTT_PORT) { $env:MQTT_PORT } else { "1883" }
$env:ROBOT_REALTIME_FACTOR = if ($env:ROBOT_REALTIME_FACTOR) { $env:ROBOT_REALTIME_FACTOR } else { "1.0" }
$env:PYTHONPATH = $Root

$Python = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

Write-Host "==> Starting Mosquitto (Docker)..."
try {
  docker compose up -d mosquitto
} catch {
  Write-Host "    Docker failed — start Mosquitto yourself if needed."
}

Start-Sleep -Seconds 1

Write-Host "==> Starting backend (FastAPI on :8000)..."
Start-Process -FilePath $Python -ArgumentList "-m","uvicorn","fleet_manager.app.main:app","--host","0.0.0.0","--port","8000","--reload" -WorkingDirectory $Root

Start-Sleep -Seconds 2

Write-Host "==> Starting SINGLE multi-robot MuJoCo viewer..."
Start-Process -FilePath $Python -ArgumentList "-m","robots.run_fleet_viewer" -WorkingDirectory $Root

Write-Host "==> Starting Vite dashboard (port 5174)..."
# Resolve npm from PATH or common install locations
$Npm = "npm"
Start-Process -FilePath "cmd.exe" -ArgumentList "/c","npm run dev" -WorkingDirectory $Root

Start-Sleep -Seconds 3

Write-Host ""
Write-Host "============================================"
Write-Host "  Backend:    http://localhost:8000"
Write-Host "  Dashboard:  http://localhost:5174   <-- open this"
Write-Host "  MuJoCo:     one window with all robots"
Write-Host "============================================"
Write-Host ""
Write-Host "Press Ctrl+C in each window to stop the services."
