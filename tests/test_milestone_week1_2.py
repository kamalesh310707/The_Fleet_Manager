"""Regression test for the Week 1-2 MuJoCo + A* milestone."""
from robots.run_milestone_demo import run


def test_all_robots_reach_hardcoded_targets():
    results = run()
    assert all(results.values()), f"Some robots failed: {results}"
