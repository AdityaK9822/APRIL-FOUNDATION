"""Lessons and the generic lesson store."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Lesson:
    cue: str
    action: str
    evidence: str
    score: float
    target: str


@runtime_checkable
class LessonStore(Protocol):
    def add(self, lesson: Lesson) -> None: ...

    def lessons(self) -> list[Lesson]: ...


class InMemoryLessonStore:
    def __init__(self):
        self._lessons: list[Lesson] = []

    def add(self, lesson: Lesson) -> None:
        self._lessons.append(lesson)

    def lessons(self) -> list[Lesson]:
        return list(self._lessons)


class FileLessonStore:
    """One JSON object per line; append-only."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def add(self, lesson: Lesson) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(lesson), ensure_ascii=False) + "\n")

    def lessons(self) -> list[Lesson]:
        if not self.path.is_file():
            return []
        out: list[Lesson] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(Lesson(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return out
