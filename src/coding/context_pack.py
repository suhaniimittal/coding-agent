"""The code the coding model starts from (2026-11-02), gathered from the
downloaded folder by plain code, with no model call: the exploring a
developer would do before changing anything, done in one go so the model
needs few rounds.

From the TDD it takes the files it names (steps' "Where" and "Files to look
at") and the code names it mentions (`ReportsListScreen`, `actionButton`).
From the disk it then adds:

- the TDD's files, in full (a long file: the parts around those names);
  a file a step says to change ("Where") is shown whole up to
  WHERE_FILE_CHARS, so the model's edits copy from text with no gaps;
- files named after those names (`InfiniteScrollGrid/index.tsx`);
- what the TDD's files import from the repository, one level down;
- who imports the TDD's files and those named files (paths and lines);
- where else each name appears (search hits);
- the repository's own notes (README, CLAUDE.md, ...), package scripts and
  path aliases, and the layout around the files.

Everything fits a budget (PACK_CHARS); what doesn't is listed by path, for
the model to ask for. A TDD path that does not exist is said so, and the
nearest real path is used when there is exactly one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from . import imports
from .repo_tools import RepoTools, ToolError, is_test_path
from .tdd_reader import Task

PACK_CHARS = 150_000
FILE_CHARS = 30_000
# A file a TDD step says to change ("Where") is shown whole up to this size:
# the model copies its edits' "old" text from it, so it must not see gaps.
WHERE_FILE_CHARS = 120_000
USERS_PER_FILE = 20
HITS_PER_NAME = 20
_NAMES = 15
_NOTES_CHARS = 4_000
_WINDOW = 30  # lines kept either side of a name in a long file

# Code names: CamelCase with two or more humps, or camelCase.
_CODE_NAME = re.compile(r"\b(?:[A-Z][a-z0-9]+){2,}[A-Za-z0-9]*\b|\b[a-z]+(?:[A-Z][a-z0-9]*)+\b")
_NOT_CODE = {"JavaScript", "TypeScript", "GitHub", "PowerPoint", "LinkedIn", "iPhone", "macOS"}
_NOTE_FILES = ("CLAUDE.md", "AGENTS.md", "README.md", "CONTRIBUTING.md", ".cursorrules")


@dataclass
class Pack:
    text: str
    shown: list[str] = field(default_factory=list)
    listed: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


class Index:
    """The repository's files and who imports whom, for one service."""

    def __init__(self, tools: RepoTools, folder: str = "") -> None:
        self.tools = tools
        self.folder = folder.strip("/")
        self.files = tools.all_files()
        self.resolver = imports.Resolver(self.files, tools.text)
        self._importers: dict[str, list[tuple[str, int, str]]] | None = None

    def in_scope(self, path: str) -> bool:
        return not self.folder or path.startswith(self.folder + "/")

    def locate(self, path: str, service: str) -> str | None:
        """A TDD path as a real path: as written, without a leading service
        name, or the one file whose path ends with it."""
        clean = re.sub(r"\s*\(new file\)\s*$", "", path or "").strip().strip("`").lstrip("./")
        if not clean:
            return None
        bare = clean[len(service) + 1 :] if clean.startswith(service + "/") else clean
        candidates = [clean, bare] + ([f"{self.folder}/{bare}"] if self.folder else [])
        for c in dict.fromkeys(candidates):
            if c in self.resolver.files:
                return c
        ends = [f for f in self.files if f.endswith("/" + bare)]
        return ends[0] if len(ends) == 1 else None

    def named(self, name: str) -> list[str]:
        """Files called `name` (any extension), or `name/index.*`."""
        out = []
        for f in self.files:
            p = PurePosixPath(f)
            stem = p.name.split(".")[0]
            if (stem == name or (stem == "index" and p.parent.name == name)) and not is_test_path(
                f
            ):
                if self.in_scope(f) and imports.language(f):
                    out.append(f)
        return out[:3]

    def imports_of(self, path: str) -> list[str]:
        text = self.tools.text(path)
        if text is None:
            return []
        out = []
        for imp in imports.parse(text, path):
            target = self.resolver.resolve(path, imp.spec)
            if target and target != path and target not in out:
                out.append(target)
        return out

    def importers(self, target: str) -> list[tuple[str, int, str]]:
        """(file, line, the import line) for every file that imports `target`."""
        if self._importers is None:
            self._importers = {}
            for f in self.files:
                if not imports.language(f) or not self.in_scope(f):
                    continue
                text = self.tools.text(f)
                if text is None:
                    continue
                lines = text.splitlines()
                for imp in imports.parse(text, f):
                    resolved = self.resolver.resolve(f, imp.spec)
                    if resolved:
                        line = lines[imp.line - 1].strip() if imp.line <= len(lines) else ""
                        self._importers.setdefault(resolved, []).append((f, imp.line, line[:200]))
        return self._importers.get(target, [])

    def usages(self, name: str) -> str:
        """Where `name` is defined (a file called so) and who imports it: one
        answer for "who uses this?"."""
        files = self.named(name)
        parts = []
        for f in files:
            users = self.importers(f)
            parts.append(f"{f} is imported by {len(users)} file(s):")
            parts += [f"  {u}:{n}: {line}" for u, n, line in users[:USERS_PER_FILE]]
            if len(users) > USERS_PER_FILE:
                parts.append(f"  ... {len(users) - USERS_PER_FILE} more")
        try:
            hits = self.tools.search(name, self.folder or ".", case_sensitive=True)
        except ToolError as e:
            hits = str(e)
        parts += [f"Lines mentioning {name}:", hits]
        return "\n".join(parts)


