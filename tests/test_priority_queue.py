"""Priority-aware task queue ordering."""
import pytest

from fleet_manager.app.world_state import WorldStateStore
from shared.schemas import Task, TaskPriority, TaskStatus


@pytest.mark.asyncio
async def test_critical_jumps_low_priority_queue():
    store = WorldStateStore()
    await store.add_task(Task(task_id="low", target=(1, 1), priority=TaskPriority.LOW, created_at=1.0))
    await store.add_task(Task(task_id="crit", target=(2, 2), priority=TaskPriority.CRITICAL, created_at=2.0))
    await store.add_task(Task(task_id="norm", target=(3, 3), priority=TaskPriority.NORMAL, created_at=1.5))
    first = await store.pop_queued_task()
    assert first is not None and first.task_id == "crit"
    first.status = TaskStatus.ASSIGNED
    second = await store.pop_queued_task()
    assert second is not None and second.task_id == "norm"
    second.status = TaskStatus.ASSIGNED
    third = await store.pop_queued_task()
    assert third is not None and third.task_id == "low"
