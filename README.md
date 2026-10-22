# The Fleet Manager

A provider-agnostic, four-layer multi-agent swarm orchestration system:
a React dashboard, a FastAPI fleet manager, an MQTT communication layer,
and simulated robotic agents running in MuJoCo.

Individual hardware-based course project. Target expo date: **October 22**.

## Why this exists

Traditional robotics demos are single-robot, single-script systems — one
point of failure, no coordination, no real dispatch logic. This project
replaces that with a centralized, cloud-style dispatch architecture: multiple
simulated robots operate as independent agents, coordinated by a backend that
tracks live world state, assigns tasks based on cost and load, and prevents
collisions between robots whose paths cross.

The two strongest technical claims — worth leading with in a viva — are
the **load- and path-cost-aware dispatch logic** (with exponential load decay)
and the **space-time reservation table** used for collision avoidance.

## Architecture

| Layer | Technology | Responsibility |
|---|---|---|
| 1. Dashboard | React + WebSockets | Operator UI: live robot grid, obstacles, path overlay, click-to-dispatch, task history |
| 2. Fleet Manager | FastAPI (Python) | In-memory World State, dispatch/load-balancing, reservation table, WebSocket push, auto-requeue |
| 3. Comms Layer | MQTT / Mosquitto | Async pub/sub bus between backend and every robot process |
| 4. Robotic Agents | Python + MuJoCo | Independent state machines per robot, A* pathfinding, physics, optional live viewer |

Full detail: see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Run (preferred: one MuJoCo window for all robots)

**Do not run three `run_mqtt_agent` processes** if you want a single 3D view.
Use the fleet viewer instead — it hosts robot_1, robot_2, and robot_3 in one MuJoCo world.

### Prerequisites

- Python 3.10+
- Node.js 18+
- Docker (for Mosquitto) **or** a local Mosquitto install

### Setup

```bash
cd project   # or wherever you extracted this repo

python -m venv venv
source venv/bin/activate   # Windows: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt

npm install
```

### Launch

**Terminal 1 — MQTT broker**

```bash
docker compose up -d mosquitto
# or: mosquitto -c comms/mosquitto.conf
```

**Terminal 2 — backend**

```bash
export PYTHONPATH=.
uvicorn fleet_manager.app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 3 — ALL three robots in ONE MuJoCo window**

```bash
export PYTHONPATH=.
python -m robots.run_fleet_viewer
```

**Terminal 4 — dashboard**

```bash
npm run dev
```

Open **http://localhost:5173** and click free cells. You should see all three
colored AGVs in a single MuJoCo window.

One-shot helper (backend + viewer + Mosquitto):

```bash
./scripts/launch_demo.sh
```

## Features

- **Obstacles**: Warehouse-style layout on the 15×15 grid; A* routes around them.
- **Path consistency**: Fleet Manager reserves a path and sends it to the robot; the agent follows the reserved path instead of re-planning.
- **Load-aware dispatch**: Score = path cost + decaying task load (half-life ~2 min), with priority-biased path weight.
- **Space-time reservations**: Atomic `(cell, timestep)` claims; crossing paths are rejected or delayed.
- **Temporal shift**: On conflict, the same geometric path is retried with start delays `0..N` before queueing.
- **Progressive reservation release**: Cells behind a moving robot are freed so capacity recovers early.
- **Task priority**: `low | normal | high | critical` with priority-ordered requeue.
- **Fleet metrics**: Dispatched / completed / failed, delay-resolved conflicts, active reservations, avg path length.
- **Demo scenarios**: One-click `crossing`, `burst`, `priority_race`, `stress` sequences for the expo.
- **Auto-requeue**: Queued tasks are periodically retried when robots become free.
- **Watchdog**: Stale robots are marked ERROR, reservations freed, tasks re-queued.
- **MuJoCo warehouse arena**: Concrete floor, zone pads, aisle lanes, rack obstacles, industrial lighting; AGVs with beacon + load deck (fleet viewer hosts all 3).
- **Dashboard**: Priority selector, scenario buttons, reservation heat overlay, live ops metrics (no “percent completed” gauges).

## MQTT topic schema

See [`comms/topics.md`](comms/topics.md) — mirrors `shared/schemas.py`.
`CommandPayload` includes an optional `path` field so the reserved route is authoritative.

## Dispatch algorithm

Each eligible idle robot is scored on **A* path cost** (obstacles matter) plus
**recent task load** with exponential decay, so one robot does not starve
while another is repeatedly picked. See `fleet_manager/app/dispatch.py`.

## Collision avoidance

Each robot’s path is reserved in a shared table held by the Fleet Manager
before the command is issued. A conflicting reservation is rejected (or
temporally shifted) and the task is queued for later if needed.
See `fleet_manager/app/reservation.py`.

## Failure handling

Timeout → Error state → reservations cleared → task re-queued.
Operator can reset a robot from the dashboard. See `robots/agent.py` FSM
and the backend watchdog.

## Tests

```bash
export PYTHONPATH=.
pytest tests/ -q
```

## Status

- ✅ Week 1–2: MuJoCo arena, A*, independent drive-to
- ✅ Week 3–4: World State, dispatch, reservation table, unit tests
- ✅ Week 5+: MQTT wiring, path consistency, obstacles, auto-requeue
- ✅ Dashboard: live grid, obstacles, path overlay, polished UI wired to API
- ✅ Demo launcher + improved MuJoCo environment
