"""The reusable learning engine: propose, validate, evaluate, commit, distil."""

from __future__ import annotations

import json
from pathlib import Path

from ..llm import ChatRequest, Model
from .lessons import Lesson, LessonStore
from .target import Learnable, Trace, WorkflowTarget


class Engine:
    def __init__(
        self,
        target: Learnable,
        model: Model,
        model_name: str,
        *,
        library: LessonStore | None = None,
        k: int = 1,
        temperature: float = 0.4,
        proposer=None,
        runs_root: str | Path = "runs",
        tag: str = "",
    ):
        self.target = target
        self.model = model
        self.model_name = model_name
        self.library = library
        self.k = max(1, int(k))
        self.temperature = float(temperature)
        self.proposer = proposer
        self.runs_root = Path(runs_root)
        self.tag = tag

    def run(self, trace: Trace | None = None) -> Lesson | None:
        trace = trace or Trace()
        if (
            self.proposer is not None
            and trace.task_id
            and isinstance(self.target, WorkflowTarget)
        ):
            return self._run_workflow(trace)
        base = self.target.read()
        candidates: list[tuple[float, dict, dict]] = []
        for index in range(self.k):
            proposal = _extract_json(self._propose(base, trace, index))
            if proposal is None:
                continue
            snapshot = self.target.edit(proposal)
            if self.target.validate(snapshot):
                continue
            score = self.target.evaluate(snapshot, trace)
            candidates.append((score, snapshot, proposal))
        if not candidates:
            return None
        score, snapshot, proposal = max(candidates, key=lambda item: item[0])
        self.target.commit(snapshot)
        lesson = Lesson(
            cue=f"{self.target.name}: {trace.task_id or 'session'}",
            action=json.dumps(proposal, ensure_ascii=False)[:500],
            evidence=trace.evidence[:500],
            score=float(score),
            target=self.target.name,
        )
        if self.library is not None:
            self.library.add(lesson)
        return lesson

    def _run_workflow(self, trace: Trace) -> Lesson | None:
        """The full Propose-Probe-Compose loop for the workflow target."""
        from .schedule import run_task

        result = run_task(
            trace.task_id,
            self.k,
            adapter=self.target.adapter,
            prop=self.proposer,
            selector_prop=self.proposer,
            runs_root=self.runs_root,
            modes=("agg",),
            tag=self.tag,
        )
        if result.status != "ok":
            return None
        lesson = Lesson(
            cue=f"workflow: {trace.task_id}",
            action="schedule.run_task",
            evidence=trace.evidence[:500],
            score=1.0,
            target="workflow",
        )
        if self.library is not None:
            self.library.add(lesson)
        return lesson

    def _propose(self, base: dict, trace: Trace, index: int) -> str:
        prompt = (
            f"You are improving the {self.target.name} target.\n\n"
            f"Current state:\n{self.target.render(base)}\n\n"
            f"Evidence:\n{trace.evidence or '(none)'}\n\n"
            f"Candidate {index + 1}. Return ONLY a JSON object matching this "
            f"schema:\n{self.target.proposal_schema()}"
        )
        reply = self.model.complete(
            ChatRequest(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
            )
        )
        return reply.text


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
