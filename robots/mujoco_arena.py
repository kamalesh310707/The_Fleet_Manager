"""
Warehouse arena: 30×30 floor, 5 carrier AGVs with load decks, physical inventory boxes.
"""
from __future__ import annotations

from dataclasses import dataclass

from robots.grid_config import (
    arena_half_extent,
    grid_to_world,
    CELL_SIZE,
    DEFAULT_OBSTACLES,
    DEFAULT_ITEM_SPAWNS,
    GRID_SIZE,
)

CHASSIS_LENGTH = 0.34
CHASSIS_WIDTH = 0.24
CHASSIS_HEIGHT = 0.11
WHEEL_RADIUS = 0.05
WHEEL_WIDTH = 0.03
WHEEL_BASE = CHASSIS_WIDTH + 0.05
CHASSIS_Z = 0.075
LOAD_BED_HEIGHT = 0.03
BEACON_HEIGHT = 0.05
# Cargo box on deck (carrier)
CARGO_SIZE = (0.10, 0.10, 0.08)


@dataclass
class RobotSpec:
    name: str
    start_pos: tuple[float, float]
    color: tuple[float, float, float, float]


@dataclass
class ItemSpec:
    item_id: str
    cell: tuple[int, int]
    color: tuple[float, float, float]


DEFAULT_ROBOTS = [
    RobotSpec("robot_1", grid_to_world((1, 1)), (0.90, 0.22, 0.18, 1.0)),
    RobotSpec("robot_2", grid_to_world((1, 15)), (0.15, 0.72, 0.35, 1.0)),
    RobotSpec("robot_3", grid_to_world((1, 28)), (0.18, 0.42, 0.92, 1.0)),
    RobotSpec("robot_4", grid_to_world((28, 1)), (0.95, 0.75, 0.15, 1.0)),
    RobotSpec("robot_5", grid_to_world((28, 28)), (0.75, 0.30, 0.85, 1.0)),
]

ITEM_COLORS = [
    (0.90, 0.55, 0.12),
    (0.20, 0.70, 0.90),
    (0.85, 0.30, 0.65),
    (0.95, 0.85, 0.20),
    (0.35, 0.85, 0.45),
    (0.90, 0.40, 0.35),
]


def default_items() -> list[ItemSpec]:
    items: list[ItemSpec] = []
    for i, cell in enumerate(DEFAULT_ITEM_SPAWNS):
        items.append(
            ItemSpec(
                item_id=f"item_{i + 1:02d}",
                cell=cell,
                color=ITEM_COLORS[i % len(ITEM_COLORS)],
            )
        )
    return items


def _assets_xml() -> str:
    return """
  <asset>
    <texture type="skybox" builtin="gradient"
             rgb1="0.07 0.09 0.12" rgb2="0.02 0.02 0.04" width="512" height="512"/>
    <texture name="tex_floor" type="2d" builtin="checker"
             rgb1="0.68 0.70 0.72" rgb2="0.56 0.58 0.61"
             width="512" height="512" mark="edge" markrgb="0.48 0.50 0.52"/>
    <texture name="tex_wall" type="2d" builtin="flat" rgb1="0.18 0.22 0.28" width="64" height="64"/>
    <texture name="tex_rack" type="2d" builtin="gradient"
             rgb1="0.38 0.24 0.12" rgb2="0.24 0.14 0.08" width="64" height="64"/>
    <texture name="tex_metal" type="2d" builtin="flat" rgb1="0.50 0.52 0.56" width="32" height="32"/>
    <material name="mat_floor" texture="tex_floor" texrepeat="20 20" reflectance="0.05"/>
    <material name="mat_wall" texture="tex_wall"/>
    <material name="mat_rack" texture="tex_rack" reflectance="0.08"/>
    <material name="mat_metal" texture="tex_metal" reflectance="0.25"/>
    <material name="mat_lane" rgba="0.95 0.80 0.10 1"/>
    <material name="mat_grid" rgba="0.28 0.48 0.55 0.35"/>
    <material name="mat_post" rgba="0.15 0.17 0.20 1"/>
    <material name="mat_carrier" rgba="0.25 0.28 0.32 1" reflectance="0.2"/>
  </asset>
"""


def _lights_xml(cx: float, cy: float) -> str:
    return f"""
    <light name="oh1" diffuse="1 0.98 0.92" pos="{cx - 5} {cy - 5} 9" dir="0.1 0.1 -1" castshadow="true"/>
    <light name="oh2" diffuse="1 0.98 0.92" pos="{cx + 5} {cy - 5} 9" dir="-0.1 0.1 -1" castshadow="true"/>
    <light name="oh3" diffuse="0.9 0.9 0.95" pos="{cx - 5} {cy + 5} 9" dir="0.1 -0.1 -1"/>
    <light name="oh4" diffuse="0.9 0.9 0.95" pos="{cx + 5} {cy + 5} 9" dir="-0.1 -0.1 -1"/>
    <light name="fill" diffuse="0.2 0.22 0.26" pos="{cx} {cy} 14" dir="0 0 -1"/>
"""


