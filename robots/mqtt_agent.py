"""MQTT command adapter for one MuJoCo-backed RobotAgent process."""
from __future__ import annotations

import os
import time

import paho.mqtt.client as mqtt

from robots.agent import RobotAgent
from robots.astar import find_path
from shared.schemas import (
    CommandPayload,
    ErrorPayload,
    RobotStatus,
    StatusPayload,
    topic_command,
    topic_error,
    topic_status,
)


class MqttRobotAgent:
    def __init__(self, agent: RobotAgent) -> None:
        self.agent = agent
        self.host = os.getenv("MQTT_HOST", "localhost")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self.realtime_factor = float(os.getenv("ROBOT_REALTIME_FACTOR", "0.5"))
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"agent-{agent.robot_id}"
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._active_task_id: str | None = None
        self._active_path: list = []

    def start(self) -> None:
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()
        self.publish_status()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        # paho-mqtt v2 passes ReasonCode objects — never compare with == 0 directly
        try:
            rc = int(getattr(reason_code, "value", reason_code))
        except Exception:
            rc = 0 if (reason_code is None or str(reason_code) in ("Success", "0")) else -1
        if rc == 0:
            client.subscribe(topic_command(self.agent.robot_id), qos=1)
            print(f"[mqtt] {self.agent.robot_id} subscribed to commands")
        else:
            print(f"[mqtt] {self.agent.robot_id} connect failed reason={reason_code!r}")

    def _on_message(self, client, userdata, message) -> None:
        try:
            command = CommandPayload.model_validate_json(message.payload)
        except ValueError:
            return

        path = command.path if command.path else find_path(
            self.agent.grid, self.agent.fsm.position, command.target
        )
        if not path:
            self.publish_error("unreachable_target")
            return

        self._active_task_id = command.task_id
        self._active_path = list(path)
        self.agent.fsm.receive_command(command.target, path)
        self.publish_status(command.task_id, path_index=0)

        if command.start_delay > 0:
            cell_time = 0.6 / max(self.realtime_factor, 0.1)
            for _ in range(command.start_delay):
                time.sleep(cell_time)
                self.publish_status(command.task_id, path_index=0)

        def on_progress(position, path_index: int = 0):
            self.publish_status(command.task_id, position, path_index=path_index)

        success = self.agent.drive_to(
            command.target,
            path=path,
            progress_callback=on_progress,
            realtime_factor=self.realtime_factor if self.realtime_factor > 0 else None,
        )
        if success:
            self.agent.fsm.status = RobotStatus.IDLE
            self.publish_status(command.task_id, path_index=max(0, len(path) - 1))
        else:
            self.publish_error("drive_timeout")
        self._active_task_id = None
        self._active_path = []

    def publish_status(self, task_id: str | None = None, position=None, path_index: int = 0) -> None:
        pos = position if position is not None else self.agent.fsm.position
        payload = StatusPayload(
            position=pos,
            status=self.agent.fsm.status,
            task_id=task_id if task_id is not None else self._active_task_id,
            timestamp=time.time(),
            path_index=path_index,
        )
        self.client.publish(
            topic_status(self.agent.robot_id),
            payload.model_dump_json(),
            qos=1,
        )

    def publish_error(self, error_type: str) -> None:
        self.agent.fsm.status = RobotStatus.ERROR
        payload = ErrorPayload(
            error_type=error_type,
            last_position=self.agent.fsm.position,
            timestamp=time.time(),
        )
        self.client.publish(
            topic_error(self.agent.robot_id),
            payload.model_dump_json(),
            qos=1,
        )
        self.publish_status()
