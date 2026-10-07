"""Real verification, never a guess: detects a repo's own test command and
actually runs it. No LLM "does this look right" pass — the repair loop that
feeds a real failure back into the patcher lives in coding_flow.py, which
orchestrates patcher.py + repo_ops.py + this module together; this module's
only job is "detect a command, run it, report what genuinely happened."
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .models import VerificationResult

# Checked in order; the first marker file found wins. A lookup, not a
# guess — same discipline as summarizer.py's entry-point basenames: a repo
# either has one of these well-known markers or no test command is run at
# all (VerificationResult.ran = False), never a fabricated "probably this."
_TEST_COMMANDS_BY_MARKER: list[tuple[str, list[str]]] = [
    ("pom.xml", ["mvn", "-q", "-B", "test"]),
    ("build.gradle", ["./gradlew", "test", "--quiet"]),
    ("build.gradle.kts", ["./gradlew", "test", "--quiet"]),
    ("go.mod", ["go", "test", "./..."]),
    ("package.json", ["npm", "test", "--silent"]),
    ("pyproject.toml", ["pytest", "-q"]),
    ("requirements.txt", ["pytest", "-q"]),
]

_OUTPUT_TAIL_CHARS = 4000


def detect_test_command(repo_root: Path) -> list[str] | None:
    for marker, command in _TEST_COMMANDS_BY_MARKER:
        if (repo_root / marker).exists():
            return command
    return None


# The repo's tests execute that repo's own code, so they must not inherit the
# agent's credentials (GitHub token, Neo4j password, LLM keys, the platform's
# storage/AWS identity). Everything else (PATH, JAVA_HOME, HOME, ...) is kept
# so the build tools still work.
_SECRET_ENV_PREFIXES = (
    "GITHUB_",
    "NEO4J_",
    "OPENAI_",
    "AGENTS_",
    "AI_GATEWAY",
    "STORAGE_",
    "AWS_",
    "AETHERION_",
    "TENANT_ID",
)


def _scrubbed_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith(_SECRET_ENV_PREFIXES)}


def run_tests(repo_root: Path, timeout: int = 300) -> VerificationResult:
    """Actually runs the repo's own test command, if one can be detected.
    Never fabricates a pass/fail — an undetectable test command is reported
    as `ran=False`, not assumed to have passed."""
    command = detect_test_command(repo_root)
    if command is None:
        return VerificationResult(
            ran=False, passed=False, detail="no test command detected for this repo"
        )

    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            env=_scrubbed_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return VerificationResult(
            ran=True,
            passed=False,
            detail=f"test command timed out after {timeout}s: {' '.join(command)}",
        )
    except OSError as e:
        return VerificationResult(
            ran=False, passed=False, detail=f"could not run test command {' '.join(command)}: {e}"
        )

    output = (result.stdout or "") + (result.stderr or "")
    tail = output[-_OUTPUT_TAIL_CHARS:]
    if result.returncode == 0:
        return VerificationResult(ran=True, passed=True, detail=tail or "tests passed")
    return VerificationResult(
        ran=True, passed=False, detail=tail or f"test command exited {result.returncode}"
    )
