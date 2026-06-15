"""MQTT bridge — subscribes to all home/# topics and maintains live state for the dashboard."""
from __future__ import annotations

import json
import threading
from collections import defaultdict, deque
from typing import Any

import paho.mqtt.client as mqtt
import structlog

from dashboard.config import settings

log = structlog.get_logger(__name__)

# ── Live state stores ──────────────────────────────────────────────────────────
# These are written by the MQTT thread and read by the API/WebSocket layer.

state: dict[str, Any] = {
    # Seismic
    "seismic_rsam": None,       # latest RSAM reading
    "seismic_status": None,     # engine status
    "eq_alerts": deque(maxlen=20),  # recent EQ alerts

    # Environmental
    "env_readings": {},         # sensor_id → latest reading
    "env_alerts": deque(maxlen=20),

    # Air quality (AirGradient)
    "air_quality": {},          # sensor_id → {co2_ppm, pm25, temp, humidity}

    # Power
    "power": {},                # circuit_id → {watts, voltage, circuit_name}
    "power_alerts": deque(maxlen=20),

    # Sensors / rooms
    "climate": {},              # room → {temp_f, humidity}
    "occupancy": {},            # room → {occupied: bool}
    "soil": {},                 # zone_id → {moisture_pct, location}

    # BirdNET
    "bird_sightings": deque(maxlen=50),  # today's detections

    # Alarms (live - fetched from BerkeleyAlarms API)
    "active_alarms": [],

    # Agent health
    "agent_status": {},         # agent_name → {status, uptime_s, timestamp}

    # Raw event log (all topics, rolling)
    "event_log": deque(maxlen=200),
}

# WebSocket listeners — callables that receive (topic, payload) on new MQTT messages
_ws_listeners: list[Any] = []


def add_ws_listener(fn) -> None:
    _ws_listeners.append(fn)


def _notify_ws(topic: str, payload: dict) -> None:
    for fn in list(_ws_listeners):
        try:
            fn(topic, payload)
        except Exception:
            pass


# ── MQTT client ───────────────────────────────────────────────────────────────

class MQTTBridge:
    """Subscribes to all home/# topics and updates the shared state dict."""

    def __init__(self) -> None:
        self._client = mqtt.Client(
            client_id=settings.mqtt_client_id,
            protocol=mqtt.MQTTv311,
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

    def start(self) -> None:
        self._client.connect(settings.mqtt_broker, settings.mqtt_port, keepalive=60)
        self._client.loop_start()
        log.info("mqtt_bridge.started", broker=settings.mqtt_broker)

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc == 0:
            client.subscribe("home/#", qos=0)
            log.info("mqtt_bridge.connected", subscribed="home/#")
        else:
            log.error("mqtt_bridge.connect_failed", rc=rc)

    def _on_disconnect(self, client, userdata, rc) -> None:
        if rc != 0:
            log.warning("mqtt_bridge.disconnected", rc=rc)

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        # Append to raw event log
        state["event_log"].appendleft({"topic": topic, "payload": payload})

        # Route to state updaters
        try:
            self._route(topic, payload)
        except Exception:
            log.exception("mqtt_bridge.route_error", topic=topic)

        _notify_ws(topic, payload)

    def _route(self, topic: str, payload: dict) -> None:
        parts = topic.split("/")

        # home/status/{agent}
        if len(parts) >= 3 and parts[1] == "status":
            agent = parts[2]
            state["agent_status"][agent] = payload

        # home/sensors/seismic — RSAM
        elif topic.startswith("home/sensors/seismic"):
            state["seismic_rsam"] = payload

        # home/alerts/earthquake
        elif topic == "home/alerts/earthquake":
            state["eq_alerts"].appendleft(payload)

        # home/alerts/power/{circuit_id}
        elif len(parts) >= 3 and parts[1] == "alerts" and parts[2] == "power":
            state["power_alerts"].appendleft(payload)

        # home/alerts/fire-weather, air-quality, etc
        elif len(parts) >= 3 and parts[1] == "alerts":
            state["env_alerts"].appendleft({"alert_type": parts[2], **payload})

        # home/sensors/house/{room}/climate
        elif topic.startswith("home/sensors/house") and "climate" in topic:
            room = parts[3] if len(parts) > 3 else "unknown"
            state["climate"][room] = payload

        # home/sensors/house/{room}/occupancy
        elif topic.startswith("home/sensors/house") and "occupancy" in topic:
            room = parts[3] if len(parts) > 3 else "unknown"
            state["occupancy"][room] = payload

        # home/sensors/house/{zone}/soil
        elif topic.startswith("home/sensors/house") and "soil" in topic:
            zone = parts[3] if len(parts) > 3 else "unknown"
            state["soil"][zone] = payload

        # home/sensors/power/{circuit_id}
        elif topic.startswith("home/sensors/power"):
            circuit_id = parts[3] if len(parts) > 3 else "unknown"
            state["power"][circuit_id] = payload

        # home/events/bird-audio
        elif topic == "home/events/bird-audio":
            state["bird_sightings"].appendleft(payload)

        # home/sensors/environmental-station
        elif topic.startswith("home/sensors/environmental"):
            sensor_id = parts[3] if len(parts) > 3 else "env"
            state["env_readings"][sensor_id] = payload

        # home/sensors/air/{sensor_id}
        elif topic.startswith("home/sensors/air"):
            sensor_id = parts[3] if len(parts) > 3 else "air"
            state["air_quality"][sensor_id] = payload
