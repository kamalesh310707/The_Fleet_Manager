#!/usr/bin/env bash
# One-command demo launcher for The Fleet Manager.
# Starts Mosquitto, FastAPI backend, and the SINGLE multi-robot MuJoCo viewer.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export MQTT_HOST="${MQTT_HOST:-localhost}"
export MQTT_PORT="${MQTT_PORT:-1883}"
export ROBOT_REALTIME_FACTOR="${ROBOT_REALTIME_FACTOR:-1.0}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

echo "==> Starting Mosquitto (Docker)…"
docker compose up -d mosquitto 2>/dev/null || {
  echo "    Docker not available — trying native mosquitto…"
  if command -v mosquitto >/dev/null 2>&1; then
    mosquitto -c comms/mosquitto.conf -d || true
  else
    echo "    WARNING: no MQTT broker found. Install mosquitto or start Docker."
  fi
}

sleep 1

echo "==> Starting Fleet Manager backend (uvicorn)…"
PYTHON="${ROOT}/venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python3
fi
$PYTHON -m uvicorn fleet_manager.app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

sleep 2

echo "==> Starting SINGLE multi-robot MuJoCo viewer (all 3 robots in one window)…"
$PYTHON -m robots.run_fleet_viewer &
VIEWER_PID=$!

echo ""
echo "Backend:  http://localhost:8000"
echo "Dashboard: cd dashboard && npm run dev   →  http://localhost:5173"
echo "MuJoCo:   one window with robot_1, robot_2, robot_3"
echo ""
echo "Press Ctrl+C to stop everything."

trap 'echo "Shutting down…"; kill $BACKEND_PID $VIEWER_PID 2>/dev/null; exit 0' INT TERM
wait
