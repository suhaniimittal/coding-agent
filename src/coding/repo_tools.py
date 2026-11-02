"""The tools the coding model works with (2026-11-02): everything it learns
about the code, and every change it makes, goes through these, on one
service's downloaded folder (workspace.py).

Reading: list a folder, find files by name, search text, read a range of
lines. Editing: replace one exact piece of text, create a file, delete a
file. Checking: the diff of everything changed so far. Each returns plain
text for the model; a request that can't be done raises ToolError, whose
message is shown to the model so it can try something else.

Every answer is capped (lines per read, hits per search, entries per list),
so one call never floods the model's context; it asks for more when it needs
more. Paths are relative to the service's folder and can never leave it.
Folders that are not source (node_modules, build output, ...) are left out
of listings and searches, and lockfiles, `.git` and generated folders can't
be edited. A test file can be changed or added, never deleted.

A file's text before its first edit is kept in memory (never on disk) so
`diff` can show the change; it goes when the run ends. Nothing here logs a
file's text.
"""

from __future__ import annotations

import difflib
import fnmatch
import re
from pathlib import Path

READ_LINES = 400
SEARCH_HITS = 50
LIST_ENTRIES = 200
FIND_HITS = 100
_LINE_CHARS = 240
_MAX_FILE_BYTES = 1_000_000

# Not source: skipped when listing and searching, and never edited.
_SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "out",
    "coverage",
    ".next",
    ".nuxt",
    "vendor",
    "target",
    "__pycache__",
    ".venv",
    "venv",
    ".gradle",
    ".idea",
    ".cache",
}
_LOCKFILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "go.sum",
    "composer.lock",
    "Gemfile.lock",
}
# Same rule as the planner's, so both sides agree on what is a test file.
_TEST_PATH = re.compile(
    r"(^|/)(src/test|test|tests|__tests__|acceptance-tests|e2e|cypress|__mocks__|mocks|fixtures)/"
    r"|[A-Za-z0-9_](Test|Tests|Spec)\.(kt|java|scala)$"
    r"|\.(spec|test|cy)\.(ts|tsx|js|jsx)$"
)


class ToolError(Exception):
    """A request the tool refused or couldn't do; the message is for the model."""


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


