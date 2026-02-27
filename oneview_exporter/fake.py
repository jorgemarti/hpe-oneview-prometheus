"""Fake OneView client that returns realistic simulated data."""

from __future__ import annotations

import random
import time

_SERVERS = [
    ("srv-rack01-01", "ProLiant DL380 Gen10 Plus", "CZ233101AA", 1048576, 2),
    ("srv-rack01-02", "ProLiant DL380 Gen10 Plus", "CZ233101AB", 1048576, 2),
    ("srv-rack01-03", "ProLiant DL380 Gen11", "CZ240301AC", 262144, 2),
    ("srv-rack02-01", "ProLiant DL380 Gen10 Plus", "CZ233101AD", 131072, 2),
    ("srv-rack02-02", "ProLiant DL380 Gen9", "CZJ74501AE", 786432, 2),
]

_ENCLOSURES = [
    ("enclosure-01", "SN2024001"),
    ("enclosure-02", "SN2024002"),
]

_INTERCONNECTS = [
    ("interconnect-01", "Virtual Connect SE 100Gb F32", "IC2024001"),
    ("interconnect-02", "Virtual Connect SE 100Gb F32", "IC2024002"),
]


def _utilization(temp_base: float, power_base: float) -> dict:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {
        "metricList": [
            {"metricName": "AmbientTemperature", "metricSamples": [[ts, temp_base + random.uniform(-1, 2)]]},
            {"metricName": "CpuUtilization", "metricSamples": [[ts, random.uniform(2, 60)]]},
            {"metricName": "AveragePower", "metricSamples": [[ts, power_base + random.uniform(-20, 40)]]},
            {"metricName": "PeakPower", "metricSamples": [[ts, power_base + random.uniform(50, 150)]]},
        ]
    }


class FakeOneViewClient:
    """Drop-in replacement for OneViewClient that generates fake metrics."""

    def get_server_hardware(self) -> list[dict]:
        servers = []
        statuses = ["OK", "OK", "OK", "Warning", "OK"]
        for i, (name, model, serial, mem, cpus) in enumerate(_SERVERS):
            uri = f"/rest/server-hardware/fake-{i:04d}"
            servers.append({
                "uri": uri,
                "name": name,
                "model": model,
                "serialNumber": serial,
                "powerState": "On",
                "status": statuses[i % len(statuses)],
                "memoryMb": mem,
                "processorCount": cpus,
            })
        return servers

    def get_server_utilization(self, server_uri: str) -> dict:
        return _utilization(temp_base=18.0, power_base=350.0)

    def get_enclosures(self) -> list[dict]:
        return [
            {"uri": f"/rest/enclosures/fake-{i:04d}", "name": name, "serialNumber": sn, "status": "OK"}
            for i, (name, sn) in enumerate(_ENCLOSURES)
        ]

    def get_enclosure_utilization(self, enclosure_uri: str) -> dict:
        return _utilization(temp_base=20.0, power_base=2800.0)

    def get_interconnects(self) -> list[dict]:
        return [
            {"uri": f"/rest/interconnects/fake-{i:04d}", "name": name, "model": model, "serialNumber": sn, "status": "OK"}
            for i, (name, model, sn) in enumerate(_INTERCONNECTS)
        ]

    def get_active_alerts(self) -> list[dict]:
        return [
            {
                "uri": "/rest/alerts/fake-001",
                "severity": "Warning",
                "alertState": "Active",
                "description": "The server hardware health status is degraded.",
                "correctiveAction": "Check the server hardware health in OneView.",
                "associatedResource": {
                    "resourceName": "srv-rack01-01",
                    "resourceCategory": "server-hardware",
                    "resourceUri": "/rest/server-hardware/fake-0000",
                },
            },
            {
                "uri": "/rest/alerts/fake-002",
                "severity": "Warning",
                "alertState": "Active",
                "description": "Firmware update available for server hardware.",
                "correctiveAction": "Apply the latest firmware using SPP.",
                "associatedResource": {
                    "resourceName": "srv-rack02-01",
                    "resourceCategory": "server-hardware",
                    "resourceUri": "/rest/server-hardware/fake-0003",
                },
            },
            {
                "uri": "/rest/alerts/fake-003",
                "severity": "Critical",
                "alertState": "Active",
                "description": "Power supply redundancy lost.",
                "correctiveAction": "Replace the failed power supply immediately.",
                "associatedResource": {
                    "resourceName": "srv-rack01-02",
                    "resourceCategory": "server-hardware",
                    "resourceUri": "/rest/server-hardware/fake-0001",
                },
            },
        ]
