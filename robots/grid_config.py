"""
Grid <-> world coordinate mapping for the warehouse deployment.
30×30 cells ≈ 18 m × 18 m floor (CELL_SIZE 0.6 m) — aisle-scale warehouse section.
"""
from shared.schemas import Coordinate

GRID_SIZE = 30
CELL_SIZE = 0.6
ARENA_MARGIN = 1.0


def grid_to_world(cell: Coordinate) -> tuple[float, float]:
    x, y = cell
    return (x * CELL_SIZE + CELL_SIZE / 2, y * CELL_SIZE + CELL_SIZE / 2)


def world_to_grid(pos: tuple[float, float]) -> Coordinate:
    x, y = pos
    gx = int(x // CELL_SIZE)
    gy = int(y // CELL_SIZE)
    gx = max(0, min(GRID_SIZE - 1, gx))
    gy = max(0, min(GRID_SIZE - 1, gy))
    return (gx, gy)


def arena_half_extent() -> float:
    return (GRID_SIZE * CELL_SIZE) / 2 + ARENA_MARGIN


def empty_grid() -> list[list[int]]:
    return [[0] * GRID_SIZE for _ in range(GRID_SIZE)]


def _rack_aisle(x0: int, y0: int, rows: int, cols: int, gap: int = 1) -> list[Coordinate]:
    """Solid rack block with optional internal gap for a side aisle."""
    cells: list[Coordinate] = []
    for dx in range(rows):
        for dy in range(cols):
            if gap and dy == cols // 2:
                continue
            cells.append((x0 + dx, y0 + dy))
    return cells


# Multi-aisle warehouse: long storage racks, open travel lanes, dock edge free.
DEFAULT_OBSTACLES: list[Coordinate] = []
# Aisle racks along X (storage banks)
for _x0 in (4, 8, 12, 16, 20, 24):
    DEFAULT_OBSTACLES.extend(_rack_aisle(_x0, 4, 2, 10))
    DEFAULT_OBSTACLES.extend(_rack_aisle(_x0, 16, 2, 10))
# Extra block near packing
DEFAULT_OBSTACLES.extend(_rack_aisle(6, 26, 3, 3, gap=0))
DEFAULT_OBSTACLES.extend(_rack_aisle(20, 26, 3, 3, gap=0))
# Deduplicate
DEFAULT_OBSTACLES = sorted(set(DEFAULT_OBSTACLES))


# Default inventory spawn cells (free cells adjacent to racks / docks)
DEFAULT_ITEM_SPAWNS: list[Coordinate] = [
    (3, 5), (3, 8), (3, 12), (3, 18), (3, 22),
    (11, 3), (15, 3), (19, 3), (23, 3),
    (7, 15), (11, 15), (15, 15), (19, 15),
    (27, 8), (27, 12), (27, 18), (27, 22),
    (5, 28), (10, 28), (18, 28), (25, 28),
]

# Drop / shipping bays (free cells)
DEFAULT_PLACE_BAYS: list[Coordinate] = [
    (1, 1), (1, 14), (1, 28),
    (14, 1), (28, 1),
    (28, 14), (28, 28), (14, 28),
]


def scenario_grid(obstacles: list[Coordinate] | None = None) -> list[list[int]]:
    grid = empty_grid()
    obs = obstacles if obstacles is not None else DEFAULT_OBSTACLES
    for x, y in obs:
        if 0 <= x < GRID_SIZE and 0 <= y < GRID_SIZE:
            grid[x][y] = 1
    return grid


def obstacle_list(grid: list[list[int]] | None = None) -> list[Coordinate]:
    if grid is None:
        return list(DEFAULT_OBSTACLES)
    return [(x, y) for x in range(len(grid)) for y in range(len(grid[0])) if grid[x][y] == 1]