class RepoTools:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._before: dict[str, str | None] = {}  # path -> text before the first edit (None: new)
        self._file_list: list[str] | None = None
        self.chars_returned = 0

    # --- reading --------------------------------------------------------------------------

    def list_folder(self, path: str = ".") -> str:
        folder = self._path(path)
        if not folder.is_dir():
            raise ToolError(f"{path!r} is not a folder")
        entries = []
        for child in sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if child.is_symlink() or child.name in _SKIP_DIRS:
                continue
            if child.is_dir():
                entries.append(f"{child.name}/")
            else:
                entries.append(f"{child.name} ({_size(child.stat().st_size)})")
        shown = entries[:LIST_ENTRIES]
        if len(entries) > LIST_ENTRIES:
            shown.append(f"... {len(entries) - LIST_ENTRIES} more entries not shown")
        return self._out("\n".join(shown) or "(empty folder)")

    def find_files(self, pattern: str) -> str:
        """Files whose path matches `pattern`: a glob (`*Grid*.tsx`,
        `src/**/view.tsx`) or, with no wildcard, any path containing it
        (case-insensitive)."""
        wild = any(ch in pattern for ch in "*?[")
        needle = pattern.lower()
        hits = []
        for rel in self.all_files():
            low = rel.lower()
            name = low.rsplit("/", 1)[-1]
            if (
                (fnmatch.fnmatch(low, needle) or fnmatch.fnmatch(name, needle))
                if wild
                else needle in low
            ):
                hits.append(rel)
                if len(hits) > FIND_HITS:
                    break
        if not hits:
            return self._out(f"no file matches {pattern!r}")
        more = "\n... more not shown; narrow the pattern" if len(hits) > FIND_HITS else ""
        return self._out("\n".join(hits[:FIND_HITS]) + more)

    def search(
        self, text: str, path: str = ".", regex: bool = False, case_sensitive: bool = False
    ) -> str:
        """Lines containing `text` (or matching it as a regular expression),
        as `path:line: text`, in the folder or file `path`."""
        if not text:
            raise ToolError("search needs some text")
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(text if regex else re.escape(text), flags)
        except re.error as e:
            raise ToolError(f"not a valid regular expression: {e}") from e
        start = self._path(path)
        if start.is_file():
            files = [start.relative_to(self.root).as_posix()]
        elif start.is_dir():
            prefix = "" if start == self.root else start.relative_to(self.root).as_posix() + "/"
            files = [f for f in self.all_files() if f.startswith(prefix)]
        else:
            raise ToolError(f"{path!r} does not exist")
        hits = []
        for rel in files:
            lines = self._text_lines(rel)
            if lines is None:
                continue
            for n, line in enumerate(lines, start=1):
                if pattern.search(line):
                    hits.append(f"{rel}:{n}: {_clip(line.strip())}")
                    if len(hits) > SEARCH_HITS:
                        break
            if len(hits) > SEARCH_HITS:
                break
        if not hits:
            return self._out(f"no match for {text!r}")
        more = "\n... more matches not shown; narrow the search" if len(hits) > SEARCH_HITS else ""
        return self._out("\n".join(hits[:SEARCH_HITS]) + more)

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_lines: int = READ_LINES,
    ) -> str:
        """Lines `start_line`..`end_line` (1-based, inclusive) with their
        numbers; at most `max_lines` per call."""
        rel = self._rel(path)
        lines = self._text_lines(rel)
        if lines is None:
            raise ToolError(f"{path!r} is not a readable text file")
        total = len(lines)
        start = max(1, start_line)
        if total == 0:
            return self._out(f"{rel} is empty")
        if start > total:
            raise ToolError(f"{rel} has only {total} lines")
        end = min(total, end_line or total, start + max_lines - 1)
        body = "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(start, end + 1))
        head = f"{rel} (lines {start}-{end} of {total})"
        tail = f"\n... {total - end} more lines; read from line {end + 1}" if end < total else ""
        return self._out(f"{head}\n{body}{tail}")

    # --- editing --------------------------------------------------------------------------

    def replace_in_file(self, path: str, old_text: str, new_text: str) -> str:
        """Replaces `old_text`, which must appear exactly once, with `new_text`."""
        rel = self._rel(path, editing=True)
        text = self._read_text(rel)
        if not old_text:
            raise ToolError("old_text is empty; to write a new file use create_file")
        plain_old, plain_new = old_text, new_text
        if "\r\n" in text:  # the model writes "\n"; keep the file's own line endings
            old_text, new_text = _crlf(old_text), _crlf(new_text)
        count = text.count(old_text)
        if count == 0:
            # Same lines apart from spaces at their ends: still one clear place.
            loose = _replace_lines_loosely(text, plain_old, plain_new)
            if loose is not None:
                self._remember(rel, text)
                self._write(rel, loose)
                return self._out(f"replaced in {rel} (matched ignoring spaces at line ends)")
            raise ToolError(
                f"old_text was not found in {rel}; copy the text exactly, including spaces and "
                "indentation, without line numbers." + _closest(text, plain_old)
            )
        if count > 1:
            raise ToolError(
                f"old_text appears {count} times in {rel}; include more surrounding lines so it "
                "appears once"
            )
        self._remember(rel, text)
        self._write(rel, text.replace(old_text, new_text, 1))
        return self._out(
            f"replaced in {rel} ({old_text.count(chr(10)) + 1} line(s) -> "
            f"{new_text.count(chr(10)) + 1} line(s))"
        )

    def locate_lines(
        self, path: str, start: int, end: int, first_line: str, last_line: str
    ) -> tuple[int, int]:
        """The real (start, end) of an edit given by line numbers: where it
        says, if its first and last lines are there (spaces aside); else the
        nearest place holding both, as a slip in counting moves numbers."""
        rel = self._rel(path, editing=True)
        lines = self._read_text(rel).replace("\r\n", "\n").split("\n")
        if start < 1 or end < start:
            raise ToolError(f"start_line {start} and end_line {end} are not a range of lines")
        first, last = _squash(first_line), _squash(last_line)

        def fits(a: int, b: int) -> bool:
            return _squash(lines[a - 1]) == first and _squash(lines[b - 1]) == last

        if end <= len(lines) and fits(start, end):
            return start, end
        span = end - start
        found = sorted(
            {
                (a, b)
                for a in range(1, len(lines) + 1)
                if _squash(lines[a - 1]) == first
                for b in range(a, min(len(lines), a + span + 20) + 1)
                if fits(a, b) and abs((b - a) - span) <= 20
            },
            key=lambda ab: (abs(ab[0] - start), abs((ab[1] - ab[0]) - span)),
        )
        if found and (len(found) == 1 or abs(found[0][0] - start) < abs(found[1][0] - start)):
            return found[0]
        if start > len(lines):
            raise ToolError(f"line {start} is past the end of {rel}, which has {len(lines)} lines")
        shown = "\n".join(
            f"{n:>5}  {lines[n - 1]}" for n in range(start, min(end, start + 4, len(lines)) + 1)
        )
        raise ToolError(
            f"lines {start}-{end} of {rel} do not start with first_line and end with last_line; "
            f"the file there reads:\n{shown}"
        )

    def replace_lines(self, path: str, spans: list[tuple[int, int, str]]) -> str:
        """Replaces each (start, end, new) span, numbered on the file as it is
        now, bottom-up so one span never shifts another; spans may not overlap."""
        rel = self._rel(path, editing=True)
        text = self._read_text(rel)
        eol = "\r\n" if "\r\n" in text else "\n"
        lines = text.split(eol)
        ordered = sorted(spans, key=lambda s: s[0], reverse=True)
        for (start, _, _), (_, prev_end, _) in zip(ordered, ordered[1:]):
            if prev_end >= start:
                raise ToolError(f"two edits to {rel} overlap at line {start}")
        for start, end, new in ordered:
            new = new.replace("\r\n", "\n").removesuffix("\n")
            lines[start - 1 : end] = new.split("\n") if new else []
        self._remember(rel, text)
        self._write(rel, eol.join(lines))
        return self._out(f"replaced {len(spans)} range(s) in {rel}")

    def create_file(self, path: str, text: str) -> str:
        rel = self._rel(path, editing=True, must_exist=False)
        if (self.root / rel).exists():
            raise ToolError(f"{rel} already exists; use replace_in_file to change it")
        self._remember(rel, None)
        self._write(rel, text)
        return self._out(f"created {rel} ({text.count(chr(10)) + 1} lines)")

    def delete_file(self, path: str) -> str:
        rel = self._rel(path, editing=True)
        if is_test_path(rel):
            raise ToolError(f"{rel} is a test file; tests are never deleted")
        self._remember(rel, self._read_text(rel))
        (self.root / rel).unlink()
        self._file_list = None
        return self._out(f"deleted {rel}")

    # --- checking -------------------------------------------------------------------------

    def diff(self, context: int = 3) -> str:
        """Every change made so far, as a unified diff with `context` lines around."""
        parts = []
        for rel, before in sorted(self._before.items()):
            target = self.root / rel
            after = _load(target) if target.exists() else None
            if before == after:
                continue
            parts.append(
                "".join(
                    difflib.unified_diff(
                        (before or "").splitlines(keepends=True),
                        (after or "").splitlines(keepends=True),
                        fromfile="/dev/null" if before is None else f"a/{rel}",
                        tofile="/dev/null" if after is None else f"b/{rel}",
                        n=context,
                    )
                )
            )
        return self._out("\n".join(parts) or "no changes yet")

    def edited(self) -> list[str]:
        return sorted(self._before)

    def changed(self) -> list[str]:
        """Edited files whose text now differs from before the first edit."""
        out = []
        for rel, before in sorted(self._before.items()):
            target = self.root / rel
            if before != (_load(target) if target.exists() else None):
                out.append(rel)
        return out

    def before(self, rel: str) -> str | None:
        """An edited file's text before its first edit; None for a new file."""
        return self._before[rel]

    # --- for the code around the model (context_pack.py, code_checks.py) -------------------

    def all_files(self) -> list[str]:
        """Every file's path, outside the skipped folders, sorted."""
        if self._file_list is None:
            self._file_list = self._files()
        return self._file_list

    def text(self, rel: str) -> str | None:
        """A file's text now; None when it is missing, too big or not text."""
        if not (self.root / rel).is_file():
            return None
        lines = self._text_lines(rel)
        return None if lines is None else "\n".join(lines)

    # --- helpers --------------------------------------------------------------------------

    def _out(self, text: str) -> str:
        self.chars_returned += len(text)
        return text

    def _path(self, path: str) -> Path:
        clean = (path or ".").strip().lstrip("/") or "."
        target = (self.root / clean).resolve()
        if target != self.root and self.root not in target.parents:
            raise ToolError(f"{path!r} is outside the repository")
        return target

    def _rel(self, path: str, editing: bool = False, must_exist: bool = True) -> str:
        target = self._path(path)
        if target == self.root:
            raise ToolError("give a file path, not the repository root")
        rel = target.relative_to(self.root).as_posix()
        if must_exist and not target.is_file():
            raise ToolError(f"{rel} does not exist; use find_files to find the right path")
        if editing:
            parts = rel.split("/")
            if any(p in _SKIP_DIRS for p in parts[:-1]):
                raise ToolError(f"{rel} is in a generated or tool folder and can't be edited")
            if parts[-1] in _LOCKFILES:
                raise ToolError(f"{rel} is a lockfile and can't be edited")
        return rel

    def _files(self) -> list[str]:
        out = []
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root)
            if any(p in _SKIP_DIRS for p in rel.parts[:-1]):
                continue
            if path.is_file() and not path.is_symlink():
                out.append(rel.as_posix())
        return out

    def _text_lines(self, rel: str) -> list[str] | None:
        target = self.root / rel
        if target.stat().st_size > _MAX_FILE_BYTES:
            return None
        try:
            return target.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            return None

    def _read_text(self, rel: str) -> str:
        try:
            return _load(self.root / rel)
        except UnicodeDecodeError as e:
            raise ToolError(f"{rel} is not a text file") from e

    def _remember(self, rel: str, before: str | None) -> None:
        self._before.setdefault(rel, before)

    def _write(self, rel: str, text: str) -> None:
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        self._file_list = None


