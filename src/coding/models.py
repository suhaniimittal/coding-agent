"""Data shapes for the coding pipeline: a re-verified change (safe to act
on) vs. a skipped one (flagged for a human instead), and the result of
patching one file.
"""

from __future__ import annotations

from dataclasses import dataclass

from .tdd_models import FileChange


@dataclass
class VerifiedChange:
    """A FileChange that's been re-checked against the CURRENT state of the
    cloned repo (not just trusted from the TDD's own has_real_source claim,
    which could have gone stale since the TDD was generated) — safe to hand
    to the patcher."""

    service: str
    repo: str
    branch: str | None
    change: FileChange


@dataclass
class SkippedChange:
    """A FileChange that failed the structural check — never auto-applied,
    always surfaced to a human instead."""

    service: str
    change: FileChange
    reason: str


@dataclass
class FilePatch:
    """One file's result from the per-service patch call — either its full
    corrected content (modify/create, produced by that one LLM call
    alongside every other file in the same service) or a removal (delete,
    new_content is None, no LLM involved for it)."""

    service: str
    file_path: str
    change_type: str  # "modify" | "create" | "delete"
    new_content: str | None
    changes_applied: list[str]


@dataclass
class VerificationResult:
    """The outcome of trying to prove a branch's changes actually work —
    never assumed, always the result of a real attempt (or an honest
    admission that nothing could be run)."""

    ran: bool
    passed: bool
    detail: str
