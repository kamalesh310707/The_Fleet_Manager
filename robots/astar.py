"""
Improved A* pathfinding for the warehouse fleet.

Upgrades over plain 4-connected A*:
  - 8-connected movement (diagonals allowed when both side cells are free)
  - Diagonal step cost √2
  - Optional soft cost map (e.g. reservation pressure, busy corridors)
  - Tie-breaking toward the goal for cleaner paths
"""
from __future__ import annotations

import heapq
import math
from shared.schemas import Coordinate

Grid = list[list[int]]  # 0 = free, 1 = obstacle

# (dx, dy, step_cost)
_NEIGHBORS_8 = [
    (1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
    (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)),
    (-1, 1, math.sqrt(2)), (-1, -1, math.sqrt(2)),
]


def _heuristic(a: Coordinate, b: Coordinate) -> float:
    """Octile distance — admissible for 8-connected grids."""
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    return (dx + dy) + (math.sqrt(2) - 2) * min(dx, dy)


def _in_bounds(grid: Grid, x: int, y: int) -> bool:
    return 0 <= x < len(grid) and 0 <= y < len(grid[0])


def _free(grid: Grid, x: int, y: int) -> bool:
    return _in_bounds(grid, x, y) and grid[x][y] == 0


def _neighbors(cell: Coordinate, grid: Grid) -> list[tuple[Coordinate, float]]:
    x, y = cell
    out: list[tuple[Coordinate, float]] = []
    for dx, dy, cost in _NEIGHBORS_8:
        nx, ny = x + dx, y + dy
        if not _free(grid, nx, ny):
            continue
        # Diagonal: both orthogonal side-cells must be free (no corner-cut)
        if dx != 0 and dy != 0:
            if not _free(grid, x + dx, y) or not _free(grid, x, y + dy):
                continue
        out.append(((nx, ny), cost))
    return out


def find_path(
    grid: Grid,
    start: Coordinate,
    goal: Coordinate,
    cost_map: dict[Coordinate, float] | None = None,
) -> list[Coordinate]:
    """
    Returns a path inclusive of start and goal, or [] if unreachable.
    cost_map adds non-negative soft penalties per cell (does not block).
    """
    if start == goal:
        return [start]
    if not _free(grid, start[0], start[1]) or not _free(grid, goal[0], goal[1]):
        return []

    # (f, tie, g, cell) — tie prefers cells closer to goal
    tie = 0
    open_set: list[tuple[float, int, float, Coordinate]] = []
    heapq.heappush(open_set, (_heuristic(start, goal), tie, 0.0, start))
    came_from: dict[Coordinate, Coordinate] = {}
    g_score: dict[Coordinate, float] = {start: 0.0}

    while open_set:
        _, _, g, current = heapq.heappop(open_set)
        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path

        if g > g_score.get(current, float("inf")):
            continue

        for neighbor, step in _neighbors(current, grid):
            extra = 0.0
            if cost_map:
                extra = cost_map.get(neighbor, 0.0)
            tentative = g + step + extra
            if tentative < g_score.get(neighbor, float("inf")):
                came_from[neighbor] = current
                g_score[neighbor] = tentative
                f = tentative + _heuristic(neighbor, goal)
                tie += 1
                heapq.heappush(open_set, (f, tie, tentative, neighbor))

    return []


def path_cost(
    grid: Grid,
    start: Coordinate,
    goal: Coordinate,
    cost_map: dict[Coordinate, float] | None = None,
) -> float:
    path = find_path(grid, start, goal, cost_map=cost_map)
    if not path:
        return float("inf")
    # Approximate length using consecutive octile steps
    total = 0.0
    for i in range(1, len(path)):
        dx = abs(path[i][0] - path[i - 1][0])
        dy = abs(path[i][1] - path[i - 1][1])
        total += math.sqrt(2) if dx and dy else 1.0
    return total


def build_reservation_cost_map(
    reservations: dict[str, str],
    horizon: int = 40,
    penalty: float = 0.35,
) -> dict[Coordinate, float]:
    """
    Soft cost for cells that appear in near-term space-time reservations.
    Encourages the planner to prefer quieter corridors without hard-blocking.
    """
    costs: dict[Coordinate, float] = {}
    for key, _owner in reservations.items():
        parts = key.split(",")
        if len(parts) != 3:
            continue
        try:
            x, y, t = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        if 0 <= t <= horizon:
            cell = (x, y)
            # Nearer timesteps cost more
            weight = penalty * (1.0 - (t / max(horizon, 1)) * 0.5)
            costs[cell] = costs.get(cell, 0.0) + weight
    return costs
