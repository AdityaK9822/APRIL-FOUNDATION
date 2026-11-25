"""Deterministic device source for tests and the offline self-check."""

from __future__ import annotations

from .base import Device, Telemetry


class FakeDevices:
    def __init__(self, data: dict[str, dict] | None = None):
        self._data = dict(data or {"fake": {"temperature": 20.0}})
        self._devices = [
            Device(id=key, name=key, kind="fake", address=f"fake://{key}")
            for key in self._data
        ]

    def devices(self) -> list[Device]:
        return list(self._devices)

    def read(self, device_id: str) -> Telemetry:
        if device_id not in self._data:
            return Telemetry(device_id=device_id, ok=False, error="unknown device")
        return Telemetry(device_id=device_id, ok=True, data=dict(self._data[device_id]))
