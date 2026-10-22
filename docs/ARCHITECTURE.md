# Architecture

## Layers

Four decoupled layers, each independently replaceable and independently
testable — see root README for the summary table.

### Why the simulation isn't built directly in React

Browsers target roughly 60Hz for rendering. Real rigid-body physics needs
500-1000Hz to stay numerically stable, which would choke a single-threaded
JS render loop. Keeping MuJoCo as a separate Python process mirrors how a
real fleet's robots run independent onboard software while a cloud dashboard
only tracks coordinates.

## Data flow, end to end

1. Operator clicks a target cell on the dashboard grid → React fires an HTTP
   POST to FastAPI with the target coordinate.
2. Fleet Manager checks live World State, scores eligible idle robots (A*
   path cost + decaying load), and selects one (`dispatch.py`).
3. Fleet Manager plans the path, atomically reserves every `(cell, t)` pair,
   then publishes a command (including the reserved path) to
   `fleet/robot/{id}/command` over MQTT.
4. The target robot follows the reserved path in MuJoCo (or re-plans only
   if the path field is absent).
5. The robot publishes live status updates to `fleet/robot/{id}/status`.
6. Fleet Manager updates World State and pushes the change to the dashboard
   over a WebSocket.
7. Queued tasks are periodically retried by a background worker when robots
   become free again.

## World State shape

```json
{
  "robots": {
    "robot_1": {
      "position": [x, y],
      "status": "idle|moving|error",
      "target": [x, y] | null,
      "path": [[x, y], "..."],
      "last_seen": 0.0,
      "tasks_completed": 0
    }
  },
  "reservations": { "x,y,t": "robot_id" },
  "task_queue": [{ "task_id": "...", "target": [x, y], "status": "...", "assigned_to": "..." }],
  "obstacles": [[5, 5], "..."]
}
```

## Worked example: two crossing paths

Robot 1 drives from `(0,0)` to `(2,0)`, planning the path
`[(0,0), (1,0), (2,0)]` — it reserves `(0,0,0)`, `(1,0,1)`, `(2,0,2)`.

Robot 2 then wants to drive from `(2,0)` to `(0,0)` — a straight crossing
path `[(2,0), (1,0), (0,0)]`, which needs `(1,0,1)`. That cell/timestep is
already held by Robot 1, so the Fleet Manager rejects Robot 2's reservation.
The task is queued and retried later.

This is exactly what `tests/test_reservation.py::test_crossing_paths_second_reservation_rejected`
exercises.

## Failure handling state diagram (informal)

```
IDLE --(command received)--> MOVING
MOVING --(no ack/progress within timeout)--> ERROR
MOVING --(goal reached)--> IDLE
ERROR --(manual/automatic reset, reconnected)--> IDLE   [reservations cleared]
```

Backend side: if no status update arrives from a robot within N seconds, the
Fleet Manager marks it ERROR in World State, frees its reservations, and
requeues any in-progress task. A background worker later reassigns queued
tasks to available robots.

## MuJoCo deployment environment

- Arena is generated programmatically (`robots/mujoco_arena.py`) as a
  **warehouse deployment scene**: checker concrete floor, painted zone pads
  (Receiving / Storage / Packing / Dispatch), yellow aisle lanes, industrial
  perimeter walls with corner posts, and **rack-style storage obstacles**
  (shelves + uprights) whose collision volume still matches the A* grid.
- AGVs are low chassis carriers with bumper, load deck, cargo block, and
  status beacon — joint/actuator names unchanged for the drive controller.
- Multi-light overhead lighting with shadows; materials for floor, racks, metal.
- Fleet viewer hosts all robots in one world: `python -m robots.run_fleet_viewer`.
- One-command launcher: `./scripts/launch_demo.sh`.

## Next-level upgrades

### Temporal shift
When a path conflicts with existing reservations, the Fleet Manager retries the
same geometry with increasing `start_delay` (robot waits at the origin for
N discrete timesteps). Many crossing-path conflicts resolve without queueing.

### Progressive release
As robots report `path_index` in status messages, reservation keys for cells
already vacated are dropped.

### Priority dispatch
Tasks carry `priority: low|normal|high|critical`. The requeue worker always
pops the highest priority first.

### Demo scenarios
`POST /scenarios/run` with `{ "name": "crossing" | "burst" | "priority_race" | "stress" }`.
