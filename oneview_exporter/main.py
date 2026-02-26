"""Entrypoint for the HPE OneView Prometheus exporter."""

from __future__ import annotations

import logging
import os
import signal
import sys

from prometheus_client import REGISTRY, start_http_server

from .collector import OneViewCollector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        logger.error("Required environment variable %s is not set", name)
        sys.exit(1)
    return val  # type: ignore[return-value]


def _build_client(dry_run: bool):
    if dry_run:
        from .fake import FakeOneViewClient
        logger.info("Running in --dry-run mode with simulated data")
        return FakeOneViewClient()

    from .client import OneViewClient

    host = _env("OV_HOST", required=True)
    username = _env("OV_USERNAME", required=True)
    password = _env("OV_PASSWORD", required=True)
    login_domain = _env("OV_LOGIN_DOMAIN")
    api_version = int(_env("OV_API_VERSION", "7200"))

    tls_ca = _env("TLS_CA_BUNDLE")
    tls_insecure = _env("TLS_INSECURE", "false").lower() == "true"
    if tls_insecure:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        ca_bundle: str | bool = False
    elif tls_ca:
        ca_bundle = tls_ca
    else:
        ca_bundle = True

    return OneViewClient(
        host=host,
        username=username,
        password=password,
        login_domain=login_domain or None,
        api_version=api_version,
        ca_bundle=ca_bundle,
    )


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    scrape_interval = int(_env("OV_SCRAPE_INTERVAL", "60"))
    port = int(_env("EXPORTER_PORT", "9130"))

    client = _build_client(dry_run)

    collector = OneViewCollector(client, scrape_interval=scrape_interval)
    REGISTRY.register(collector)
    collector.start()

    start_http_server(port)
    logger.info("Exporter listening on http://0.0.0.0:%d/metrics", port)

    stop = signal.Event() if hasattr(signal, "Event") else _make_stop_event()
    stop.wait()


def _make_stop_event():
    """Create a threading.Event that gets set on SIGINT/SIGTERM."""
    import threading
    evt = threading.Event()

    def _handler(signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        evt.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
    return evt


if __name__ == "__main__":
    main()
