"""One logger over the event bus."""

from __future__ import annotations

from pathlib import Path

from .bus import EventBus


class FileSink:
    """Writes every ``log`` event to a file, one record per line.

    shortcut: the file is opened and appended per record; buffer it only when
    log volume makes per-write open a real cost.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)

    def attach(self, bus: EventBus) -> None:
        bus.subscribe("log", self._write)

    def _write(self, event) -> None:
        record = event.payload
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(
                f"{record.timestamp.isoformat()} [{record.level}] {record.source}: "
                f"{record.message}\n"
            )


class Logger:
    """A fixed-source facade over the bus's ``log`` events."""

    def __init__(self, bus: EventBus, source: str = "april"):
        self.bus = bus
        self.source = source

    def log(self, level: str, message: str):
        return self.bus.emit_log(level, self.source, message)

    def debug(self, message):
        return self.log("DEBUG", message)

    def info(self, message):
        return self.log("INFO", message)

    def warning(self, message):
        return self.log("WARNING", message)

    def error(self, message):
        return self.log("ERROR", message)
