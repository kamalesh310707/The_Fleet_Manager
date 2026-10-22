"""
Fleet viewer — 5 carrier AGVs + inventory boxes.

Commands arrive via MQTT *and* via HTTP world-state sync so MuJoCo still
moves even if MQTT is flaky (common cause of "dashboard MOVING but no motion").
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import mujoco
from mujoco import viewer as mj_viewer
import paho.mqtt.client as mqtt

from robots.astar import find_path
from robots.controller import DriveController
from robots.grid_config import scenario_grid, world_to_grid, grid_to_world
from robots.mujoco_arena import build_arena_xml, DEFAULT_ROBOTS, default_items
from shared.schemas import (
    CommandPayload,
    ErrorPayload,
    RobotStatus,
    StatusPayload,
    TaskPhase,
    topic_command,
    topic_error,
    topic_status,
)

CHASSIS_LIFT = 0.22
API_BASE = os.getenv("FLEET_API", "http://127.0.0.1:8000")


@dataclass
class RobotRuntime:
    robot_id: str
    status: RobotStatus = RobotStatus.IDLE
    position: tuple[int, int] = (0, 0)
    path: list = field(default_factory=list)
    path_index: int = 0
    task_id: str | None = None
    controller: DriveController | None = None
    delay_remaining: float = 0.0
    start_delay: int = 0
    phase: TaskPhase = TaskPhase.MOVE
    action: str = "drive"
    carrying_item_id: str | None = None
    pending_action: str | None = None
    item_id_for_action: str | None = None
    last_task_seen: str | None = None  # avoid re-starting same job
    last_http_post_t: float = 0.0
    last_posted_status: RobotStatus = RobotStatus.IDLE
    last_posted_path_idx: int = -1


@dataclass
class ItemRuntime:
    item_id: str
    cell: tuple[int, int]
    carried_by: str | None = None
    visible: bool = True


class FleetSim:
    def __init__(self) -> None:
        self.realtime = float(os.getenv("ROBOT_REALTIME_FACTOR", "1.5"))
        self.host = os.getenv("MQTT_HOST", "localhost")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self.substeps = int(os.getenv("FLEET_SUBSTEPS", "6"))
        self.delay_tick_s = float(os.getenv("FLEET_DELAY_TICK_S", "0.2"))
        self.action_hold_s = float(os.getenv("FLEET_ACTION_HOLD_S", "0.5"))

        self._item_specs = default_items()
        xml = build_arena_xml(robots=DEFAULT_ROBOTS, items=self._item_specs)
        self.model = mujoco.MjModel.from_xml_string(xml)
        self.data = mujoco.MjData(self.model)
        # Stabilize contacts a bit
        self.model.opt.timestep = 0.004
        self.grid = scenario_grid()

        self.robots: dict[str, RobotRuntime] = {}
        self._payload_geom: dict[str, int] = {}
        self._qpos: dict[str, int] = {}
        self._act_L: dict[str, int] = {}
        self._act_R: dict[str, int] = {}

        for spec in DEFAULT_ROBOTS:
            qadr = int(self.model.joint(f"{spec.name}_free").qposadr[0])
            self._qpos[spec.name] = qadr
            gx, gy = world_to_grid((self.data.qpos[qadr], self.data.qpos[qadr + 1]))
            self.robots[spec.name] = RobotRuntime(robot_id=spec.name, position=(gx, gy))
            try:
                self._payload_geom[spec.name] = int(self.model.geom(f"{spec.name}_payload").id)
            except Exception:
                pass
            try:
                self._act_L[spec.name] = int(self.model.actuator(f"{spec.name}_wheel_L_act").id)
                self._act_R[spec.name] = int(self.model.actuator(f"{spec.name}_wheel_R_act").id)
            except Exception as e:
                print(f"actuator lookup failed {spec.name}: {e}")

        self.items: dict[str, ItemRuntime] = {}
        self._item_qpos: dict[str, int] = {}
        for spec in self._item_specs:
            try:
                qadr = int(self.model.joint(f"{spec.item_id}_free").qposadr[0])
                self._item_qpos[spec.item_id] = qadr
                self.items[spec.item_id] = ItemRuntime(item_id=spec.item_id, cell=spec.cell)
            except Exception as e:
                print(f"item skip {spec.item_id}: {e}")

        self._last_status_t = 0.0
        self._status_interval = 0.1
        self._action_timers: dict[str, float] = {}
        self._cmd_lock = threading.Lock()
        self._stop = False
        self._http_status_queue: queue.Queue = queue.Queue(maxsize=100)

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"fleet-viewer-{os.getpid()}"
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=5)

    # ── MQTT ─────────────────────────────────────────────
    def start_mqtt(self) -> None:
        self._poster_thread = threading.Thread(target=self._http_status_poster, daemon=True)
        self._poster_thread.start()
        print(f"[mqtt] connecting {self.host}:{self.port} ...")
        try:
            self.client.connect_async(self.host, self.port, keepalive=30)
            self.client.loop_start()
        except Exception as e:
            print(f"[mqtt] connect error: {e} -- will rely on HTTP sync")
        time.sleep(0.6)
        for rt in self.robots.values():
            self._publish_status(rt)
            print(f"  {rt.robot_id} ready @ {rt.position}")
        print(f"  boxes: {len(self.items)}")
        print(f"[http] syncing paths from {API_BASE}/world-state")

    def stop(self) -> None:
        self._stop = True
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        # paho v2 passes ReasonCode objects — never call int() on them directly
        try:
            rc = int(getattr(reason_code, "value", reason_code))
        except Exception:
            rc = 0 if (reason_code is None or str(reason_code) in ("Success", "0")) else -1
        if rc == 0:
            for rid in self.robots:
                client.subscribe(topic_command(rid), qos=1)
            print(f"[mqtt] OK subscribed {list(self.robots)}")
        else:
            print(f"[mqtt] connect failed reason={reason_code!r} (rc={rc})")

    def _on_message(self, client, userdata, message) -> None:
        parts = message.topic.split("/")
        if len(parts) != 4 or parts[3] != "command":
            return
        rid = parts[2]
        rt = self.robots.get(rid)
        if rt is None:
            return
        try:
            cmd = CommandPayload.model_validate_json(message.payload)
        except ValueError as e:
            print(f"[mqtt] bad payload: {e}")
            return
        print(f"[mqtt] CMD {rid} -> {cmd.target} action={cmd.action} path={len(cmd.path or [])}")
        with self._cmd_lock:
            self._accept_command(rt, cmd)

    # ── HTTP fallback: pull paths from backend world-state ─
    def _http_sync_loop(self) -> None:
        while not self._stop:
            try:
                with urllib.request.urlopen(f"{API_BASE}/world-state", timeout=1.5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                robots = data.get("robots") or {}
                tasks = data.get("task_queue") or []
                task_by_robot = {}
                for task in tasks:
                    aid = task.get("assigned_to")
                    if aid and task.get("status") in ("assigned", "in_progress"):
                        task_by_robot[aid] = task
                with self._cmd_lock:
                    for rid, st in robots.items():
                        rt = self.robots.get(rid)
                        if rt is None:
                            continue
                        status = (st.get("status") or "").lower()
                        path = st.get("path") or []
                        tinfo = task_by_robot.get(rid) or {}
                        task_id = tinfo.get("task_id") or st.get("task_id")
                        target = st.get("target")
                        phase = st.get("phase") or tinfo.get("phase") or "move"
                        carrying = st.get("carrying_item_id") or tinfo.get("item_id")

                        if status in ("moving", "picking", "placing") and path and len(path) >= 1:
                            # Start only if we are idle or this is a new path/target
                            path_t = [tuple(p) for p in path]
                            tgt = tuple(target) if target else path_t[-1]
                            already = (
                                rt.status == RobotStatus.MOVING
                                and rt.path == path_t
                                and rt.controller is not None
                            )
                            if already:
                                continue
                            if rt.status in (
                                RobotStatus.PICKING,
                                RobotStatus.PLACING,
                            ):
                                continue
                            # Dedup: same task already completed locally
                            sig = f"{task_id}:{tgt}:{phase}"
                            if rt.last_task_seen == sig and rt.status == RobotStatus.IDLE:
                                continue
                            # Accept whenever we are not already driving (controller may be
                            # None after a previous finish, or never started due to MQTT loss)
                            if rt.controller is None or rt.path != path_t:
                                # Infer action from phase
                                action = "drive"
                                ph = str(phase).lower()
                                if ph in ("to_pick", "pick"):
                                    action = "pick"
                                elif ph in ("to_place", "place"):
                                    action = "place"
                                cmd = CommandPayload(
                                    target=tgt,  # type: ignore[arg-type]
                                    task_id=task_id or f"http-{rid}-{int(time.time())}",
                                    path=path_t,  # type: ignore[arg-type]
                                    start_delay=int(st.get("start_delay") or 0),
                                    phase=TaskPhase(ph) if ph in TaskPhase._value2member_map_ else TaskPhase.MOVE,
                                    item_id=carrying or st.get("item_id"),
                                    action=action,
                                )
                                print(
                                    f"[http] sync {rid} -> {tgt} action={action} "
                                    f"path={len(path_t)} (backend status={status})"
                                )
                                self._accept_command(rt, cmd)
                                rt.last_task_seen = sig
            except Exception as e:
                # Avoid silent failures during debug
                if int(time.time()) % 10 == 0:
                    print(f"[http] sync error: {e}")
            time.sleep(0.35)

    # ── Status out ───────────────────────────────────────
    def _post_http_status(self, robot_id: str, payload_json: str, is_error: bool = False) -> None:
        try:
            self._http_status_queue.put_nowait((robot_id, payload_json, is_error))
        except queue.Full:
            pass

    def _http_status_poster(self) -> None:
        while not self._stop:
            try:
                item = self._http_status_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            robot_id, payload_json, is_error = item
            endpoint = f"{API_BASE}/robots/{robot_id}/{'error' if is_error else 'status'}"
            try:
                req = urllib.request.Request(
                    endpoint,
                    data=payload_json.encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=1.0):
                    pass
            except Exception:
                pass

    def _publish_status(self, rt: RobotRuntime, action_done: str | None = None) -> None:
        payload = StatusPayload(
            position=rt.position,
            status=rt.status,
            task_id=rt.task_id,
            timestamp=time.time(),
            path_index=rt.path_index,
            phase=rt.phase,
            carrying_item_id=rt.carrying_item_id,
            action_done=action_done,
        )
        json_data = payload.model_dump_json()
        try:
            self.client.publish(topic_status(rt.robot_id), json_data, qos=1)
        except Exception:
            pass
        now = time.time()
        should_post = False
        if action_done:
            should_post = True
        elif rt.status != rt.last_posted_status:
            should_post = True
        elif rt.status == RobotStatus.MOVING and rt.path_index != rt.last_posted_path_idx and (now - rt.last_http_post_t >= 0.3):
            should_post = True
        elif now - rt.last_http_post_t >= 2.0:
            should_post = True

        if should_post:
            rt.last_http_post_t = now
            rt.last_posted_status = rt.status
            rt.last_posted_path_idx = rt.path_index
            self._post_http_status(rt.robot_id, json_data, is_error=False)

    def _publish_error(self, rt: RobotRuntime, error_type: str) -> None:
        rt.status = RobotStatus.ERROR
        err_payload = ErrorPayload(
            error_type=error_type,
            last_position=rt.position,
            timestamp=time.time(),
        )
        err_json = err_payload.model_dump_json()
        try:
            self.client.publish(
                topic_error(rt.robot_id),
                err_json,
                qos=1,
            )
        except Exception:
            pass
        self._post_http_status(rt.robot_id, err_json, is_error=True)
        self._publish_status(rt)

    # ── Visuals ──────────────────────────────────────────
    def _set_carrier_loaded(self, robot_id: str, loaded: bool, color=None) -> None:
        gid = self._payload_geom.get(robot_id)
        if gid is None:
            return
        if loaded:
            self.model.geom_rgba[gid] = color or (0.9, 0.55, 0.15, 1.0)
        else:
            self.model.geom_rgba[gid][3] = 0.0

    def _hide_world_item(self, item_id: str) -> None:
        it = self.items.get(item_id)
        if not it:
            return
        it.visible = False
        qadr = self._item_qpos.get(item_id)
        if qadr is not None:
            self.data.qpos[qadr + 2] = -1.0

    def _show_world_item(self, item_id: str, cell: tuple[int, int]) -> None:
        it = self.items.get(item_id)
        if not it:
            return
        it.visible = True
        it.cell = cell
        it.carried_by = None
        qadr = self._item_qpos.get(item_id)
        if qadr is not None:
            wx, wy = grid_to_world(cell)
            self.data.qpos[qadr] = wx
            self.data.qpos[qadr + 1] = wy
            self.data.qpos[qadr + 2] = 0.09
            self.data.qpos[qadr + 3] = 1.0
            self.data.qpos[qadr + 4 : qadr + 7] = 0.0

    def _attach_item_to_carrier(self, robot_id: str, item_id: str) -> None:
        rq = self._qpos.get(robot_id)
        iq = self._item_qpos.get(item_id)
        if rq is None or iq is None:
            return
        self.data.qpos[iq] = self.data.qpos[rq]
        self.data.qpos[iq + 1] = self.data.qpos[rq + 1]
        self.data.qpos[iq + 2] = self.data.qpos[rq + 2] + CHASSIS_LIFT
        self.data.qpos[iq + 3 : iq + 7] = self.data.qpos[rq + 3 : rq + 7]

    # ── Accept & drive ───────────────────────────────────
    def _accept_command(self, rt: RobotRuntime, cmd: CommandPayload) -> None:
        target = tuple(cmd.target)
        path = [tuple(p) for p in cmd.path] if cmd.path else []
        if not path:
            path = find_path(self.grid, rt.position, target)
        if not path:
            print(f"  !! no path {rt.position} -> {target}")
            self._publish_error(rt, "unreachable_target")
            return

        # If already driving identical path, ignore
        if (
            rt.controller is not None
            and rt.path == path
            and rt.status == RobotStatus.MOVING
        ):
            return

        rt.path = path
        rt.path_index = 0
        rt.task_id = cmd.task_id
        rt.status = RobotStatus.MOVING
        try:
            rt.phase = cmd.phase if isinstance(cmd.phase, TaskPhase) else TaskPhase(str(cmd.phase))
        except Exception:
            rt.phase = TaskPhase.MOVE
        rt.action = cmd.action or "drive"
        rt.item_id_for_action = cmd.item_id
        rt.start_delay = max(0, int(cmd.start_delay or 0))
        rt.delay_remaining = float(rt.start_delay)
        rt.controller = None
        rt.pending_action = None

        if cmd.item_id and rt.action == "place":
            rt.carrying_item_id = cmd.item_id
            self._set_carrier_loaded(rt.robot_id, True)

        # Start immediately if no delay
        if rt.start_delay <= 0:
            self._activate_controller(rt)

        self._publish_status(rt)
        print(
            f"  -> {rt.robot_id} DRIVE path={len(path)} start={rt.position} "
            f"goal={target} action={rt.action}"
        )

    def _activate_controller(self, rt: RobotRuntime) -> None:
        if not rt.path:
            return
        rt.controller = DriveController(self.model, self.data, rt.robot_id, rt.path)
        print(f"  -> {rt.robot_id} controller ON ({len(rt.path)} waypoints)")

    def _read_position(self, robot_id: str) -> tuple[int, int]:
        qadr = self._qpos[robot_id]
        return world_to_grid((float(self.data.qpos[qadr]), float(self.data.qpos[qadr + 1])))

    def _estimate_path_index(self, rt: RobotRuntime) -> int:
        if not rt.path:
            return 0
        best_i, best_d = 0, float("inf")
        for i, cell in enumerate(rt.path):
            d = abs(cell[0] - rt.position[0]) + abs(cell[1] - rt.position[1])
            if d < best_d:
                best_d, best_i = d, i
        return best_i

    def _zero_wheels(self, robot_id: str) -> None:
        if robot_id in self._act_L:
            self.data.ctrl[self._act_L[robot_id]] = 0.0
            self.data.ctrl[self._act_R[robot_id]] = 0.0

    def _finish_path(self, rt: RobotRuntime) -> None:
        rt.position = self._read_position(rt.robot_id)
        rt.path_index = max(0, len(rt.path) - 1)
        rt.controller = None
        self._zero_wheels(rt.robot_id)

        if rt.action == "pick":
            rt.status = RobotStatus.PICKING
            rt.pending_action = "pick"
            self._action_timers[rt.robot_id] = self.action_hold_s
            self._publish_status(rt)
            print(f"  -> {rt.robot_id} at pick cell {rt.position}")
        elif rt.action == "place":
            rt.status = RobotStatus.PLACING
            rt.pending_action = "place"
            self._action_timers[rt.robot_id] = self.action_hold_s
            self._publish_status(rt)
            print(f"  -> {rt.robot_id} at place cell {rt.position}")
        else:
            rt.status = RobotStatus.IDLE
            self._publish_status(rt)
            print(f"  -> {rt.robot_id} ARRIVED {rt.position}")
            rt.task_id = None
            rt.path = []
            rt.path_index = 0
            rt.action = "drive"

    def _complete_action(self, rt: RobotRuntime) -> None:
        act = rt.pending_action
        rt.pending_action = None
        if act == "pick":
            iid = rt.item_id_for_action or rt.carrying_item_id
            if not iid:
                # nearest on-shelf item cell match
                for item_id, it in self.items.items():
                    if it.cell == rt.position and it.visible:
                        iid = item_id
                        break
            rt.carrying_item_id = iid
            if iid:
                self._hide_world_item(iid)
                it = self.items.get(iid)
                if it:
                    it.carried_by = rt.robot_id
            self._set_carrier_loaded(rt.robot_id, True)
            rt.status = RobotStatus.IDLE
            self._publish_status(rt, action_done="pick")
            print(f"  -> {rt.robot_id} PICKED {iid}")
        elif act == "place":
            iid = rt.carrying_item_id or rt.item_id_for_action
            if iid:
                self._show_world_item(iid, rt.position)
            self._set_carrier_loaded(rt.robot_id, False)
            rt.carrying_item_id = None
            rt.status = RobotStatus.IDLE
            self._publish_status(rt, action_done="place")
            print(f"  -> {rt.robot_id} PLACED {iid} @ {rt.position}")
            rt.task_id = None
            rt.path = []
            rt.path_index = 0
            rt.action = "drive"
            rt.item_id_for_action = None
        else:
            rt.status = RobotStatus.IDLE
            self._publish_status(rt)

    def tick(self, dt_wall: float) -> None:
        with self._cmd_lock:
            for rt in self.robots.values():
                if rt.status != RobotStatus.MOVING:
                    continue
                if rt.controller is None and rt.path:
                    if rt.delay_remaining > 0:
                        rt.delay_remaining -= dt_wall / max(self.delay_tick_s, 1e-3)
                        if rt.delay_remaining > 0:
                            continue
                        rt.delay_remaining = 0.0
                    self._activate_controller(rt)

            finished: list[str] = []
            for rt in self.robots.values():
                if rt.controller is None:
                    continue
                done = rt.controller.step()
                rt.position = self._read_position(rt.robot_id)
                rt.path_index = self._estimate_path_index(rt)
                if done:
                    finished.append(rt.robot_id)

        for _ in range(self.substeps):
            mujoco.mj_step(self.model, self.data)

        for rt in self.robots.values():
            if rt.carrying_item_id:
                self._attach_item_to_carrier(rt.robot_id, rt.carrying_item_id)

        with self._cmd_lock:
            for rid in finished:
                self._finish_path(self.robots[rid])

            for rid, rem in list(self._action_timers.items()):
                rem -= dt_wall
                if rem <= 0:
                    del self._action_timers[rid]
                    self._complete_action(self.robots[rid])
                else:
                    self._action_timers[rid] = rem

        now = time.time()
        if now - self._last_status_t >= self._status_interval:
            self._last_status_t = now
            for rt in self.robots.values():
                rt.position = self._read_position(rt.robot_id)
                if rt.status == RobotStatus.MOVING:
                    rt.path_index = self._estimate_path_index(rt)
                self._publish_status(rt)

    def run(self) -> None:
        self.start_mqtt()
        sync_thread = threading.Thread(target=self._http_sync_loop, daemon=True)
        sync_thread.start()
        print(
            f"[ready] {len(self.robots)} carriers | {len(self.items)} boxes | "
            f"MQTT+HTTP sync | realtime={self.realtime}x"
        )
        print("[ready] Dispatch from dashboard — robots should move in this window.")
        try:
            with mj_viewer.launch_passive(self.model, self.data) as viewer:
                last = time.time()
                while viewer.is_running():
                    now = time.time()
                    dt = max(1e-4, now - last)
                    last = now
                    self.tick(dt)
                    viewer.sync()
                    target = self.model.opt.timestep * self.substeps
                    if self.realtime > 0:
                        sleep_for = max(0.0, (target / self.realtime) - (time.time() - now))
                        if sleep_for > 0:
                            time.sleep(sleep_for)
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"[viewer] {e} — headless mode")
            try:
                last = time.time()
                while not self._stop:
                    now = time.time()
                    dt = max(1e-4, now - last)
                    last = now
                    self.tick(dt)
                    target = self.model.opt.timestep * self.substeps
                    if self.realtime > 0:
                        sleep_for = max(0.0, (target / self.realtime) - (time.time() - now))
                        if sleep_for > 0:
                            time.sleep(sleep_for)
            except KeyboardInterrupt:
                pass
        finally:
            self.stop()
            print("[stop] fleet viewer exited")


def main() -> None:
    try:
        FleetSim().run()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()