def _floor_lanes(cx: float, cy: float, half: float) -> str:
    parts = [
        f'''    <geom name="floor" type="plane" size="{half + 2} {half + 2} 0.1"
          pos="{cx} {cy} 0" material="mat_floor" friction="1.2 0.005 0.0001"/>'''
    ]
    span = GRID_SIZE * CELL_SIZE / 2 - 0.4
    for i, y in enumerate([2.0, 9.0, 15.0, GRID_SIZE * CELL_SIZE - 2.0]):
        parts.append(
            f'''    <geom name="lane_h_{i}" type="box" pos="{cx} {y} 0.002"
          size="{span} 0.06 0.001" material="mat_lane" contype="0" conaffinity="0"/>'''
        )
    for i, x in enumerate([2.0, 9.0, 15.0, GRID_SIZE * CELL_SIZE - 2.0]):
        parts.append(
            f'''    <geom name="lane_v_{i}" type="box" pos="{x} {cy} 0.002"
          size="0.06 {span} 0.001" material="mat_lane" contype="0" conaffinity="0"/>'''
        )
    return "\n".join(parts)


def _wall_geoms() -> str:
    wall_h, wall_t = 0.75, 0.14
    cx = (GRID_SIZE * CELL_SIZE) / 2
    cy = (GRID_SIZE * CELL_SIZE) / 2
    extent = (GRID_SIZE * CELL_SIZE) / 2 + 0.35
    post_h = 0.95
    walls = f"""
    <geom name="wall_px" type="box" pos="{cx + extent} {cy} {wall_h/2}"
          size="{wall_t} {extent + wall_t} {wall_h/2}" material="mat_wall"/>
    <geom name="wall_nx" type="box" pos="{cx - extent} {cy} {wall_h/2}"
          size="{wall_t} {extent + wall_t} {wall_h/2}" material="mat_wall"/>
    <geom name="wall_py" type="box" pos="{cx} {cy + extent} {wall_h/2}"
          size="{extent + wall_t} {wall_t} {wall_h/2}" material="mat_wall"/>
    <geom name="wall_ny" type="box" pos="{cx} {cy - extent} {wall_h/2}"
          size="{extent + wall_t} {wall_t} {wall_h/2}" material="mat_wall"/>
"""
    posts = []
    for i, (px, py) in enumerate(
        [(cx + extent, cy + extent), (cx + extent, cy - extent),
         (cx - extent, cy + extent), (cx - extent, cy - extent)]
    ):
        posts.append(
            f'''    <geom name="post_{i}" type="cylinder" pos="{px} {py} {post_h/2}"
          size="0.13 {post_h/2}" material="mat_post"/>'''
        )
    return walls + "\n".join(posts)


def _rack_obstacle(i: int, gx: int, gy: int) -> str:
    wx, wy = grid_to_world((gx, gy))
    half = CELL_SIZE / 2 * 0.78
    s = half * 0.95
    return f"""
    <geom name="obs_coll_{i}" type="box" pos="{wx} {wy} 0.28"
          size="{half} {half} 0.28" rgba="0.2 0.14 0.1 0.1"
          friction="1 0.005 0.0001" contype="1" conaffinity="1"/>
    <geom name="obs_base_{i}" type="box" pos="{wx} {wy} 0.03"
          size="{s} {s} 0.03" material="mat_metal" contype="0" conaffinity="0"/>
    <geom name="obs_s0_{i}" type="box" pos="{wx} {wy} 0.2"
          size="{s} {s} 0.02" material="mat_rack" contype="0" conaffinity="0"/>
    <geom name="obs_s1_{i}" type="box" pos="{wx} {wy} 0.4"
          size="{s} {s} 0.02" material="mat_rack" contype="0" conaffinity="0"/>
    <geom name="obs_top_{i}" type="box" pos="{wx} {wy} 0.55"
          size="{s} {s} 0.015" material="mat_metal" contype="0" conaffinity="0"/>
"""


def _obstacle_geoms(obstacles=None) -> str:
    obs = obstacles if obstacles is not None else DEFAULT_OBSTACLES
    return "\n".join(_rack_obstacle(i, gx, gy) for i, (gx, gy) in enumerate(obs))


