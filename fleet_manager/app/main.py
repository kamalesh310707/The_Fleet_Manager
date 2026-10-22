"""FastAPI entrypoint — warehouse fleet with pick-and-place scheduling."""
from __future__ import annotations

import asyncio
import contextlib
import os
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from fleet_manager.app.world_state import WorldStateStore
from fleet_manager.app import dispatch, reservation
from fleet_manager.app.mqtt_bridge import MqttBridge
from robots.astar import find_path, path_cost, build_reservation_cost_map
from robots.grid_config import (
    scenario_grid,
    DEFAULT_OBSTACLES,
    DEFAULT_ITEM_SPAWNS,
    DEFAULT_PLACE_BAYS,
    GRID_SIZE,
)
from shared.schemas import (
    CommandPayload,
    Coordinate,
    DispatchBroadcast,
    ErrorPayload,
    ItemState,
    ItemStatus,
    RobotState,
    RobotStatus,
    StatusPayload,
    Task,
    TaskPhase,
    TaskPriority,
    TaskStatus,
    TaskType,
)

# Robot home cells for a 30×30 warehouse
ROBOT_HOMES: list[tuple[str, Coordinate]] = [
    ("robot_1", (1, 1)),
    ("robot_2", (1, 15)),
    ("robot_3", (1, 28)),
    ("robot_4", (28, 1)),
    ("robot_5", (28, 28)),
]

ITEM_COLORS = [
    (0.90, 0.45, 0.15),
    (0.20, 0.65, 0.85),
    (0.75, 0.25, 0.70),
    (0.95, 0.80, 0.20),
    (0.30, 0.80, 0.45),
    (0.85, 0.35, 0.40),
]


class DispatchRequest(BaseModel):
    target: Coordinate
    priority: TaskPriority = TaskPriority.NORMAL


class PickPlaceRequest(BaseModel):
    pick: Coordinate
    place: Coordinate
    item_id: str | None = None
    priority: TaskPriority = TaskPriority.NORMAL


class ScenarioRequest(BaseModel):
    name: str = Field(description="crossing | burst | priority_race | stress | pick_wave")


GRID = scenario_grid()
ROBOT_TIMEOUT_S = float(os.getenv("ROBOT_TIMEOUT_S", "20"))
WATCHDOG_INTERVAL_S = float(os.getenv("WATCHDOG_INTERVAL_S", "1"))
REQUEUE_INTERVAL_S = float(os.getenv("REQUEUE_INTERVAL_S", "2"))
MAX_TEMPORAL_DELAY = int(os.getenv("MAX_TEMPORAL_DELAY", "16"))


def _cost_fn(start: Coordinate, goal: Coordinate) -> float:
    cost_map = build_reservation_cost_map(world_state.snapshot().get("reservations", {}))
    return path_cost(GRID, start, goal, cost_map=cost_map)


@asynccontextmanager
async def lifespan(app: FastAPI):
    for robot_id, position in ROBOT_HOMES:
        await world_state.upsert_robot(
            RobotState(robot_id=robot_id, position=position, last_seen=time.time())
        )
    # Seed inventory on shelves
    for i, cell in enumerate(DEFAULT_ITEM_SPAWNS):
        if GRID[cell[0]][cell[1]] == 1:
            continue
        color = ITEM_COLORS[i % len(ITEM_COLORS)]
        await world_state.upsert_item(
            ItemState(
                item_id=f"item_{i+1:02d}",
                position=cell,
                status=ItemStatus.ON_SHELF,
                sku=f"SKU-{1000 + i}",
                color=color,
            )
        )
    mqtt_bridge.start(asyncio.get_running_loop())
    watchdog = asyncio.create_task(robot_watchdog())
    requeue = asyncio.create_task(queued_task_worker())
    place_worker = asyncio.create_task(place_leg_worker())
    try:
        yield
    finally:
        for t in (watchdog, requeue, place_worker):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        mqtt_bridge.stop()


app = FastAPI(title="The Fleet Manager", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

world_state = WorldStateStore(obstacles=DEFAULT_OBSTACLES)
_dashboard_clients: set[WebSocket] = set()


async def handle_robot_status(robot_id, payload) -> None:
    robot = await world_state.get_robot(robot_id)
    if robot is not None and robot.path and payload.path_index > 0:
        await reservation.release_behind(
            world_state, robot_id, robot.path, payload.path_index,
            start_delay=getattr(robot, "start_delay", 0) or 0,
        )
    await world_state.apply_status(robot_id, payload)
    await broadcast_world_state()


async def handle_robot_error(robot_id, payload) -> None:
    await world_state.apply_error(robot_id, payload)
    await broadcast_world_state()


mqtt_bridge = MqttBridge(handle_robot_status, handle_robot_error)


async def robot_watchdog() -> None:
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_S)
        if await world_state.mark_stale_robots(ROBOT_TIMEOUT_S):
            await broadcast_world_state()


