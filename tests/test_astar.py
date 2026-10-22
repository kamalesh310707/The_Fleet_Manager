from robots.astar import find_path, path_cost


def test_straight_line_no_obstacles():
    grid = [[0] * 5 for _ in range(5)]
    path = find_path(grid, (0, 0), (0, 4))
    assert path[0] == (0, 0)
    assert path[-1] == (0, 4)
    assert len(path) == 5


def test_unreachable_returns_empty():
    grid = [
        [0, 1, 0],
        [0, 1, 0],
        [0, 1, 0],
    ]
    path = find_path(grid, (0, 0), (0, 2))
    assert path == []


def test_path_cost_matches_length_minus_one():
    grid = [[0] * 3 for _ in range(3)]
    assert path_cost(grid, (0, 0), (0, 2)) == 2.0


def test_path_cost_inf_when_unreachable():
    grid = [
        [0, 1],
        [1, 0],
    ]
    assert path_cost(grid, (0, 0), (1, 1)) == float("inf")
