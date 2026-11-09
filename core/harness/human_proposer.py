"""A Proposer whose session is a human at the web app.

The channel stages the engine's evidence directory exactly like the model
channels do (`context/` at the session root), then publishes
`context/harness/` for a human to edit. The engine thread blocks until the
human accepts, declines, or the proposal times out.

Declining raises `ProposerError`, so the stage falls back to the current
harness exactly as an unreachable model channel would -- the ledger's R/P
identity is unaffected. This is a duck-typed proposer: it satisfies
`Proposer.call` without touching `proposer.py` internals.

Ponytail: a human proposal is one flat file map. Deletions and binary files
are out of scope; clearing a file's text deletes it, which is enough for the
always-on Markdown surface.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Protocol

from .proposer import ProposerError, Reply


class ProposalBridge(Protocol):
    """What the runner must provide. Returns accepted files or None to decline."""

    def request_proposal(
        self,
        *,
        label: str,
        mission: str,
        files: Mapping[str, str],
        timeout_s: float,
    ) -> dict[str, str] | None: ...


def _read_files(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            out[rel] = path.read_text(encoding="utf-8", errors="replace")
    return out


def _safe_relative(rel: str) -> str:
    clean = rel.replace("\\", "/").lstrip("/")
    parts = [p for p in clean.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        raise ProposerError(f"human proposal path {rel!r} escapes the harness directory")
    return "/".join(parts)


def _apply(root: Path, files: Mapping[str, str]) -> None:
    """Replace the staged harness directory with the accepted file map."""
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        target = root / _safe_relative(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(text), encoding="utf-8")


class HumanProposer:
    """A `Proposer.call`-compatible channel backed by a web editor."""

    spec = type(
        "ChannelSpecLike",
        (),
        {"kind": "human", "model": "user", "effort": "", "steps": 0, "timeout": 0},
    )()
    attempts = 1
    audit_dir = None

    def __init__(self, bridge: ProposalBridge, *, timeout_s: float = 300.0) -> None:
        self.bridge = bridge
        self.timeout_s = float(timeout_s)

    def call(
        self,
        mission: str,
        context_dir: Path | str | None,
        label: str,
        *,
        system: str = "",
        timeout: int | None = None,
        max_attempts: int | None = None,
        harvest: str | None = None,
    ) -> Reply:
        sub = (harvest or "context/harness").strip("/")
        session = Path(tempfile.mkdtemp(prefix="april-human-prop-"))
        try:
            if context_dir is not None and Path(context_dir).is_dir():
                # The engine passes the evidence dir itself (e.g. `.../context/inv1`);
                # every model channel stages it as `<session>/context`, which is what
                # `harvest="context/harness"` names.
                shutil.copytree(Path(context_dir), session / "context", dirs_exist_ok=True)
        except OSError as exc:
            shutil.rmtree(session, ignore_errors=True)
            raise ProposerError(f"[{label}] could not stage the evidence: {exc}") from exc

        staged = session / sub
        staged.mkdir(parents=True, exist_ok=True)
        current = _read_files(staged)
        accepted = self.bridge.request_proposal(
            label=label,
            mission=mission,
            files=current,
            timeout_s=float(timeout or self.timeout_s),
        )
        if accepted is None:
            shutil.rmtree(session, ignore_errors=True)
            raise ProposerError(
                f"[{label}] human proposer declined (or timed out); the stage falls back",
                attempts=0,
            )

        changed = {rel: text for rel, text in accepted.items() if current.get(rel) != text}
        removed = set(current) - set(accepted)
        try:
            _apply(staged, accepted)
        except (OSError, ProposerError) as exc:
            shutil.rmtree(session, ignore_errors=True)
            raise ProposerError(f"[{label}] could not apply the human edit: {exc}") from exc

        if changed or removed:
            text = (
                "<notes>human edit from the web run editor: "
                + ", ".join(sorted(changed) or sorted(removed))
                + "</notes>"
            )
        else:
            text = "<notes>human kept the current harness unchanged</notes>"
        return Reply(
            label=label,
            text=text,
            ok=True,
            cost_usd=0.0,
            wall_s=0.0,
            attempts=1,
            cached=False,
            channel="human",
            workspace=staged,
        )


__all__ = ["HumanProposer", "ProposalBridge"]