async def _issue_drive(
    task: Task,
    robot: RobotState,
    target: Coordinate,
    phase: TaskPhase,
    action: str = "drive",
) -> bool:
    # Drop any stale reservations for this robot so a new leg can always book
    await world_state.clear_reservations_for(robot.robot_id)
    snap = world_state.snapshot()
    cost_map = build_reservation_cost_map(snap.get("reservations", {}))
    path = find_path(GRID, robot.position, target, cost_map=cost_map)
    if not path:
        # Retry without soft costs (hard obstacles only)
        path = find_path(GRID, robot.position, target)
    if not path:
        return False
    result = await reservation.try_reserve_with_temporal_shift(
        world_state, robot.robot_id, path, max_delay=MAX_TEMPORAL_DELAY
    )
    if not result.ok:
        # Last resort: accept path with delay 0 even if soft-conflicted map was noisy
        keys = reservation.keys_for_path(path, 0)
        ok = await world_state.try_reserve_path(robot.robot_id, keys)
        if not ok:
            return False
        result = reservation.ReservationResult(ok=True, start_delay=0, keys=keys)

    task.status = TaskStatus.ASSIGNED if task.status == TaskStatus.QUEUED else task.status
    task.assigned_to = robot.robot_id
    task.start_delay = result.start_delay
    task.phase = phase
    robot.status = RobotStatus.MOVING
    robot.target = target
    robot.path = path
    robot.path_index = 0
    robot.start_delay = result.start_delay
    robot.phase = phase
    robot.last_seen = time.time()
    await world_state.upsert_robot(robot)
    await world_state.record_dispatch(len(path), delayed=result.start_delay > 0)

    mqtt_bridge.publish_command(
        robot.robot_id,
        CommandPayload(
            target=target,
            task_id=task.task_id,
            path=path,
            start_delay=result.start_delay,
            phase=phase,
            item_id=task.item_id,
            action=action,
        ),
    )
    mqtt_bridge.publish_dispatch(
        DispatchBroadcast(
            task_id=task.task_id,
            target=target,
            assigned_to=robot.robot_id,
            timestamp=time.time(),
            start_delay=result.start_delay,
            priority=task.priority,
            task_type=task.task_type,
            phase=phase,
            item_id=task.item_id,
        )
    )
    return True


async def _get_available_idle_robots() -> list[RobotState]:
    idle = await world_state.idle_robots()
    if not idle:
        # Auto-recover any error robot without active task so users are never permanently blocked
        snap = world_state.snapshot()
        for r_id, r_st in snap.get("robots", {}).items():
            if r_st.get("status") == RobotStatus.ERROR.value:
                await world_state.reset_robot(r_id)
        idle = await world_state.idle_robots()
    return idle


async def try_assign_task(task: Task) -> bool:
    robot = dispatch.select_robot_for_task(
        await _get_available_idle_robots(),
        task,
        _cost_fn,
    )
    if robot is None:
        return False

    if task.task_type == TaskType.PICK_PLACE:
        pick = task.pick or task.target
        return await _issue_drive(task, robot, pick, TaskPhase.TO_PICK, action="pick")
    return await _issue_drive(task, robot, task.target, TaskPhase.MOVE, action="drive")


async def queued_task_worker() -> None:
    while True:
        await asyncio.sleep(REQUEUE_INTERVAL_S)
        task = await world_state.pop_queued_task()
        if task is None:
            continue
        if await try_assign_task(task):
            await world_state.add_task(task)
            await broadcast_world_state()
        else:
            task.status = TaskStatus.QUEUED
            task.assigned_to = None
            await world_state.add_task(task)


