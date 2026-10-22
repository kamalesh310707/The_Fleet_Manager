"""
Space-time reservation table (blueprint Section 3.5).

Upgrades:
  - Temporal shift: retry path with delayed start timesteps before queueing.
  - Progressive release: free cells behind a moving robot early.
"""
from __future__ import annotations

from dataclasses import dataclass

from shared.schemas import Coordinate
from fleet_manager.app.world_state import WorldStateStore


def reservation_key(cell: Coordinate, t: int) -> str:
    x, y = cell
    return f"{x},{y},{t}"


@dataclass
class ReservationResult:
    ok: bool
    start_delay: int = 0
    keys: list[str] | None = None


def keys_for_path(path: list[Coordinate], start_delay: int = 0) -> list[str]:
    if not path:
        return []
    keys: list[str] = []
    for t in range(start_delay):
        keys.append(reservation_key(path[0], t))
    for i, cell in enumerate(path):
        keys.append(reservation_key(cell, start_delay + i))
    return keys


async def try_reserve_path(
    store: WorldStateStore,
    robot_id: str,
    path: list[Coordinate],
    start_delay: int = 0,
) -> bool:
    keys = keys_for_path(path, start_delay)
    return await store.try_reserve_path(robot_id, keys)


async def try_reserve_with_temporal_shift(
    store: WorldStateStore,
    robot_id: str,
    path: list[Coordinate],
    max_delay: int = 12,
) -> ReservationResult:
    if not path:
        return ReservationResult(ok=False)
    for delay in range(max_delay + 1):
        keys = keys_for_path(path, delay)
        if await store.try_reserve_path(robot_id, keys):
            return ReservationResult(ok=True, start_delay=delay, keys=keys)
    return ReservationResult(ok=False)


async def release_behind(
    store: WorldStateStore,
    robot_id: str,
    path: list[Coordinate],
    path_index: int,
    start_delay: int = 0,
) -> None:
    if not path or path_index <= 0:
        return
    stale: list[str] = []
    if path_index > 0:
        for t in range(start_delay):
            stale.append(reservation_key(path[0], t))
    for i in range(path_index):
        stale.append(reservation_key(path[i], start_delay + i))
    if stale:
        await store.release_keys(robot_id, stale)
