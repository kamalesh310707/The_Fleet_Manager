import pytest

from fleet_manager.app.world_state import WorldStateStore
from shared.schemas import ErrorPayload, RobotState, RobotStatus, StatusPayload, Task, TaskStatus


@pytest.mark.asyncio
async def test_idle_status_completes_task_and_clears_reservations():
    store = WorldStateStore()
    await store.upsert_robot(RobotState(robot_id="robot_1", position=(1, 1), status=RobotStatus.MOVING, target=(3, 3)))
    await store.add_task(Task(task_id="task-1", target=(3, 3), status=TaskStatus.ASSIGNED, assigned_to="robot_1"))
    assert await store.reserve("1,1,0", "robot_1")

    await store.apply_status("robot_1", StatusPayload(position=(3, 3), status=RobotStatus.IDLE, task_id="task-1", timestamp=10.0))

    snapshot = store.snapshot()
    assert snapshot["robots"]["robot_1"]["status"] == RobotStatus.IDLE
    assert snapshot["task_queue"][0]["status"] == TaskStatus.COMPLETE
    assert snapshot["reservations"] == {}


@pytest.mark.asyncio
async def test_moving_status_updates_position_and_marks_task_in_progress():
    store = WorldStateStore()
    await store.upsert_robot(RobotState(robot_id="robot_1", position=(1, 1)))
    await store.add_task(Task(task_id="task-1", target=(3, 3), status=TaskStatus.ASSIGNED, assigned_to="robot_1"))

    await store.apply_status("robot_1", StatusPayload(position=(2, 1), status=RobotStatus.MOVING, task_id="task-1", timestamp=10.0))

    snapshot = store.snapshot()
    assert snapshot["robots"]["robot_1"]["position"] == (2, 1)
    assert snapshot["task_queue"][0]["status"] == TaskStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_error_status_marks_active_task_failed():
    store = WorldStateStore()
    await store.upsert_robot(RobotState(robot_id="robot_1", position=(1, 1)))
    await store.add_task(Task(task_id="task-1", target=(3, 3), status=TaskStatus.ASSIGNED, assigned_to="robot_1"))

    await store.apply_error("robot_1", ErrorPayload(error_type="drive_timeout", last_position=(2, 2), timestamp=10.0))

    snapshot = store.snapshot()
    assert snapshot["robots"]["robot_1"]["status"] == RobotStatus.ERROR
    assert snapshot["task_queue"][0]["status"] == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_stale_robot_is_failed_and_its_task_is_requeued():
    store = WorldStateStore()
    await store.upsert_robot(RobotState(robot_id="robot_1", position=(1, 1), status=RobotStatus.MOVING, target=(3, 3), path=[(1, 1), (2, 1)], last_seen=10.0))
    await store.add_task(Task(task_id="task-1", target=(3, 3), status=TaskStatus.IN_PROGRESS, assigned_to="robot_1"))
    assert await store.reserve("1,1,0", "robot_1")

    assert await store.mark_stale_robots(timeout_s=15.0, now=30.0) == ["robot_1"]

    snapshot = store.snapshot()
    assert snapshot["robots"]["robot_1"]["status"] == RobotStatus.ERROR
    assert snapshot["reservations"] == {}
    assert snapshot["task_queue"][0]["status"] == TaskStatus.QUEUED
    assert snapshot["task_queue"][0]["assigned_to"] is None
