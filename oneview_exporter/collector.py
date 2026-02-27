"""Prometheus custom collector backed by a background-polling cache."""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from typing import Any

from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily

from .client import OneViewClient

logger = logging.getLogger(__name__)

STATUS_MAP = {"Unknown": 0, "OK": 1, "Warning": 2, "Critical": 3, "Disabled": 4}
POWER_STATE_MAP = {"Unknown": 0, "On": 1, "Off": 2}
SEVERITY_LEVELS = ("Critical", "Warning", "Info", "Unknown")


def _latest_sample(metric_list: list[dict], metric_name: str) -> float | None:
    """Extract the most recent sample value from a utilization metricList entry."""
    for m in metric_list:
        if m.get("metricName") == metric_name:
            samples = m.get("metricSamples", [])
            if samples:
                return float(samples[0][1])
    return None


class CachedData:
    """Thread-safe container for the latest poll results."""

    def __init__(self) -> None:
        self.servers: list[dict] = []
        self.server_utilizations: dict[str, dict] = {}
        self.enclosures: list[dict] = []
        self.enclosure_utilizations: dict[str, dict] = {}
        self.interconnects: list[dict] = []
        self.active_alerts: list[dict] = []
        self.scrape_duration: float = 0.0
        self.scrape_success: bool = False
        self.api_request_counts: Counter = Counter()
        self.lock = threading.Lock()


