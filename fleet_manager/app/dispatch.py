"""
Load-aware, multi-leg task scheduler for move + pick-and-place jobs.

Scoring (lower is better):
  MOVE:       path(start → target) + load
  PICK_PLACE: path(start → pick) + path(pick → place) + load
              with a small bonus if the robot is already near the pick zone

Priority biases the path weight so critical jobs prefer closer robots harder.
"""
from __future__ import annotations

import math
import time
from typing import Callable, Optional

from shared.schemas import Coordinate, RobotState, Task, TaskPriority, TaskType

PATH_COST_WEIGHT = 1.0
LOAD_WEIGHT = 3.0
LOAD_HALF_LIFE_S = 120.0
PICK_PROXIMITY_BONUS = 0.85  # multiply path cost when robot is near pick

PRIORITY_PATH_BIAS: dict[TaskPriority, float] = {
    TaskPriority.LOW: 0.7,
    TaskPriority.NORMAL: 1.0,
    TaskPriority.HIGH: 1.2,
    TaskPriority.CRITICAL: 1.45,
}

_task_load: dict[str, tuple[float, float]] = {}


def _decayed_load(robot_id: str, now: float | None = None) -> float:
    t = time.time() if now is None else now
    if robot_id not in _task_load:
        return 0.0
    value, ts = _task_load[robot_id]
    elapsed = max(0.0, t - ts)
    return value * math.pow(0.5, elapsed / LOAD_HALF_LIFE_S)


def record_assignment(robot_id: str, now: float | None = None) -> None:
    t = time.time() if now is None else now
    current = _decayed_load(robot_id, t)
    _task_load[robot_id] = (current + 1.0, t)


def reset_loads() -> None:
    _task_load.clear()


def score_robot_for_task(
    robot: RobotState,
    task: Task,
    path_cost_fn: Callable[[Coordinate, Coordinate], float],
    now: float | None = None,
) -> float:
    load = _decayed_load(robot.robot_id, now)
    path_w = PATH_COST_WEIGHT * PRIORITY_PATH_BIAS.get(task.priority, 1.0)

    if task.task_type == TaskType.PICK_PLACE:
        pick = task.pick or task.target
        place = task.place or task.target
        c1 = path_cost_fn(robot.position, pick)
        c2 = path_cost_fn(pick, place)
        if c1 == float("inf") or c2 == float("inf"):
            return float("inf")
        # Proximity bonus: if already close to pick, reduce first-leg weight
        if c1 <= 6.0:
            c1 *= PICK_PROXIMITY_BONUS
        path_total = c1 + c2
    else:
        path_total = path_cost_fn(robot.position, task.target)
        if path_total == float("inf"):
            return float("inf")

    return path_w * path_total + LOAD_WEIGHT * load


def select_robot_for_task(
    idle_robots: list[RobotState],
    task: Task,
    path_cost_fn: Callable[[Coordinate, Coordinate], float],
    now: float | None = None,
) -> Optional[RobotState]:
    if not idle_robots:
        return None
    scored: list[tuple[float, RobotState]] = []
    for r in idle_robots:
        s = score_robot_for_task(r, task, path_cost_fn, now)
        if s == float("inf"):
            continue
        scored.append((s, r))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0])
    best = scored[0][1]
    record_assignment(best.robot_id, now)
    return best


# Backward-compatible alias used by older call sites
def select_robot(
    idle_robots: list[RobotState],
    target: Coordinate,
    path_cost_fn: Callable[[Coordinate, Coordinate], float],
    now: float | None = None,
    priority: TaskPriority = TaskPriority.NORMAL,
) -> Optional[RobotState]:
    task = Task(
        task_id="_tmp",
        target=target,
        priority=priority,
        task_type=TaskType.MOVE,
    )
    return select_robot_for_task(idle_robots, task, path_cost_fn, now)
