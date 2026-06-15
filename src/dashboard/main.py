"""BerkeleyDashboard — main entry point."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import threading
import time

import structlog
import uvicorn

from dashboard.config import settings


def _configure_logging() -> None:
    logging.basicConfig(format="%(message)s",
                        level=getattr(logging, settings.log_level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger(__name__)


async def _run() -> None:
    from dashboard.mqtt_bridge import MQTTBridge
    from dashboard.api.server import app, init as init_api

    bridge = MQTTBridge()
    init_api(bridge)
    bridge.start()

    log.info(
        "main.started",
        api_port=settings.dashboard_port,
        internal_url=f"http://{settings.dashboard_host}:{settings.dashboard_port}/internal/",
        public_url=f"http://{settings.dashboard_host}:{settings.dashboard_port}/public/",
    )

    api_config = uvicorn.Config(
        app,
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
    api_server = uvicorn.Server(api_config)
    api_thread = threading.Thread(target=api_server.run, daemon=True, name="api-server")
    api_thread.start()

    stop_event = asyncio.Event()

    def _signal_handler(sig, frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    await stop_event.wait()
    bridge.stop()
    log.info("main.stopped")


def main() -> None:
    _configure_logging()
    from dashboard import __version__
    log.info("main.starting", version=__version__)
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