class OneViewCollector:
    """Prometheus collector that serves cached OneView data."""

    def __init__(self, client: OneViewClient, scrape_interval: int = 60) -> None:
        self._client = client
        self._scrape_interval = scrape_interval
        self._cache = CachedData()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=10)

    # ------------------------------------------------------------------
    # Background polling
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            self._poll_once()
            self._stop_event.wait(self._scrape_interval)

    def _poll_once(self) -> None:
        t0 = time.monotonic()
        success = True
        try:
            servers = self._safe_call("server-hardware", self._client.get_server_hardware)
            server_utils: dict[str, dict] = {}
            for srv in servers:
                uri = srv.get("uri", "")
                if uri and srv.get("powerState") == "On":
                    util = self._safe_call(
                        f"server-utilization({uri})",
                        self._client.get_server_utilization,
                        uri,
                    )
                    if util:
                        server_utils[uri] = util

            enclosures = self._safe_call("enclosures", self._client.get_enclosures)
            enc_utils: dict[str, dict] = {}
            for enc in enclosures:
                uri = enc.get("uri", "")
                if uri:
                    util = self._safe_call(
                        f"enclosure-utilization({uri})",
                        self._client.get_enclosure_utilization,
                        uri,
                    )
                    if util:
                        enc_utils[uri] = util

            interconnects = self._safe_call("interconnects", self._client.get_interconnects)
            active_alerts = self._safe_call("alerts", self._client.get_active_alerts)

            with self._cache.lock:
                self._cache.servers = servers
                self._cache.server_utilizations = server_utils
                self._cache.enclosures = enclosures
                self._cache.enclosure_utilizations = enc_utils
                self._cache.interconnects = interconnects
                self._cache.active_alerts = active_alerts

        except Exception as exc:
            logger.error("Poll cycle failed — %s", exc)
            logger.debug("Poll cycle failed", exc_info=True)
            success = False

        elapsed = time.monotonic() - t0
        with self._cache.lock:
            self._cache.scrape_duration = elapsed
            self._cache.scrape_success = success
        logger.info("Poll completed in %.2fs (success=%s)", elapsed, success)

    def _safe_call(self, label: str, fn, *args) -> Any:
        try:
            result = fn(*args)
            with self._cache.lock:
                self._cache.api_request_counts[(label, "ok")] += 1
            return result
        except Exception as exc:
            # One-liner at ERROR; full traceback only at DEBUG
            logger.error("API call failed: %s — %s", label, exc)
            logger.debug("API call failed: %s", label, exc_info=True)
            with self._cache.lock:
                self._cache.api_request_counts[(label, "error")] += 1
            return [] if "utilization" not in label else {}

    # ------------------------------------------------------------------
    # Prometheus Collector interface
    # ------------------------------------------------------------------

    def describe(self):
        return []

    def collect(self):
        with self._cache.lock:
            servers = list(self._cache.servers)
            server_utils = dict(self._cache.server_utilizations)
            enclosures = list(self._cache.enclosures)
            enc_utils = dict(self._cache.enclosure_utilizations)
            interconnects = list(self._cache.interconnects)
            active_alerts = list(self._cache.active_alerts)
            scrape_duration = self._cache.scrape_duration
            scrape_success = self._cache.scrape_success

        # --- Server hardware ---
        yield from self._collect_servers(servers, server_utils)

        # --- Enclosures ---
        yield from self._collect_enclosures(enclosures, enc_utils)

        # --- Interconnects ---
        yield from self._collect_interconnects(interconnects)

        # --- Alerts ---
        yield from self._collect_alerts(active_alerts)

        # --- Exporter self-metrics ---
        g = GaugeMetricFamily("oneview_scrape_duration_seconds", "Time spent polling OneView")
        g.add_metric([], scrape_duration)
        yield g

        g = GaugeMetricFamily("oneview_scrape_success", "Whether the last poll succeeded (1=yes, 0=no)")
        g.add_metric([], 1.0 if scrape_success else 0.0)
        yield g

    # ------------------------------------------------------------------
    # Per-resource collectors
    # ------------------------------------------------------------------

    def _collect_servers(self, servers, server_utils):
        status_fam = GaugeMetricFamily(
            "oneview_server_hardware_status",
            "Server hardware status (0=Unknown,1=OK,2=Warning,3=Critical,4=Disabled)",
            labels=["name", "model", "serial_number", "uri"],
        )
        power_fam = GaugeMetricFamily(
            "oneview_server_hardware_power_state",
            "Server power state (0=Unknown,1=On,2=Off)",
            labels=["name", "model"],
        )
        memory_fam = GaugeMetricFamily(
            "oneview_server_hardware_memory_mb",
            "Server installed memory in MB",
            labels=["name", "model"],
        )
        cpu_count_fam = GaugeMetricFamily(
            "oneview_server_hardware_processor_count",
            "Server processor count",
            labels=["name", "model"],
        )
        temp_fam = GaugeMetricFamily(
            "oneview_server_hardware_ambient_temperature_celsius",
            "Server ambient temperature in Celsius",
            labels=["name"],
        )
        cpu_util_fam = GaugeMetricFamily(
            "oneview_server_hardware_cpu_utilization_percent",
            "Server CPU utilization percentage",
            labels=["name"],
        )
        avg_power_fam = GaugeMetricFamily(
            "oneview_server_hardware_average_power_watts",
            "Server average power consumption in watts",
            labels=["name"],
        )
        peak_power_fam = GaugeMetricFamily(
            "oneview_server_hardware_peak_power_watts",
            "Server peak power consumption in watts",
            labels=["name"],
        )

        for srv in servers:
            name = srv.get("name", "unknown")
            model = srv.get("model", "unknown")
            serial = srv.get("serialNumber", "unknown")
            uri = srv.get("uri", "")

            status_fam.add_metric(
                [name, model, serial, uri],
                STATUS_MAP.get(srv.get("status", "Unknown"), 0),
            )
            power_fam.add_metric(
                [name, model],
                POWER_STATE_MAP.get(srv.get("powerState", "Unknown"), 0),
            )
            memory_fam.add_metric([name, model], srv.get("memoryMb", 0))
            cpu_count_fam.add_metric([name, model], srv.get("processorCount", 0))

            util = server_utils.get(uri)
            if util:
                ml = util.get("metricList", [])
                val = _latest_sample(ml, "AmbientTemperature")
                if val is not None:
                    temp_fam.add_metric([name], val)
                val = _latest_sample(ml, "CpuUtilization")
                if val is not None:
                    cpu_util_fam.add_metric([name], val)
                val = _latest_sample(ml, "AveragePower")
                if val is not None:
                    avg_power_fam.add_metric([name], val)
                val = _latest_sample(ml, "PeakPower")
                if val is not None:
                    peak_power_fam.add_metric([name], val)

        yield status_fam
        yield power_fam
        yield memory_fam
        yield cpu_count_fam
        yield temp_fam
        yield cpu_util_fam
        yield avg_power_fam
        yield peak_power_fam

    def _collect_enclosures(self, enclosures, enc_utils):
        status_fam = GaugeMetricFamily(
            "oneview_enclosure_status",
            "Enclosure status (0=Unknown,1=OK,2=Warning,3=Critical,4=Disabled)",
            labels=["name", "serial_number", "uri"],
        )
        temp_fam = GaugeMetricFamily(
            "oneview_enclosure_ambient_temperature_celsius",
            "Enclosure ambient temperature in Celsius",
            labels=["name"],
        )
        avg_power_fam = GaugeMetricFamily(
            "oneview_enclosure_average_power_watts",
            "Enclosure average power in watts",
            labels=["name"],
        )
        peak_power_fam = GaugeMetricFamily(
            "oneview_enclosure_peak_power_watts",
            "Enclosure peak power in watts",
            labels=["name"],
        )

        for enc in enclosures:
            name = enc.get("name", "unknown")
            serial = enc.get("serialNumber", "unknown")
            uri = enc.get("uri", "")

            status_fam.add_metric(
                [name, serial, uri],
                STATUS_MAP.get(enc.get("status", "Unknown"), 0),
            )

            util = enc_utils.get(uri)
            if util:
                ml = util.get("metricList", [])
                val = _latest_sample(ml, "AmbientTemperature")
                if val is not None:
                    temp_fam.add_metric([name], val)
                val = _latest_sample(ml, "AveragePower")
                if val is not None:
                    avg_power_fam.add_metric([name], val)
                val = _latest_sample(ml, "PeakPower")
                if val is not None:
                    peak_power_fam.add_metric([name], val)

        yield status_fam
        yield temp_fam
        yield avg_power_fam
        yield peak_power_fam

    def _collect_interconnects(self, interconnects):
        status_fam = GaugeMetricFamily(
            "oneview_interconnect_status",
            "Interconnect status (0=Unknown,1=OK,2=Warning,3=Critical,4=Disabled)",
            labels=["name", "model", "serial_number", "uri"],
        )
        for ic in interconnects:
            status_fam.add_metric(
                [
                    ic.get("name", "unknown"),
                    ic.get("model", "unknown"),
                    ic.get("serialNumber", "unknown"),
                    ic.get("uri", ""),
                ],
                STATUS_MAP.get(ic.get("status", "Unknown"), 0),
            )
        yield status_fam

    def _collect_alerts(self, active_alerts):
        counts: dict[str, int] = {s: 0 for s in SEVERITY_LEVELS}
        for alert in active_alerts:
            sev = alert.get("severity", "Unknown")
            counts[sev] = counts.get(sev, 0) + 1

        g = GaugeMetricFamily(
            "oneview_active_alerts",
            "Number of active alerts by severity",
            labels=["severity"],
        )
        for sev in SEVERITY_LEVELS:
            g.add_metric([sev], counts[sev])
        yield g

        # Individual active alerts with detail labels.
        # Cardinality is bounded by the number of *currently active* alerts,
        # which is typically small.  Alerts disappear once cleared.
        alert_fam = GaugeMetricFamily(
            "oneview_alert_active",
            "Active alert. Value is always 1; labels carry detail.",
            labels=[
                "severity",
                "description",
                "corrective_action",
                "resource_name",
                "resource_category",
                "resource_uri",
                "alert_uri",
            ],
        )
        for alert in active_alerts:
            assoc = alert.get("associatedResource", {})
            alert_fam.add_metric(
                [
                    alert.get("severity", "Unknown"),
                    alert.get("description", ""),
                    alert.get("correctiveAction", ""),
                    assoc.get("resourceName", ""),
                    assoc.get("resourceCategory", ""),
                    assoc.get("resourceUri", ""),
                    alert.get("uri", ""),
                ],
                1,
            )
        yield alert_fam
