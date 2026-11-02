"""R7: a code change and the test change that covers it are a pair.

Two changes in one service are a pair when they name the same
function_or_symbol and exactly one side's file is a test file. (For a test
change, function_or_symbol names the PRODUCTION symbol whose behaviour
changed, which is what makes the match possible.)

- Case 1, the code change did not land (skipped for any reason, or produced
  no actual change): drop the test change too. A test asserting behaviour
  nothing implements would only break the build.
- Case 2, the code landed but its test change did not: keep the code (an
  empty PR leaves the bug unfixed) and warn loudly that the existing test
  still asserts the old behaviour and will fail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import FilePatch, SkippedChange
from .tdd_models import FileChange

# Same rule the planner uses, so both sides agree on what is a test file.
_TEST_PATH = re.compile(
    r"(^|/)(src/test|test|tests|__tests__|acceptance-tests|e2e|cypress|__mocks__|mocks|fixtures)/"
    r"|[A-Za-z0-9_](Test|Tests|Spec)\.(kt|java|scala)$"
    r"|\.(spec|test|cy)\.(ts|tsx|js|jsx)$"
)


def is_test_path(path: str | None) -> bool:
    return bool(path) and bool(_TEST_PATH.search(path))


@dataclass
class _Pair:
    symbol: str
    code: list[FileChange]
    tests: list[FileChange]


def _pairs(changes: list[FileChange]) -> list[_Pair]:
    by_symbol: dict[str, _Pair] = {}
    for change in changes:
        if change.change_type == "reference" or not change.function_or_symbol:
            continue
        pair = by_symbol.setdefault(
            change.function_or_symbol, _Pair(change.function_or_symbol, [], [])
        )
        (pair.tests if is_test_path(change.file_path) else pair.code).append(change)
    return [p for p in by_symbol.values() if p.code and p.tests]


def enforce_test_pairs(
    service: str,
    changes: list[FileChange],
    patches: list[FilePatch],
    to_commit: list[FilePatch],
) -> tuple[list[FilePatch], list[FilePatch], list[SkippedChange], list[str]]:
    """Applies R7 to one service. `changes` is every change the TDD lists for
    it (so code changes skipped earlier still count), `patches` everything the
    LLM produced, `to_commit` the subset that really changes a file.
    Returns (patches, to_commit, skipped, warnings) with Case-1 tests removed."""
    committed = {p.file_path for p in to_commit}
    drop: set[str] = set()
    skipped: list[SkippedChange] = []
    warnings: list[str] = []

    for pair in _pairs(changes):
        code_landed = any(c.file_path in committed for c in pair.code)
        code_files = ", ".join(c.file_path or "<no path>" for c in pair.code)
        for test in pair.tests:
            if not code_landed:
                drop.add(test.file_path)
                skipped.append(
                    SkippedChange(
                        service,
                        test,
                        f"skipped with its code change: {pair.symbol} ({code_files}) was not "
                        "applied, so this test would assert behaviour nothing implements",
                    )
                )
            elif test.file_path not in committed:
                warnings.append(
                    f"Test change {test.file_path} was not applied. The code change to "
                    f"{pair.symbol} ({code_files}) altered its observable behaviour; the "
                    "existing test still asserts the old behaviour and will likely fail."
                )

    kept_patches = [p for p in patches if p.file_path not in drop]
    kept_commit = [p for p in to_commit if p.file_path not in drop]
    return kept_patches, kept_commit, skipped, warnings
