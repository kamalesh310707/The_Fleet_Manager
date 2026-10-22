"""
Robot agent process (blueprint Section 3.3).

Each robot runs as its OWN process with its own state machine — no shared
memory with other robots, coordination happens only via MQTT + the backend's
reservation table.
"""
import time
from collections.abc import Callable

import mujoco

from shared.schemas import Coordinate, RobotStatus
from robots.astar import find_path
from robots.controller import DriveController
from robots.grid_config import world_to_grid


class AgentFSM:
    """Pure state machine, no MQTT/MuJoCo — unit-testable in isolation."""

    def __init__(self, robot_id: str, start_position: Coordinate):
        self.robot_id = robot_id
        self.position = start_position
        self.status = RobotStatus.IDLE
        self.target: Coordinate | None = None
        self.path: list[Coordinate] = []
        self.last_command_time: float | None = None
        self.command_ack_timeout_s = 5.0

    def receive_command(self, target: Coordinate, path: list[Coordinate]) -> None:
        self.target = target
        self.path = path
        self.status = RobotStatus.MOVING
        self.last_command_time = time.time()

    def check_timeout(self) -> None:
        if (
            self.status == RobotStatus.MOVING
            and self.last_command_time is not None
            and time.time() - self.last_command_time > self.command_ack_timeout_s
        ):
            self.status = RobotStatus.ERROR

    def reset(self) -> None:
        self.status = RobotStatus.IDLE
        self.target = None
        self.path = []


class RobotAgent:
    """Wraps AgentFSM with MuJoCo driving."""

    def __init__(
        self,
        robot_id: str,
        model: mujoco.MjModel,
        data: mujoco.MjData,
        grid: list[list[int]],
    ):
        self.robot_id = robot_id
        self.model = model
        self.data = data
        self.grid = grid

        qpos_adr = model.joint(f"{robot_id}_free").qposadr[0]
        start_x, start_y = data.qpos[qpos_adr], data.qpos[qpos_adr + 1]
        self.fsm = AgentFSM(robot_id, world_to_grid((start_x, start_y)))

    def drive_to(
        self,
        target: Coordinate,
        path: list[Coordinate] | None = None,
        max_steps: int = 30_000,
        dt_steps_per_tick: int = 1,
        progress_callback: Callable[[Coordinate], None] | None = None,
        progress_interval_steps: int = 40,
        realtime_factor: float | None = None,
        on_step: Callable[[], None] | None = None,
    ) -> bool:
        """
        Drive along `path` (or plan one) until the robot reaches `target`.
        `on_step` is called every control tick (use it to viewer.sync()).
        """
        start_grid = self.fsm.position
        if path is None:
            path = find_path(self.grid, start_grid, target)
        if not path:
            self.fsm.status = RobotStatus.ERROR
            return False

        self.fsm.receive_command(target, path)
        controller = DriveController(self.model, self.data, self.robot_id, path)

        for step in range(max_steps):
            for _ in range(dt_steps_per_tick):
                mujoco.mj_step(self.model, self.data)
                if realtime_factor and realtime_factor > 0:
                    time.sleep(self.model.opt.timestep / realtime_factor)
            if on_step is not None:
                on_step()
            done = controller.step()
            if progress_callback and step % progress_interval_steps == 0:
                qpos_adr = self.model.joint(f"{self.robot_id}_free").qposadr[0]
                self.fsm.position = world_to_grid(
                    (self.data.qpos[qpos_adr], self.data.qpos[qpos_adr + 1])
                )
                idx = 0
                if path:
                    best = float("inf")
                    for i, cell in enumerate(path):
                        d = abs(cell[0] - self.fsm.position[0]) + abs(cell[1] - self.fsm.position[1])
                        if d < best:
                            best = d
                            idx = i
                try:
                    progress_callback(self.fsm.position, path_index=idx)
                except TypeError:
                    progress_callback(self.fsm.position)
            if done:
                qpos_adr = self.model.joint(f"{self.robot_id}_free").qposadr[0]
                final_xy = (self.data.qpos[qpos_adr], self.data.qpos[qpos_adr + 1])
                self.fsm.position = world_to_grid(final_xy)
                self.fsm.status = RobotStatus.IDLE
                return True

        self.fsm.status = RobotStatus.ERROR
        return False