def _grid_lines() -> str:
    lines = []
    z = 0.003
    for i in range(0, GRID_SIZE + 1, 2):
        x = i * CELL_SIZE
        lines.append(
            f'''    <geom name="gv_{i}" type="capsule"
          fromto="{x} 0 {z} {x} {GRID_SIZE * CELL_SIZE} {z}" size="0.004"
          material="mat_grid" contype="0" conaffinity="0"/>'''
        )
        y = i * CELL_SIZE
        lines.append(
            f'''    <geom name="gh_{i}" type="capsule"
          fromto="0 {y} {z} {GRID_SIZE * CELL_SIZE} {y} {z}" size="0.004"
          material="mat_grid" contype="0" conaffinity="0"/>'''
        )
    return "\n".join(lines)


def _robot_body_xml(spec: RobotSpec) -> str:
    """Carrier AGV: chassis + dedicated cargo cradle on the load deck."""
    x, y = spec.start_pos
    r, g, b, a = spec.color
    body = f"{r} {g} {b} {a}"
    deck = f"{min(r * 1.1, 1)} {min(g * 1.1, 1)} {min(b * 1.1, 1)} 1"
    beacon = f"{min(r * 1.3, 1)} {min(g * 1.3, 1)} {min(b * 1.3, 1)} 1"
    wheel = "0.06 0.06 0.07 1"
    hl, hw, hh = CHASSIS_LENGTH / 2, CHASSIS_WIDTH / 2, CHASSIS_HEIGHT / 2
    cx, cy, cz = CARGO_SIZE

    return f"""
    <body name="{spec.name}_chassis" pos="{x} {y} {CHASSIS_Z}">
      <freejoint name="{spec.name}_free"/>
      <geom name="{spec.name}_body" type="box" size="{hl} {hw} {hh}"
            rgba="{body}" mass="3.2" friction="0.5 0.005 0.0001"/>
      <geom name="{spec.name}_bumper" type="box" pos="{hl - 0.015} 0 {-hh * 0.1}"
            size="0.015 {hw * 0.9} {hh * 0.45}" rgba="0.1 0.1 0.12 1"
            mass="0.08" contype="0" conaffinity="0"/>
      <!-- Load deck -->
      <geom name="{spec.name}_load_bed" type="box"
            pos="0 0 {hh + LOAD_BED_HEIGHT / 2}"
            size="{hl - 0.02} {hw - 0.02} {LOAD_BED_HEIGHT / 2}"
            rgba="{deck}" mass="0.5"/>
      <!-- Carrier cradle (always visible — the bot's own carrier) -->
      <geom name="{spec.name}_cradle_L" type="box"
            pos="{-cx * 0.7} {cy * 0.85} {hh + LOAD_BED_HEIGHT + 0.04}"
            size="0.012 {cy * 0.15} 0.04" material="mat_carrier"
            contype="0" conaffinity="0"/>
      <geom name="{spec.name}_cradle_R" type="box"
            pos="{-cx * 0.7} {-cy * 0.85} {hh + LOAD_BED_HEIGHT + 0.04}"
            size="0.012 {cy * 0.15} 0.04" material="mat_carrier"
            contype="0" conaffinity="0"/>
      <geom name="{spec.name}_cradle_F" type="box"
            pos="{cx * 0.7} 0 {hh + LOAD_BED_HEIGHT + 0.035}"
            size="0.012 {cy * 0.9} 0.035" material="mat_carrier"
            contype="0" conaffinity="0"/>
      <geom name="{spec.name}_cradle_B" type="box"
            pos="{-cx * 0.7} 0 {hh + LOAD_BED_HEIGHT + 0.035}"
            size="0.012 {cy * 0.9} 0.035" material="mat_carrier"
            contype="0" conaffinity="0"/>
      <!-- Occupied cargo marker (toggled by viewer via rgba alpha) -->
      <geom name="{spec.name}_payload" type="box"
            pos="0 0 {hh + LOAD_BED_HEIGHT + cz / 2 + 0.01}"
            size="{cx} {cy} {cz}"
            rgba="0.9 0.55 0.15 0" mass="0.01" contype="0" conaffinity="0"/>
      <geom name="{spec.name}_beacon" type="cylinder"
            pos="{hl * 0.55} 0 {hh + LOAD_BED_HEIGHT + BEACON_HEIGHT / 2 + 0.02}"
            size="0.022 {BEACON_HEIGHT / 2}" rgba="{beacon}" mass="0.02"
            contype="0" conaffinity="0"/>
      <body name="{spec.name}_wheel_L" pos="0 {WHEEL_BASE / 2} {-CHASSIS_Z + WHEEL_RADIUS}">
        <joint name="{spec.name}_wheel_L_joint" type="hinge" axis="0 1 0" damping="0.25"/>
        <geom type="cylinder" size="{WHEEL_RADIUS} {WHEEL_WIDTH / 2}" euler="90 0 0"
              rgba="{wheel}" friction="1.6 0.05 0.0001" mass="0.25"/>
      </body>
      <body name="{spec.name}_wheel_R" pos="0 {-WHEEL_BASE / 2} {-CHASSIS_Z + WHEEL_RADIUS}">
        <joint name="{spec.name}_wheel_R_joint" type="hinge" axis="0 1 0" damping="0.25"/>
        <geom type="cylinder" size="{WHEEL_RADIUS} {WHEEL_WIDTH / 2}" euler="90 0 0"
              rgba="{wheel}" friction="1.6 0.05 0.0001" mass="0.25"/>
      </body>
      <body name="{spec.name}_caster_front" pos="{hl - 0.045} 0 {-CHASSIS_Z + 0.026}">
        <joint name="{spec.name}_caster_front_joint" type="ball" damping="0.02"/>
        <geom type="sphere" size="0.026" rgba="{wheel}" friction="0.05 0.002 0.0001" mass="0.08"/>
      </body>
      <body name="{spec.name}_caster_rear" pos="{-hl + 0.045} 0 {-CHASSIS_Z + 0.026}">
        <joint name="{spec.name}_caster_rear_joint" type="ball" damping="0.02"/>
        <geom type="sphere" size="0.026" rgba="{wheel}" friction="0.05 0.002 0.0001" mass="0.08"/>
      </body>
    </body>"""