def code_names(task: Task) -> list[str]:
    """The code names the TDD mentions, most mentioned first."""
    texts = [task.cause, task.fix, *task.design_decisions, *task.flow_today, *task.flow_after]
    for s in task.steps:
        texts += [s.title, s.where, s.what, s.why, s.done_when]
    texts += [f.path + " " + f.why for f in task.files_to_look_at]
    counts: dict[str, int] = {}
    for text in texts:
        for name in _CODE_NAME.findall(text or ""):
            if name not in _NOT_CODE and len(name) >= 4:
                counts[name] = counts.get(name, 0) + 1
    return sorted(counts, key=lambda n: -counts[n])[:_NAMES]


def build(tools: RepoTools, index: Index, task: Task, service: str) -> Pack:
    names = code_names(task)
    pack = Pack(text="", names=names)

    wheres = [s.where for s in task.steps if s.service in ("", service) and s.where]
    hints = wheres + [f.path for f in task.files_to_look_at if f.service in ("", service)]
    seeds: list[str] = []
    for hint in hints:
        found = index.locate(hint, service)
        if found and found not in seeds:
            seeds.append(found)
        elif not found and "(new file)" not in hint and hint not in pack.missing:
            pack.missing.append(hint)
    to_change = {f for f in (index.locate(w, service) for w in wheres) if f}

    named = [f for n in names for f in index.named(n) if f not in seeds]
    named = list(dict.fromkeys(named))
    imported: list[tuple[str, str]] = []  # (path, why)
    for seed in seeds:
        for target in index.imports_of(seed):
            if target in seeds or target in named or any(target == p for p, _ in imported):
                continue
            if target.endswith((".css", ".scss", ".less", ".json", ".d.ts", ".svg")):
                continue
            imported.append((target, f"imported by {seed}"))
    # Imports whose path holds a TDD name come first.
    imported.sort(key=lambda pw: not any(n.lower() in pw[0].lower() for n in names))

    candidates = [
        (p, "the TDD says to change it" if p in to_change else "named in the TDD") for p in seeds
    ]
    candidates += [(p, "named after a name in the TDD") for p in named]
    candidates += imported

    sections: list[str] = []
    used = 0
    shown_blocks: list[str] = []
    for path, why in candidates:
        limit = WHERE_FILE_CHARS if path in to_change else FILE_CHARS
        block = _file_block(tools, path, why, names, limit)
        if block is None:
            continue
        if used + len(block) > PACK_CHARS:
            pack.listed.append(f"{path} ({why})")
            continue
        shown_blocks.append(block)
        pack.shown.append(path)
        used += len(block)

    sections.append(_layout(tools, index, seeds or pack.shown))
    notes = _notes(tools, index)
    if notes:
        sections.append(notes)
    if pack.missing:
        sections.append(
            "## Paths in the TDD that do not exist in the repository\n"
            + "\n".join(f"- {m}" for m in pack.missing)
        )
    sections.append(
        "## Files (line numbers are not part of the files)\n\n" + "\n\n".join(shown_blocks)
        if shown_blocks
        else "## Files\n(none of the TDD's files were found)"
    )

    users = []
    for target in [*seeds, *named]:
        rows = index.importers(target)
        if rows:
            users.append(f"{target} is imported by {len(rows)} file(s):")
            users += [f"  {f}:{n}: {line}" for f, n, line in rows[:USERS_PER_FILE]]
            if len(rows) > USERS_PER_FILE:
                users.append(f"  ... {len(rows) - USERS_PER_FILE} more")
    if users:
        sections.append("## Who uses them\n" + "\n".join(users))

    hits = []
    for name in names:
        try:
            found = tools.search(name, index.folder or ".", case_sensitive=True)
        except ToolError:
            continue
        lines = [
            line
            for line in found.splitlines()
            if ":" in line and line.split(":", 1)[0] not in pack.shown
        ][:HITS_PER_NAME]
        if lines:
            hits.append(f"{name}:\n" + "\n".join(f"  {line}" for line in lines))
    if hits:
        sections.append("## Where the TDD's names appear in other files\n" + "\n".join(hits))

    if pack.listed:
        sections.append(
            "## Related files not shown (ask to read them if you need them)\n"
            + "\n".join(f"- {p}" for p in pack.listed)
        )
    pack.text = "\n\n".join(sections)
    return pack


