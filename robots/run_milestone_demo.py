"""
Week 1-2 milestone check:
"3 robots in MuJoCo, each independently drivable to a hardcoded target via a
direct function call."

Run:  python -m robots.run_milestone_demo
Also exercised by tests/test_milestone_week1_2.py.
"""
import mujoco

from robots.mujoco_arena import build_arena_xml, DEFAULT_ROBOTS
from robots.agent import RobotAgent
from robots.grid_config import scenario_grid

# Hardcoded targets chosen to be reachable around the default obstacles.
HARDCODED_TARGETS = {
    "robot_1": (1, 5),
    "robot_2": (1, 10),
    "robot_3": (1, 24),
    "robot_4": (28, 5),
    "robot_5": (28, 24),
}


def run() -> dict[str, bool]:
    xml = build_arena_xml()
    model = mujoco.MjModel.from_xml_string(xml)
    data = mujoco.MjData(model)
    grid = scenario_grid()

    results: dict[str, bool] = {}
    for spec in DEFAULT_ROBOTS:
        agent = RobotAgent(spec.name, model, data, grid)
        start = agent.fsm.position
        target = HARDCODED_TARGETS[spec.name]
        reached = agent.drive_to(target)
        results[spec.name] = reached
        print(
            f"{spec.name}: start_grid={start} -> target_grid={target} "
            f"| reached={reached} | final_grid={agent.fsm.position} "
            f"| status={agent.fsm.status.value}"
        )
    return results


if __name__ == "__main__":
    results = run()
    all_ok = all(results.values())
    print(f"\nMilestone {'PASSED' if all_ok else 'FAILED'}: {results}")