def _item_body_xml(spec: ItemSpec) -> str:
    wx, wy = grid_to_world(spec.cell)
    r, g, b = spec.color
    # Sit on floor / low shelf height
    z = 0.09
    return f"""
    <body name="{spec.item_id}_body" pos="{wx} {wy} {z}">
      <freejoint name="{spec.item_id}_free"/>
      <geom name="{spec.item_id}_box" type="box" size="0.09 0.09 0.08"
            rgba="{r} {g} {b} 1" mass="0.4" friction="0.8 0.005 0.0001"
            contype="0" conaffinity="0"/>
      <geom name="{spec.item_id}_stripe" type="box" size="0.092 0.02 0.082"
            rgba="1 1 1 0.35" mass="0.01" contype="0" conaffinity="0"/>
    </body>"""


def _actuator_xml(spec: RobotSpec) -> str:
    return f"""
    <velocity name="{spec.name}_wheel_L_act" joint="{spec.name}_wheel_L_joint" kv="5" ctrlrange="-15 15"/>
    <velocity name="{spec.name}_wheel_R_act" joint="{spec.name}_wheel_R_joint" kv="5" ctrlrange="-15 15"/>"""


def build_arena_xml(
    robots: list[RobotSpec] | None = None,
    obstacles=None,
    items: list[ItemSpec] | None = None,
    include_grid_lines: bool = True,
) -> str:
    robots = robots if robots is not None else DEFAULT_ROBOTS
    items = items if items is not None else default_items()
    half = arena_half_extent()
    cx = (GRID_SIZE * CELL_SIZE) / 2
    cy = (GRID_SIZE * CELL_SIZE) / 2
    bodies = "\n".join(_robot_body_xml(r) for r in robots)
    item_bodies = "\n".join(_item_body_xml(it) for it in items)
    actuators = "\n".join(_actuator_xml(r) for r in robots)
    return f"""<mujoco model="fleet_warehouse_carriers">
  <compiler angle="degree" autolimits="true"/>
  <option gravity="0 0 -9.81" timestep="0.004" integrator="implicitfast" cone="elliptic"/>
  <default>
    <joint damping="0.1"/>
    <geom friction="1.0 0.005 0.0001"/>
  </default>
  <visual>
    <headlight ambient="0.2 0.21 0.23" diffuse="0.4 0.4 0.42" specular="0.1 0.1 0.1"/>
    <rgba haze="0.08 0.1 0.13 1"/>
    <quality shadowsize="2048"/>
  </visual>
{_assets_xml()}
  <worldbody>
{_lights_xml(cx, cy)}
{_floor_lanes(cx, cy, half)}
{_wall_geoms()}
{_obstacle_geoms(obstacles)}
{_grid_lines() if include_grid_lines else ""}
{bodies}
{item_bodies}
  </worldbody>
  <actuator>{actuators}
  </actuator>
</mujoco>
"""


def robot_names(robots=None) -> list[str]:
    robots = robots if robots is not None else DEFAULT_ROBOTS
    return [r.name for r in robots]
