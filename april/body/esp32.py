"""ESP32 telemetry over HTTP. Read-only until the risk policy allows actuation."""

from __future__ import annotations

import json
import os
import urllib.request

from .base import Device, Telemetry


class ESP32Devices:
    def __init__(self, devices: list[Device] | None = None, timeout: float = 5.0):
        self._devices = list(devices if devices is not None else _from_env())
        self.timeout = timeout

    def devices(self) -> list[Device]:
        return list(self._devices)

    def read(self, device_id: str) -> Telemetry:
        device = next(
            (item for item in self._devices if item.id == device_id), None
        )
        if device is None:
            return Telemetry(device_id=device_id, ok=False, error="unknown device")
        if not device.address:
            return Telemetry(device_id=device_id, ok=False, error="device has no address")
        try:
            with urllib.request.urlopen(device.address, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
            payload = json.loads(raw)
        except Exception as exc:
            return Telemetry(device_id=device_id, ok=False, error=str(exc))
        return Telemetry(
            device_id=device_id,
            ok=True,
            data=payload if isinstance(payload, dict) else {"value": payload},
        )


def _from_env() -> list[Device]:
    """``APRIL_ESP32_DEVICES="id=url,id=url"`` or bare URLs."""
    raw = os.getenv("APRIL_ESP32_DEVICES", "").strip()
    if not raw:
        return []
    devices: list[Device] = []
    for index, part in enumerate(part for part in raw.split(",") if part.strip()):
        token = part.strip()
        if "=" in token:
            device_id, address = token.split("=", 1)
        else:
            device_id, address = f"esp32-{index + 1}", token
        devices.append(
            Device(
                id=device_id.strip(),
                name=device_id.strip(),
                kind="esp32",
                risk="low",
                address=address.strip(),
            )
        )
    return devices
