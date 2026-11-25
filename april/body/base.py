"""The body seam: named devices and a read-only telemetry contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

RISK_LEVELS = ("low", "medium", "high")


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    kind: str = "sensor"
    risk: str = "low"
    address: str = ""

    def __post_init__(self) -> None:
        if self.risk not in RISK_LEVELS:
            raise ValueError(f"risk must be one of {RISK_LEVELS}, got {self.risk!r}")


@dataclass(frozen=True)
class Telemetry:
    device_id: str
    ok: bool
    data: dict = field(default_factory=dict)
    error: str = ""


@runtime_checkable
class DeviceSource(Protocol):
    def devices(self) -> list[Device]: ...

    def read(self, device_id: str) -> Telemetry: ...
