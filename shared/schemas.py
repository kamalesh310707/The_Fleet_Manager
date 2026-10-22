"""
Shared data contracts for The Fleet Manager (pick-and-place warehouse fleet).
"""
from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


Coordinate = tuple[int, int]


class RobotStatus(str, Enum):
    IDLE = "idle"
    MOVING = "moving"
    PICKING = "picking"
    PLACING = "placing"
    ERROR = "error"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class TaskType(str, Enum):
    MOVE = "move"              # drive to a cell
    PICK_PLACE = "pick_place"  # pick item at A, place at B


class TaskPhase(str, Enum):
    TO_PICK = "to_pick"
    TO_PLACE = "to_place"
    MOVE = "move"
    DONE = "done"


class ItemStatus(str, Enum):
    ON_SHELF = "on_shelf"
    CARRIED = "carried"
    DELIVERED = "delivered"


PRIORITY_WEIGHT: dict[TaskPriority, int] = {
    TaskPriority.LOW: 0,
    TaskPriority.NORMAL: 1,
    TaskPriority.HIGH: 2,
    TaskPriority.CRITICAL: 3,
}


class ItemState(BaseModel):
    item_id: str
    position: Coordinate
    status: ItemStatus = ItemStatus.ON_SHELF
    carried_by: Optional[str] = None
    sku: str = "SKU-GEN"
    color: tuple[float, float, float] = (0.85, 0.55, 0.15)


class RobotState(BaseModel):
    robot_id: str
    position: Coordinate
    status: RobotStatus = RobotStatus.IDLE
    target: Optional[Coordinate] = None
    path: list[Coordinate] = Field(default_factory=list)
    last_seen: float = 0.0
    tasks_completed: int = 0
    path_index: int = 0
    start_delay: int = 0
    carrying_item_id: Optional[str] = None
    phase: TaskPhase = TaskPhase.MOVE


class Task(BaseModel):
    task_id: str
    task_type: TaskType = TaskType.MOVE
    target: Coordinate                     # primary / place cell
    pick: Optional[Coordinate] = None      # pick cell (pick_place)
    place: Optional[Coordinate] = None     # place cell (defaults to target)
    item_id: Optional[str] = None
    status: TaskStatus = TaskStatus.QUEUED
    assigned_to: Optional[str] = None
    created_at: float = 0.0
    priority: TaskPriority = TaskPriority.NORMAL
    start_delay: int = 0
    phase: TaskPhase = TaskPhase.MOVE


class FleetMetrics(BaseModel):
    tasks_dispatched: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    picks_completed: int = 0
    places_completed: int = 0
    conflicts_resolved_by_delay: int = 0
    conflicts_queued: int = 0
    reservations_active: int = 0
    robots_idle: int = 0
    robots_moving: int = 0
    robots_error: int = 0
    items_on_shelf: int = 0
    items_carried: int = 0
    items_delivered: int = 0
    avg_path_length: float = 0.0


class WorldState(BaseModel):
    robots: dict[str, RobotState] = Field(default_factory=dict)
    reservations: dict[str, str] = Field(default_factory=dict)
    task_queue: list[Task] = Field(default_factory=list)
    obstacles: list[Coordinate] = Field(default_factory=list)
    items: dict[str, ItemState] = Field(default_factory=dict)
    metrics: FleetMetrics = Field(default_factory=FleetMetrics)


class CommandPayload(BaseModel):
    target: Coordinate
    task_id: str
    path: list[Coordinate] = Field(default_factory=list)
    start_delay: int = 0
    phase: TaskPhase = TaskPhase.MOVE
    item_id: Optional[str] = None
    action: str = "drive"  # drive | pick | place


class StatusPayload(BaseModel):
    position: Coordinate
    status: RobotStatus
    task_id: Optional[str] = None
    timestamp: float
    path_index: int = 0
    phase: TaskPhase = TaskPhase.MOVE
    carrying_item_id: Optional[str] = None
    action_done: Optional[str] = None  # "pick" | "place" when a discrete action finished


class ErrorPayload(BaseModel):
    error_type: str
    last_position: Coordinate
    timestamp: float


class DispatchBroadcast(BaseModel):
    task_id: str
    target: Coordinate
    assigned_to: str
    timestamp: float
    start_delay: int = 0
    priority: TaskPriority = TaskPriority.NORMAL
    task_type: TaskType = TaskType.MOVE
    phase: TaskPhase = TaskPhase.MOVE
    item_id: Optional[str] = None


def topic_command(robot_id: str) -> str:
    return f"fleet/robot/{robot_id}/command"


def topic_status(robot_id: str) -> str:
    return f"fleet/robot/{robot_id}/status"


def topic_error(robot_id: str) -> str:
    return f"fleet/robot/{robot_id}/error"


TOPIC_DISPATCH_BROADCAST = "fleet/dispatch/broadcast"
TOPIC_ALL_STATUS = "fleet/robot/+/status"
TOPIC_ALL_ERROR = "fleet/robot/+/error"
