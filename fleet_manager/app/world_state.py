"""In-memory World State store — robots, items, reservations, tasks."""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from shared.schemas import (
    Coordinate,
    ErrorPayload,
    ItemState,
    ItemStatus,
    PRIORITY_WEIGHT,
    RobotState,
    RobotStatus,
    StatusPayload,
    Task,
    TaskPhase,
    TaskStatus,
    TaskType,
    WorldState,
)


class WorldStateStore:
    def __init__(self, obstacles: list[Coordinate] | None = None) -> None:
        self._state = WorldState(obstacles=list(obstacles or []))
        self._lock = asyncio.Lock()
        self._path_length_sum = 0.0
        self._path_length_n = 0

    def snapshot(self) -> dict:
        robots = self._state.robots
        items = self._state.items
        m = self._state.metrics
        m.reservations_active = len(self._state.reservations)
        m.robots_idle = sum(1 for r in robots.values() if r.status == RobotStatus.IDLE)
        m.robots_moving = sum(
            1 for r in robots.values()
            if r.status in (RobotStatus.MOVING, RobotStatus.PICKING, RobotStatus.PLACING)
        )
        m.robots_error = sum(1 for r in robots.values() if r.status == RobotStatus.ERROR)
        m.items_on_shelf = sum(1 for i in items.values() if i.status == ItemStatus.ON_SHELF)
        m.items_carried = sum(1 for i in items.values() if i.status == ItemStatus.CARRIED)
        m.items_delivered = sum(1 for i in items.values() if i.status == ItemStatus.DELIVERED)
        m.avg_path_length = (
            self._path_length_sum / self._path_length_n if self._path_length_n else 0.0
        )
        return self._state.model_dump()

    async def upsert_robot(self, robot: RobotState) -> None:
        async with self._lock:
            existing = self._state.robots.get(robot.robot_id)
            if existing and robot.tasks_completed == 0:
                robot.tasks_completed = existing.tasks_completed
            if existing and robot.carrying_item_id is None and existing.carrying_item_id:
                # preserve carry flag unless explicitly cleared by status apply
                pass
            self._state.robots[robot.robot_id] = robot

    async def get_robot(self, robot_id: str) -> Optional[RobotState]:
        async with self._lock:
            return self._state.robots.get(robot_id)

    async def idle_robots(self) -> list[RobotState]:
        async with self._lock:
            return [r for r in self._state.robots.values() if r.status == RobotStatus.IDLE]

    async def add_task(self, task: Task) -> None:
        async with self._lock:
            self._state.task_queue.append(task)

    async def upsert_item(self, item: ItemState) -> None:
        async with self._lock:
            self._state.items[item.item_id] = item

    async def get_item(self, item_id: str) -> Optional[ItemState]:
        async with self._lock:
            return self._state.items.get(item_id)

    async def list_items(self) -> list[ItemState]:
        async with self._lock:
            return list(self._state.items.values())

    async def record_dispatch(self, path_len: int, delayed: bool) -> None:
        async with self._lock:
            self._state.metrics.tasks_dispatched += 1
            if delayed:
                self._state.metrics.conflicts_resolved_by_delay += 1
            self._path_length_sum += path_len
            self._path_length_n += 1

    async def record_queued_conflict(self) -> None:
        async with self._lock:
            self._state.metrics.conflicts_queued += 1


    async def reserve(self, key: str, robot_id: str) -> bool:
        async with self._lock:
            existing = self._state.reservations.get(key)
            if existing is not None and existing != robot_id:
                return False
            self._state.reservations[key] = robot_id
            return True

    async def try_reserve_path(self, robot_id: str, keys: list[str]) -> bool:
        async with self._lock:
            if any(
                (owner := self._state.reservations.get(key)) is not None and owner != robot_id
                for key in keys
            ):
                return False
            for key in keys:
                self._state.reservations[key] = robot_id
            return True

    async def release_keys(self, robot_id: str, keys: list[str]) -> None:
        async with self._lock:
            for key in keys:
                if self._state.reservations.get(key) == robot_id:
                    del self._state.reservations[key]

    async def clear_reservations_for(self, robot_id: str) -> None:
        async with self._lock:
            self._state.reservations = {
                k: v for k, v in self._state.reservations.items() if v != robot_id
            }

    async def apply_status(self, robot_id: str, payload: StatusPayload) -> None:
        async with self._lock:
            robot = self._state.robots.get(robot_id)
            if robot is None:
                robot = RobotState(robot_id=robot_id, position=payload.position)
                self._state.robots[robot_id] = robot

            robot.position = payload.position
            robot.status = payload.status
            robot.last_seen = payload.timestamp
            robot.path_index = payload.path_index
            robot.phase = payload.phase
            if payload.carrying_item_id is not None:
                robot.carrying_item_id = payload.carrying_item_id

            # Discrete pick / place completions
            if payload.action_done == "pick" and payload.carrying_item_id:
                item = self._state.items.get(payload.carrying_item_id)
                if item:
                    item.status = ItemStatus.CARRIED
                    item.carried_by = robot_id
                    item.position = payload.position
                    self._state.metrics.picks_completed += 1
                robot.carrying_item_id = payload.carrying_item_id
                # Advance pick_place task to TO_PLACE
                if payload.task_id:
                    for task in self._state.task_queue:
                        if task.task_id == payload.task_id:
                            task.phase = TaskPhase.TO_PLACE
                            task.status = TaskStatus.IN_PROGRESS
                            break

            if payload.action_done == "place":
                iid = payload.carrying_item_id or robot.carrying_item_id
                if iid:
                    item = self._state.items.get(iid)
                    if item:
                        item.status = ItemStatus.DELIVERED
                        item.carried_by = None
                        item.position = payload.position
                        self._state.metrics.places_completed += 1
                robot.carrying_item_id = None
                if payload.task_id:
                    for task in self._state.task_queue:
                        if task.task_id == payload.task_id:
                            task.status = TaskStatus.COMPLETE
                            task.phase = TaskPhase.DONE
                            robot.tasks_completed += 1
                            self._state.metrics.tasks_completed += 1
                            break

            if payload.status == RobotStatus.IDLE and not payload.action_done:
                robot.target = None
                robot.path = []
                robot.path_index = 0
                self._state.reservations = {
                    k: v for k, v in self._state.reservations.items() if v != robot_id
                }
                if payload.task_id and payload.phase in (TaskPhase.MOVE, TaskPhase.DONE):
                    for task in self._state.task_queue:
                        if task.task_id == payload.task_id and task.task_type == TaskType.MOVE:
                            task.status = TaskStatus.COMPLETE
                            robot.tasks_completed += 1
                            self._state.metrics.tasks_completed += 1
                            break
            elif payload.status == RobotStatus.MOVING and payload.task_id:
                for task in self._state.task_queue:
                    if task.task_id == payload.task_id and task.status == TaskStatus.ASSIGNED:
                        task.status = TaskStatus.IN_PROGRESS
                        break

    async def apply_error(self, robot_id: str, payload: ErrorPayload) -> None:
        async with self._lock:
            robot = self._state.robots.get(robot_id)
            if robot is None:
                robot = RobotState(robot_id=robot_id, position=payload.last_position)
                self._state.robots[robot_id] = robot
            robot.status = RobotStatus.ERROR
            robot.position = payload.last_position
            robot.target = None
            robot.path = []
            robot.path_index = 0
            robot.last_seen = payload.timestamp
            # Drop carried item back to shelf at last position
            if robot.carrying_item_id:
                item = self._state.items.get(robot.carrying_item_id)
                if item:
                    item.status = ItemStatus.ON_SHELF
                    item.carried_by = None
                    item.position = payload.last_position
                robot.carrying_item_id = None
            self._state.reservations = {
                key: owner for key, owner in self._state.reservations.items() if owner != robot_id
            }
            for task in self._state.task_queue:
                if task.assigned_to == robot_id and task.status in (
                    TaskStatus.ASSIGNED,
                    TaskStatus.IN_PROGRESS,
                ):
                    task.status = TaskStatus.FAILED
                    self._state.metrics.tasks_failed += 1

    async def mark_stale_robots(self, timeout_s: float, now: float | None = None) -> list[str]:
        current_time = time.time() if now is None else now
        stale_ids: list[str] = []
        async with self._lock:
            for robot in self._state.robots.values():
                # Only timeout robots that are actively executing — idle AGVs
                # may only send sparse heartbeats and must stay dispatchable.
                if (
                    robot.status in (
                        RobotStatus.MOVING,
                        RobotStatus.PICKING,
                        RobotStatus.PLACING,
                    )
                    and robot.last_seen > 0
                    and current_time - robot.last_seen > timeout_s
                ):
                    robot.status = RobotStatus.ERROR
                    robot.target = None
                    robot.path = []
                    robot.path_index = 0
                    stale_ids.append(robot.robot_id)
            if not stale_ids:
                return stale_ids
            stale_set = set(stale_ids)
            self._state.reservations = {
                key: owner
                for key, owner in self._state.reservations.items()
                if owner not in stale_set
            }
            for task in self._state.task_queue:
                if task.assigned_to in stale_set and task.status in (
                    TaskStatus.ASSIGNED,
                    TaskStatus.IN_PROGRESS,
                ):
                    task.status = TaskStatus.QUEUED
                    task.assigned_to = None
                    task.start_delay = 0
                    task.phase = (
                        TaskPhase.TO_PICK
                        if task.task_type == TaskType.PICK_PLACE
                        else TaskPhase.MOVE
                    )
        return stale_ids

    async def reset_robot(self, robot_id: str, now: float | None = None) -> RobotState | None:
        async with self._lock:
            robot = self._state.robots.get(robot_id)
            if robot is None:
                return None
            if robot.carrying_item_id:
                item = self._state.items.get(robot.carrying_item_id)
                if item:
                    item.status = ItemStatus.ON_SHELF
                    item.carried_by = None
                    item.position = robot.position
                robot.carrying_item_id = None
            robot.status = RobotStatus.IDLE
            robot.target = None
            robot.path = []
            robot.path_index = 0
            robot.phase = TaskPhase.MOVE
            robot.last_seen = time.time() if now is None else now
            return robot

    async def pop_queued_task(self) -> Task | None:
        async with self._lock:
            self._state.task_queue = [
                t for t in self._state.task_queue
                if t.status not in (TaskStatus.COMPLETE, TaskStatus.FAILED)
            ]
            candidates = [t for t in self._state.task_queue if t.status == TaskStatus.QUEUED]
            if not candidates:
                return None
            candidates.sort(
                key=lambda t: (-PRIORITY_WEIGHT.get(t.priority, 1), t.created_at)
            )
            best = candidates[0]
            self._state.task_queue.remove(best)
            return best

    async def get_task(self, task_id: str) -> Task | None:
        async with self._lock:
            for t in self._state.task_queue:
                if t.task_id == task_id:
                    return t
            return None

    async def needs_place_leg(self) -> list[tuple[RobotState, Task]]:
        """Robots that finished pick and need a to_place command."""
        async with self._lock:
            out: list[tuple[RobotState, Task]] = []
            for robot in self._state.robots.values():
                if robot.status != RobotStatus.IDLE or not robot.carrying_item_id:
                    continue
                for task in self._state.task_queue:
                    if (
                        task.assigned_to == robot.robot_id
                        and task.task_type == TaskType.PICK_PLACE
                        and task.phase == TaskPhase.TO_PLACE
                        and task.status == TaskStatus.IN_PROGRESS
                    ):
                        out.append((robot, task))
                        break
            return out