def _file_block(
    tools: RepoTools, path: str, why: str, names: list[str], limit: int = FILE_CHARS
) -> str | None:
    text = tools.text(path)
    if text is None:
        return None
    lines = text.splitlines()
    numbered = [f"{n:>5}  {line}" for n, line in enumerate(lines, start=1)]
    body = "\n".join(numbered)
    if len(body) <= limit:
        return f"### {path} ({why}; {len(lines)} lines)\n{body}"
    keep: set[int] = set(range(min(40, len(lines))))  # its imports and top
    for i, line in enumerate(lines):
        if any(n in line for n in names):
            keep.update(range(max(0, i - _WINDOW), min(len(lines), i + _WINDOW + 1)))
    parts, last, size = [], -1, 0
    for i in sorted(keep):
        if size > limit:
            break
        if i != last + 1:
            parts.append("  ...")
        parts.append(numbered[i])
        size += len(numbered[i]) + 1
        last = i
    if last < len(lines) - 1:
        parts.append(f"  ... (file continues to line {len(lines)}; ask to read other parts)")
    return f"### {path} ({why}; {len(lines)} lines, long: parts shown)\n" + "\n".join(parts)


def _layout(tools: RepoTools, index: Index, around: list[str]) -> str:
    parts = ["## Repository layout", "Top level:", tools.list_folder(".")]
    if index.folder:
        parts += [f"\nThe service's folder {index.folder}/:", tools.list_folder(index.folder)]
    for folder in dict.fromkeys(str(PurePosixPath(p).parent) for p in around[:4]):
        if folder not in (".", index.folder):
            listing = tools.list_folder(folder).splitlines()
            parts += [f"\n{folder}/:", "\n".join(listing[:60])]
    return "\n".join(parts)


def _notes(tools: RepoTools, index: Index) -> str:
    """The repository's own notes and conventions, short."""
    parts = []
    roots = ["", index.folder] if index.folder else [""]
    for root in roots:
        prefix = f"{root}/" if root else ""
        for name in _NOTE_FILES:
            text = tools.text(prefix + name)
            if text:
                cut = text[:_NOTES_CHARS] + ("\n..." if len(text) > _NOTES_CHARS else "")
                parts.append(f"### {prefix}{name}\n{cut}")
        package = tools.text(prefix + "package.json")
        if package:
            try:
                data = json.loads(package)
            except json.JSONDecodeError:
                data = {}
            summary = {
                "name": data.get("name"),
                "scripts": data.get("scripts"),
                "dependencies": sorted(data.get("dependencies") or {})[:60],
                "devDependencies": sorted(data.get("devDependencies") or {})[:40],
            }
            parts.append(f"### {prefix}package.json (summary)\n" + json.dumps(summary, indent=1))
    if index.resolver.aliases:
        aliases = ", ".join(f"{a} -> {', '.join(t)}" for a, t in index.resolver.aliases[:10])
        parts.append(f"### Import aliases (from tsconfig/jsconfig)\n{aliases}")
    return "## Repository notes\n" + "\n\n".join(parts) if parts else ""