async def place_leg_worker() -> None:
    """After a successful pick, send the place leg automatically."""
    while True:
        await asyncio.sleep(0.8)
        pairs = await world_state.needs_place_leg()
        for robot, task in pairs:
            place = task.place or task.target
            ok = await _issue_drive(task, robot, place, TaskPhase.TO_PLACE, action="place")
            if ok:
                await broadcast_world_state()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "grid_size": GRID_SIZE,
        "robots": len(ROBOT_HOMES),
        "obstacles": len(DEFAULT_OBSTACLES),
        "features": [
            "pick_place",
            "inventory",
            "octile_astar",
            "reservation_cost_map",
            "temporal_shift",
            "multi_leg_scheduler",
            "5_robots",
        ],
    }


@app.get("/world-state")
async def get_world_state():
    return world_state.snapshot()


@app.get("/metrics")
async def get_metrics():
    return world_state.snapshot().get("metrics", {})


@app.get("/items")
async def list_items():
    return {"items": [i.model_dump() for i in await world_state.list_items()]}


@app.post("/dispatch")
async def dispatch_task(req: DispatchRequest):
    target = tuple(req.target)
    if not (0 <= target[0] < GRID_SIZE and 0 <= target[1] < GRID_SIZE):
        raise HTTPException(status_code=422, detail=f"Target out of bounds for {GRID_SIZE}x{GRID_SIZE}")
    if GRID[target[0]][target[1]] == 1:
        raise HTTPException(status_code=422, detail="Target cell is an obstacle")

    task = Task(
        task_id=str(uuid4()),
        task_type=TaskType.MOVE,
        target=target,
        created_at=time.time(),
        priority=req.priority,
        phase=TaskPhase.MOVE,
    )
    robot = dispatch.select_robot_for_task(
        await _get_available_idle_robots(), task, _cost_fn
    )
    if robot is None:
        await world_state.add_task(task)
        await broadcast_world_state()
        return {"task": task.model_dump(), "message": "No idle robot; task queued."}

    ok = await _issue_drive(task, robot, target, TaskPhase.MOVE, action="drive")
    if not ok:
        await world_state.add_task(task)
        await world_state.record_queued_conflict()
        await broadcast_world_state()
        return {"task": task.model_dump(), "message": "Path/reservation failed; queued."}

    await world_state.add_task(task)
    await broadcast_world_state()
    return {
        "task": task.model_dump(),
        "path": robot.path,
        "start_delay": task.start_delay,
        "message": f"MOVE assigned to {robot.robot_id}",
    }


@app.post("/dispatch/pick-place")
async def dispatch_pick_place(req: PickPlaceRequest):
    pick = tuple(req.pick)
    place = tuple(req.place)
    for label, cell in (("pick", pick), ("place", place)):
        if not (0 <= cell[0] < GRID_SIZE and 0 <= cell[1] < GRID_SIZE):
            raise HTTPException(status_code=422, detail=f"{label} out of bounds")
        if GRID[cell[0]][cell[1]] == 1:
            raise HTTPException(status_code=422, detail=f"{label} is an obstacle")

    item_id = req.item_id
    if item_id:
        item = await world_state.get_item(item_id)
        if item is not None and item.status == ItemStatus.ON_SHELF:
            pick = tuple(item.position)
        else:
            item_id = f"item_dyn_{uuid4().hex[:6]}"
            await world_state.upsert_item(
                ItemState(item_id=item_id, position=pick, sku="SKU-DYN", color=ITEM_COLORS[0])
            )
    else:
        # Find any on-shelf item at pick cell, or create a virtual SKU
        items = await world_state.list_items()
        match = next(
            (i for i in items if i.status == ItemStatus.ON_SHELF and tuple(i.position) == pick),
            None,
        )
        if match:
            item_id = match.item_id
        else:
            item_id = f"item_dyn_{uuid4().hex[:6]}"
            await world_state.upsert_item(
                ItemState(item_id=item_id, position=pick, sku="SKU-DYN", color=ITEM_COLORS[0])
            )

    task = Task(
        task_id=str(uuid4()),
        task_type=TaskType.PICK_PLACE,
        target=place,
        pick=pick,
        place=place,
        item_id=item_id,
        created_at=time.time(),
        priority=req.priority,
        phase=TaskPhase.TO_PICK,
    )

    robot = dispatch.select_robot_for_task(
        await _get_available_idle_robots(), task, _cost_fn
    )
    if robot is None:
        await world_state.add_task(task)
        await broadcast_world_state()
        return {"task": task.model_dump(), "message": "No idle robot; pick-place queued."}

    ok = await _issue_drive(task, robot, pick, TaskPhase.TO_PICK, action="pick")
    if not ok:
        await world_state.add_task(task)
        await world_state.record_queued_conflict()
        await broadcast_world_state()
        return {"task": task.model_dump(), "message": "Could not reserve path to pick; queued."}

    await world_state.add_task(task)
    await broadcast_world_state()
    return {
        "task": task.model_dump(),
        "message": f"PICK_PLACE assigned to {robot.robot_id} (pick {pick} -> place {place})",
    }


