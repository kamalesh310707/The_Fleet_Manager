"""Start one MuJoCo robot controlled through MQTT.

Example (Windows PowerShell):
  $env:ROBOT_ID="robot_1"; $env:MUJOCO_VIEWER="1"; python -m robots.run_mqtt_agent

Run one process per robot ID. Set MUJOCO_VIEWER=1 to open a live 3D window.
"""
import os
import time

import mujoco

from robots.agent import RobotAgent
from robots.grid_config import scenario_grid
from robots.mqtt_agent import MqttRobotAgent
from robots.mujoco_arena import build_arena_xml, DEFAULT_ROBOTS


def main() -> None:
    robot_id = os.getenv("ROBOT_ID", "robot_1")
    use_viewer = os.getenv("MUJOCO_VIEWER", "0") == "1"
    realtime = float(os.getenv("ROBOT_REALTIME_FACTOR", "0.5"))

    # Only include this robot in the model for cleaner multi-process isolation.
    specs = [s for s in DEFAULT_ROBOTS if s.name == robot_id]
    if not specs:
        from robots.mujoco_arena import RobotSpec
        specs = [RobotSpec(robot_id, (1.0, 1.0), (0.7, 0.7, 0.2, 1))]

    xml = build_arena_xml(robots=specs)
    model = mujoco.MjModel.from_xml_string(xml)
    data = mujoco.MjData(model)
    grid = scenario_grid()

    agent = RobotAgent(robot_id, model, data, grid)
    bridge = MqttRobotAgent(agent)
    bridge.realtime_factor = realtime
    bridge.start()

    print(f"{robot_id} listening on {bridge.host}:{bridge.port} @ {realtime}x real-time")
    print(f"  start grid={agent.fsm.position}  viewer={'ON' if use_viewer else 'OFF'}")

    if use_viewer:
        try:
            # Import under a different name so it does not shadow the module-level `mujoco`
            from mujoco import viewer as mj_viewer
            with mj_viewer.launch_passive(model, data) as viewer:
                while viewer.is_running():
                    time.sleep(0.02)
                    viewer.sync()
        except Exception as e:
            print(f"Viewer unavailable ({e}); running headless.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
    else:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    bridge.stop()
    print(f"{robot_id} stopped.")


if __name__ == "__main__":
    main()
