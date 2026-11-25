"""APRIL — a composer runtime over reusable modules.

Built beside ``core/`` as a strangler; see ``docs/spec/rebuild-plan.md``.
Nothing is imported at package top level: each module is constructed at the
composition root (``april.app``, Phase 8).
"""

__version__ = "0.2.0"