@app.post("/robots/{robot_id}/reset")
async def reset_robot(robot_id: str):
    robot = await world_state.reset_robot(robot_id)
    if robot is None:
        raise HTTPException(status_code=404, detail="Unknown robot")
    await world_state.clear_reservations_for(robot_id)
    await broadcast_world_state()
    return {"robot": robot.model_dump(), "message": f"{robot_id} reset to idle"}


@app.post("/robots/{robot_id}/status")
async def update_robot_status(robot_id: str, payload: StatusPayload):
    await handle_robot_status(robot_id, payload)
    return {"status": "ok", "robot_id": robot_id}


@app.post("/robots/{robot_id}/error")
async def update_robot_error(robot_id: str, payload: ErrorPayload):
    await handle_robot_error(robot_id, payload)
    return {"status": "ok", "robot_id": robot_id}


SCENARIOS: dict[str, list] = {
    "crossing": [
        ("move", (5, 28), TaskPriority.NORMAL),
        ("move", (28, 5), TaskPriority.NORMAL),
        ("move", (15, 15), TaskPriority.HIGH),
    ],
    "burst": [
        ("move", (2, 10), TaskPriority.NORMAL),
        ("move", (10, 2), TaskPriority.NORMAL),
        ("move", (27, 10), TaskPriority.NORMAL),
        ("move", (10, 27), TaskPriority.NORMAL),
        ("move", (15, 20), TaskPriority.NORMAL),
    ],
    "priority_race": [
        ("move", (8, 28), TaskPriority.LOW),
        ("move", (22, 28), TaskPriority.LOW),
        ("move", (15, 2), TaskPriority.CRITICAL),
    ],
    "stress": [
        ("move", (2, 8), TaskPriority.NORMAL),
        ("move", (8, 2), TaskPriority.NORMAL),
        ("move", (27, 8), TaskPriority.HIGH),
        ("move", (8, 27), TaskPriority.NORMAL),
        ("move", (15, 15), TaskPriority.CRITICAL),
        ("move", (20, 5), TaskPriority.NORMAL),
    ],
    "pick_wave": [
        ("pick_place", (3, 5), (1, 28), TaskPriority.HIGH),
        ("pick_place", (3, 12), (28, 1), TaskPriority.NORMAL),
        ("pick_place", (27, 8), (1, 1), TaskPriority.NORMAL),
        ("pick_place", (15, 3), (28, 28), TaskPriority.CRITICAL),
        ("pick_place", (11, 15), (14, 28), TaskPriority.NORMAL),
    ],
}


@app.get("/scenarios")
async def list_scenarios():
    blurbs = {
        "crossing": "Crossing paths on the large floor",
        "burst": "Multi-robot move burst",
        "priority_race": "Critical move jumps the queue",
        "stress": "Sustained mixed load",
        "pick_wave": "5 concurrent pick-and-place jobs",
    }
    return {
        "scenarios": [
            {"name": n, "tasks": len(t), "description": blurbs.get(n, "")}
            for n, t in SCENARIOS.items()
        ]
    }


@app.post("/scenarios/run")
async def run_scenario(req: ScenarioRequest):
    if req.name not in SCENARIOS:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {list(SCENARIOS)}")
    results = []
    for entry in SCENARIOS[req.name]:
        try:
            if entry[0] == "move":
                _, target, priority = entry
                out = await dispatch_task(DispatchRequest(target=target, priority=priority))
            else:
                _, pick, place, priority = entry
                out = await dispatch_pick_place(
                    PickPlaceRequest(pick=pick, place=place, priority=priority)
                )
            results.append(out)
        except HTTPException as exc:
            results.append({"error": exc.detail})
        await asyncio.sleep(0.05)
    return {"scenario": req.name, "results": results}


@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    await websocket.accept()
    _dashboard_clients.add(websocket)
    try:
        await websocket.send_json(world_state.snapshot())
    except Exception:
        pass
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _dashboard_clients.discard(websocket)


async def broadcast_world_state():
    payload = world_state.snapshot()
    for client in list(_dashboard_clients):
        try:
            await client.send_json(payload)
        except Exception:
            _dashboard_clients.discard(client)
