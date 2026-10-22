"""Space-time reservation tests including temporal shift."""
import pytest

from fleet_manager.app.world_state import WorldStateStore
from fleet_manager.app import reservation
from shared.schemas import RobotState


@pytest.mark.asyncio
async def test_crossing_paths_second_reservation_rejected():
    store = WorldStateStore()
    await store.upsert_robot(RobotState(robot_id="robot_1", position=(0, 0)))
    await store.upsert_robot(RobotState(robot_id="robot_2", position=(2, 0)))
    path1 = [(0, 0), (1, 0), (2, 0)]
    assert await reservation.try_reserve_path(store, "robot_1", path1)
    path2 = [(2, 0), (1, 0), (0, 0)]
    assert not await reservation.try_reserve_path(store, "robot_2", path2)


@pytest.mark.asyncio
async def test_temporal_shift_resolves_crossing():
    store = WorldStateStore()
    await store.upsert_robot(RobotState(robot_id="robot_1", position=(0, 0)))
    await store.upsert_robot(RobotState(robot_id="robot_2", position=(2, 0)))
    path1 = [(0, 0), (1, 0), (2, 0)]
    assert await reservation.try_reserve_path(store, "robot_1", path1)
    path2 = [(2, 0), (1, 0), (0, 0)]
    result = await reservation.try_reserve_with_temporal_shift(store, "robot_2", path2, max_delay=5)
    assert result.ok
    assert result.start_delay >= 1


@pytest.mark.asyncio
async def test_progressive_release_frees_cells():
    store = WorldStateStore()
    path = [(0, 0), (1, 0), (2, 0), (3, 0)]
    assert await reservation.try_reserve_path(store, "robot_1", path)
    snap = store.snapshot()
    assert "0,0,0" in snap["reservations"]
    await reservation.release_behind(store, "robot_1", path, path_index=2)
    snap = store.snapshot()
    assert "0,0,0" not in snap["reservations"]
    assert "1,0,1" not in snap["reservations"]
    assert "2,0,2" in snap["reservations"]
    assert "3,0,3" in snap["reservations"]


@pytest.mark.asyncio
async def test_keys_for_path_with_delay():
    path = [(5, 5), (5, 6)]
    keys = reservation.keys_for_path(path, start_delay=2)
    assert keys == ["5,5,0", "5,5,1", "5,5,2", "5,6,3"]
