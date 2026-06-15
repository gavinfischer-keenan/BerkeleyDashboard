"""Dashboard service configuration."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False
    )

    # ── MQTT ────────────────────────────────────────────────────────────
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_client_id: str = "berkeley-dashboard"

    # ── API ports ────────────────────────────────────────────────────────
    dashboard_port: int = 8090
    dashboard_host: str = "0.0.0.0"

    # ── Upstream service URLs (running on same host) ────────────────────
    alarms_api_url: str = "http://localhost:8084"
    messages_api_url: str = "http://localhost:8085"
    homesensors_api_url: str = "http://localhost:8082"

    # ── Feature flags ────────────────────────────────────────────────────
    # Set to "true" to serve only the public-safe paths (for DMZ deploy)
    public_only_mode: bool = False

    # ── Logging ─────────────────────────────────────────────────────────
    log_level: str = "INFO"


settings = Settings()