def _load(path: Path) -> str:
    """A file's text with its line endings as they are (no "\\r\\n" -> "\\n")."""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def _squash(line: str) -> str:
    """A line with its spaces evened out, for checking an edit's place."""
    return " ".join(line.split())


def _replace_lines_loosely(text: str, old: str, new: str) -> str | None:
    """`text` with the one run of lines equal to `old`'s lines (spaces at
    line ends ignored) replaced by `new`; None when there is not exactly one."""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    want = [line.rstrip() for line in old.replace("\r\n", "\n").strip("\n").split("\n")]
    k = len(want)
    if not any(want):
        return None
    starts = [
        i
        for i in range(len(lines) - k + 1)
        if [line.rstrip() for line in lines[i : i + k]] == want
    ]
    if len(starts) != 1:
        return None
    i = starts[0]
    new_lines = new.replace("\r\n", "\n").strip("\n").split("\n") if new.strip("\n") else []
    return eol.join(lines[:i] + new_lines + lines[i + k :])


def _closest(text: str, old: str, around: int = 3, max_chars: int = 3_000) -> str:
    """Where the file holds the text closest to `old`, with line numbers,
    for the model to copy from on its next try; "" when nothing is close."""
    lines = text.replace("\r\n", "\n").split("\n")
    first = next((line.strip() for line in old.split("\n") if line.strip()), "")
    if not first:
        return ""
    stripped = [line.strip() for line in lines]
    if first in stripped:
        at = stripped.index(first)
    else:
        close = difflib.get_close_matches(first, stripped, n=1, cutoff=0.6)
        if not close:
            return ""
        at = stripped.index(close[0])
    start = max(0, at - around)
    end = min(len(lines), at + len(old.split("\n")) + around)
    shown = "\n".join(f"{n + 1:>5}  {lines[n]}" for n in range(start, end))[:max_chars]
    return (
        f"\nThe closest text in the file is near line {at + 1} (line numbers are not part of "
        f"the file):\n{shown}"
    )


def _clip(line: str) -> str:
    return line if len(line) <= _LINE_CHARS else line[:_LINE_CHARS] + " ..."


def _size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n // 1024} KB"
