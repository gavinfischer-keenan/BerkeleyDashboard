"""FastAPI server — serves both /internal/* and /public/* from one app.

Nginx enforces the boundary:
  - home.mosswood.internal → all routes (LAN only)
  - mosswood.science       → only /public/* and /api/public/* (internet)
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from pathlib import Path
from typing import Any

import httpx
import structlog
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

log = structlog.get_logger(__name__)

app = FastAPI(title="Berkeley Dashboard", version="0.1.0", docs_url="/api/docs")

# Injected at startup
_bridge = None
_start_time = time.time()

# WebSocket client sets
_ws_internal: set[WebSocket] = set()
_ws_public: set[WebSocket] = set()

_static = Path(__file__).parent.parent / "static"


def init(bridge) -> None:
    global _bridge
    _bridge = bridge
    from dashboard.mqtt_bridge import add_ws_listener
    add_ws_listener(_on_mqtt_event)


def _on_mqtt_event(topic: str, payload: dict) -> None:
    """Relay MQTT events to connected WebSocket clients."""
    # Determine if this topic is public-safe
    public_safe = _is_public_topic(topic)
    data = json.dumps({"topic": topic, "payload": payload})

    for ws_set, condition in [(_ws_internal, True), (_ws_public, public_safe)]:
        dead: set[WebSocket] = set()
        for ws in list(ws_set):
            try:
                asyncio.get_event_loop().call_soon_threadsafe(
                    asyncio.ensure_future, ws.send_text(data)
                )
            except Exception:
                dead.add(ws)
        ws_set.difference_update(dead)


def _is_public_topic(topic: str) -> bool:
    """Return True if this topic is safe to relay to the public-facing WebSocket."""
    PUBLIC_PREFIXES = (
        "home/alerts/earthquake",
        "home/sensors/seismic",
        "home/status/earthquake-engine",
        "home/events/bird-audio",
        "home/events/bat-audio",
        "home/sensors/environmental",
        "home/sensors/air",
        "home/status/environmental-station",
        "home/status/audio-receiver",
    )
    return any(topic.startswith(p) for p in PUBLIC_PREFIXES)


# ── Static files ──────────────────────────────────────────────────────────────
app.mount("/internal/static", StaticFiles(directory=str(_static / "internal")), name="internal-static")
app.mount("/public/static", StaticFiles(directory=str(_static / "public")), name="public-static")


# ── Page routes ───────────────────────────────────────────────────────────────
@app.get("/internal/", response_class=HTMLResponse)
@app.get("/internal", response_class=HTMLResponse)
async def internal_index():
    return (_static / "internal" / "index.html").read_text()


@app.get("/public/", response_class=HTMLResponse)
@app.get("/public", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
async def public_index():
    return (_static / "public" / "index.html").read_text()


# ── Internal API ──────────────────────────────────────────────────────────────
@app.get("/api/internal/state")
async def internal_state():
    """Full live state — only served on LAN-accessible routes."""
    from dashboard.mqtt_bridge import state
    return {
        "seismic": {
            "rsam": state["seismic_rsam"],
            "status": state["seismic_status"],
            "recent_alerts": list(state["eq_alerts"]),
        },
        "power": state["power"],
        "climate": state["climate"],
        "occupancy": state["occupancy"],
        "soil": state["soil"],
        "agent_status": state["agent_status"],
        "uptime_sec": round(time.time() - _start_time),
    }


@app.get("/api/internal/alarms")
async def internal_alarms():
    """Proxy to BerkeleyAlarms API."""
    from dashboard.config import settings
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{settings.alarms_api_url}/api/alarms")
            return r.json()
    except Exception as e:
        return {"error": str(e), "alarms": []}


@app.get("/api/internal/messages")
async def internal_messages():
    """Proxy to BerkeleyMessages API — unread inbox."""
    from dashboard.config import settings
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{settings.messages_api_url}/api/messages")
            return r.json()
    except Exception as e:
        return {"error": str(e), "messages": []}


@app.get("/api/internal/birds")
async def internal_birds():
    from dashboard.mqtt_bridge import state
    return {"sightings": list(state["bird_sightings"])}


@app.get("/api/internal/events")
async def internal_events(limit: int = 50):
    from dashboard.mqtt_bridge import state
    return {"events": list(state["event_log"])[:limit]}


# ── Public API ────────────────────────────────────────────────────────────────
@app.get("/api/public/seismic")
async def public_seismic():
    from dashboard.mqtt_bridge import state
    return {
        "rsam": state["seismic_rsam"],
        "status": state["seismic_status"],
        "recent_alerts": list(state["eq_alerts"])[:5],
    }


@app.get("/api/public/environment")
async def public_environment():
    from dashboard.mqtt_bridge import state
    return {
        "readings": state["env_readings"],
        "air_quality": state["air_quality"],
    }


@app.get("/api/public/birds")
async def public_birds():
    from dashboard.mqtt_bridge import state
    # Strip location metadata — species + time only for public feed
    public_sightings = [
        {
            "species": s.get("species"),
            "common_name": s.get("common_name"),
            "confidence": s.get("confidence"),
            "timestamp": s.get("timestamp"),
            "analyzer": s.get("analyzer"),
        }
        for s in state["bird_sightings"]
    ]
    return {"sightings": public_sightings}


@app.get("/api/public/alerts")
async def public_alerts():
    """Only EQ alerts are public-safe."""
    from dashboard.mqtt_bridge import state
    return {"eq_alerts": list(state["eq_alerts"])[:10]}


@app.get("/api/status")
async def api_status():
    from dashboard.mqtt_bridge import state
    return {
        "service": "berkeley-dashboard",
        "uptime_sec": round(time.time() - _start_time),
        "agents": state["agent_status"],
    }


# ── WebSockets ────────────────────────────────────────────────────────────────
@app.websocket("/ws/internal")
async def ws_internal(ws: WebSocket):
    await ws.accept()
    _ws_internal.add(ws)
    from dashboard.mqtt_bridge import state
    # Send current state snapshot on connect
    await ws.send_text(json.dumps({"event": "snapshot", "data": {
        "eq_alerts": list(state["eq_alerts"])[:5],
        "env_alerts": list(state["env_alerts"])[:5],
        "agent_status": state["agent_status"],
    }}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _ws_internal.discard(ws)


@app.websocket("/ws/public")
async def ws_public(ws: WebSocket):
    await ws.accept()
    _ws_public.add(ws)
    from dashboard.mqtt_bridge import state
    await ws.send_text(json.dumps({"event": "snapshot", "data": {
        "eq_alerts": list(state["eq_alerts"])[:5],
        "rsam": state["seismic_rsam"],
    }}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _ws_public.discard(ws)
