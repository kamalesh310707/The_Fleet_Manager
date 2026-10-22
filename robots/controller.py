"""
Differential-drive waypoint controller (Week 1-2 milestone).

Takes an A* grid path, converts it to world-frame waypoints via
grid_config.grid_to_world(), and drives a robot's two wheel actuators to
follow it using simple proportional control on heading + distance error.

This is intentionally simple (no PID, no trajectory smoothing) — the
blueprint's ambition is centralized dispatch/collision-avoidance, not
control theory, so "reaches the target reliably" is the bar here, not
"drives smoothly."
"""
import math

import mujoco

from robots.mujoco_arena import WHEEL_BASE, WHEEL_RADIUS
from robots.grid_config import grid_to_world
from shared.schemas import Coordinate

WAYPOINT_TOLERANCE = 0.12       # meters — how close counts as "reached" the final destination
INTERMEDIATE_TOLERANCE = 0.28   # meters — tolerance for intermediate waypoints along the route
MAX_FORWARD_SPEED = 0.75        # m/s
MAX_ANGULAR_SPEED = 2.8         # rad/s — capped independently to avoid whiplash wheel commands
HEADING_KP = 2.2
DISTANCE_KP = 1.0


def _yaw_from_quat(quat: tuple[float, float, float, float]) -> float:
    """quat is (w, x, y, z) as MuJoCo stores freejoint orientation."""
    w, x, y, z = quat
    siny_cosp = 2 * (w * z + x * y)
    cosy_cosp = 1 - 2 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


class DriveController:
    """One instance per robot. Call step() every sim tick until it returns True (done)."""

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, robot_name: str,
                 grid_path: list[Coordinate]):
        self.model = model
        self.data = data
        self.robot_name = robot_name
        self.waypoints = [grid_to_world(cell) for cell in grid_path]
        self._wp_index = 0

        self._qpos_adr = model.joint(f"{robot_name}_free").qposadr[0]
        self._left_act = model.actuator(f"{robot_name}_wheel_L_act").id
        self._right_act = model.actuator(f"{robot_name}_wheel_R_act").id

        self._last_x = float(data.qpos[self._qpos_adr])
        self._last_y = float(data.qpos[self._qpos_adr + 1])
        self._stuck_ticks = 0

    def _chassis_pose(self) -> tuple[float, float, float]:
        qpos = self.data.qpos[self._qpos_adr: self._qpos_adr + 7]
        x, y = qpos[0], qpos[1]
        quat = (qpos[3], qpos[4], qpos[5], qpos[6])  # w,x,y,z
        yaw = _yaw_from_quat(quat)
        return x, y, yaw

    def step(self) -> bool:
        """Advances one control tick. Returns True once the final waypoint is reached."""
        if self._wp_index >= len(self.waypoints):
            self.data.ctrl[self._left_act] = 0.0
            self.data.ctrl[self._right_act] = 0.0
            return True

        x, y, yaw = self._chassis_pose()
        target_x, target_y = self.waypoints[self._wp_index]
        dx, dy = target_x - x, target_y - y
        distance = math.hypot(dx, dy)

        is_last = (self._wp_index == len(self.waypoints) - 1)
        tol = WAYPOINT_TOLERANCE if is_last else INTERMEDIATE_TOLERANCE

        advance = False
        if distance < tol:
            advance = True
        elif not is_last:
            # Check if robot has passed the waypoint along the segment to next waypoint
            next_x, next_y = self.waypoints[self._wp_index + 1]
            seg_dx = next_x - target_x
            seg_dy = next_y - target_y
            seg_len_sq = seg_dx * seg_dx + seg_dy * seg_dy
            if seg_len_sq > 1e-4:
                proj = (x - target_x) * seg_dx + (y - target_y) * seg_dy
                if proj > 0:
                    advance = True
            d_next = math.hypot(next_x - x, next_y - y)
            if d_next < distance:
                advance = True

        # Anti-stall watchdog: if robot is stationary / trapped against geometry
        if not advance:
            move_dist = math.hypot(x - self._last_x, y - self._last_y)
            if move_dist < 0.008:
                self._stuck_ticks += 1
            else:
                self._stuck_ticks = 0
                self._last_x, self._last_y = x, y

            if self._stuck_ticks > 60:
                self._stuck_ticks = 0
                if not is_last:
                    advance = True
                elif distance < 0.28:
                    self._wp_index = len(self.waypoints)
                    self.data.ctrl[self._left_act] = 0.0
                    self.data.ctrl[self._right_act] = 0.0
                    return True

        if advance:
            self._wp_index += 1
            self._stuck_ticks = 0
            self._last_x, self._last_y = x, y
            if self._wp_index >= len(self.waypoints):
                self.data.ctrl[self._left_act] = 0.0
                self.data.ctrl[self._right_act] = 0.0
                return True
            target_x, target_y = self.waypoints[self._wp_index]
            dx, dy = target_x - x, target_y - y
            distance = math.hypot(dx, dy)

        target_heading = math.atan2(dy, dx)
        heading_error = math.atan2(
            math.sin(target_heading - yaw), math.cos(target_heading - yaw)
        )

        forward_speed = MAX_FORWARD_SPEED * max(0.0, 1 - abs(heading_error) / (math.pi / 2))
        forward_speed = min(forward_speed, DISTANCE_KP * distance)
        angular_speed = max(-MAX_ANGULAR_SPEED, min(MAX_ANGULAR_SPEED, HEADING_KP * heading_error))

        left_wheel_vel = (forward_speed - angular_speed * WHEEL_BASE / 2) / WHEEL_RADIUS
        right_wheel_vel = (forward_speed + angular_speed * WHEEL_BASE / 2) / WHEEL_RADIUS

        self.data.ctrl[self._left_act] = left_wheel_vel
        self.data.ctrl[self._right_act] = right_wheel_vel
        return False
