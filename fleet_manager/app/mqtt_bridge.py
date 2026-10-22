"""MQTT adapter for the Fleet Manager's external robot boundary."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable

import paho.mqtt.client as mqtt

from shared.schemas import CommandPayload, DispatchBroadcast, ErrorPayload, StatusPayload, TOPIC_ALL_ERROR, TOPIC_ALL_STATUS, TOPIC_DISPATCH_BROADCAST, topic_command

logger = logging.getLogger(__name__)
StatusHandler = Callable[[str, StatusPayload], Awaitable[None]]
ErrorHandler = Callable[[str, ErrorPayload], Awaitable[None]]


class MqttBridge:
    """Translate MQTT callbacks into safe asyncio callbacks for FastAPI."""

    def __init__(self, on_status: StatusHandler, on_error: ErrorHandler) -> None:
        self.host = os.getenv("MQTT_HOST", "localhost")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self._on_status, self._on_error = on_status, on_error
        self._loop: asyncio.AbstractEventLoop | None = None
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fleet-manager")
        self.client.on_connect = self._handle_connect
        self.client.on_message = self._handle_message

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def publish_command(self, robot_id: str, payload: CommandPayload) -> None:
        self.client.publish(topic_command(robot_id), payload.model_dump_json(), qos=1)

    def publish_dispatch(self, payload: DispatchBroadcast) -> None:
        self.client.publish(TOPIC_DISPATCH_BROADCAST, payload.model_dump_json(), qos=1)

    def _handle_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        try:
            rc = int(getattr(reason_code, "value", reason_code))
        except Exception:
            rc = 0 if str(reason_code) in ("Success", "0") else -1
        if rc == 0:
            client.subscribe([(TOPIC_ALL_STATUS, 1), (TOPIC_ALL_ERROR, 1)])
            logger.info("Connected to MQTT broker at %s:%s", self.host, self.port)
        else:
            logger.warning("MQTT connect failed: %r", reason_code)

    def _handle_message(self, client, userdata, message) -> None:
        parts = message.topic.split("/")
        if len(parts) != 4:
            return
        try:
            if parts[3] == "status":
                self._schedule(self._on_status(parts[2], StatusPayload.model_validate_json(message.payload)))
            elif parts[3] == "error":
                self._schedule(self._on_error(parts[2], ErrorPayload.model_validate_json(message.payload)))
        except (ValueError, json.JSONDecodeError) as error:
            logger.warning("Ignoring malformed MQTT message on %s: %s", message.topic, error)

    def _schedule(self, coroutine: Awaitable[None]) -> None:
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coroutine, self._loop)
