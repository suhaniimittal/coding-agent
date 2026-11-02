"""Checks on the model's change, with no build and no model call
(2026-11-02). The agent can't build the repository, so these catch, before
the pull request, the mistakes a build would have:

- syntax: every changed file still parses (tree-sitter, in Python). Only
  errors the change added count: a file the parser already disliked is not
  blamed on the change.
- imports: every import the change added points at a file that exists, and
  every name it imports from a repository file is exported there (JS/TS).
- scope: only files in the model's plan changed.
- leftovers: no console.log, debugger, TODO/FIXME or merge markers added.
- size: at most MAX_FILES files and MAX_LINES changed lines.

`problems` go back to the model to fix; `over_limits` make the pull
request a draft. Deep type errors are left to Jenkins, which builds the
pushed branch.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from . import imports
from .repo_tools import RepoTools

MAX_FILES = 10
MAX_LINES = 400

_LANGUAGES = {
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".py": "python",
    ".java": "java",
    ".kt": "kotlin",
    ".go": "go",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".scala": "scala",
    ".rs": "rust",
}
_LEFTOVER = re.compile(
    r"console\.log\(|\bdebugger\b|\bTODO\b|\bFIXME\b|^(<<<<<<<|>>>>>>>|=======)$"
)


@dataclass
class Result:
    problems: list[str] = field(default_factory=list)
    over_limits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def run(tools: RepoTools, resolver: imports.Resolver, planned: set[str]) -> Result:
    result = Result()
    changed_lines = 0
    edited = tools.changed()
    for path in edited:
        # Both sides as text() gives them: lines joined by "\n", whatever the file's endings.
        before, after = _lines(tools.before(path)), tools.text(path)
        added = _added_lines(before, after)
        changed_lines += len(added) + _removed_count(before, after)
        if path not in planned:
            result.problems.append(f"{path} was changed but is not in your plan")
        if after is None:
            continue
        _syntax(path, before, after, result)
        _imports(path, before, after, tools, resolver, result)
        for line in added:
            if _LEFTOVER.search(line.strip()):
                result.problems.append(f"{path}: remove the leftover line: {line.strip()[:120]}")
    if len(edited) > MAX_FILES:
        result.over_limits.append(f"{len(edited)} files changed (limit {MAX_FILES})")
    if changed_lines > MAX_LINES:
        result.over_limits.append(f"{changed_lines} lines changed (limit {MAX_LINES})")
    return result


def _syntax(path: str, before: str | None, after: str, result: Result) -> None:
    lang = next((v for k, v in _LANGUAGES.items() if path.endswith(k)), None)
    if lang is None:
        return
    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(lang)
    except Exception as e:  # noqa: BLE001 - not installed, or no grammar: skip, say so
        note = f"syntax of {path} not checked ({type(e).__name__})"
        if note not in result.notes:
            result.notes.append(note)
        return
    old = _error_lines(parser, before) if before is not None else []
    new = _error_lines(parser, after)
    if len(new) > len(old):
        lines = ", ".join(str(n) for n in sorted(set(new) - set(old))[:5]) or str(new[0])
        result.problems.append(f"{path}: syntax error near line {lines}")


def _error_lines(parser, text: str) -> list[int]:
    tree = parser.parse(text.encode("utf-8"))
    out, stack = [], [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            out.append(node.start_point[0] + 1)
            continue
        if node.has_error:
            stack.extend(node.children)
    return out


def _imports(
    path: str,
    before: str | None,
    after: str,
    tools: RepoTools,
    resolver: imports.Resolver,
    result: Result,
) -> None:
    old = {(i.spec, tuple(i.names), i.default) for i in imports.parse(before or "", path)}
    for imp in imports.parse(after, path):
        if (imp.spec, tuple(imp.names), imp.default) in old:
            continue
        target = resolver.resolve(path, imp.spec)
        if target is None:
            if imports.is_local(imp.spec):
                result.problems.append(
                    f"{path} line {imp.line}: imports {imp.spec!r}, which is not a file in the "
                    "repository"
                )
            continue
        if imports.language(path) != "js" or not target.endswith(imports.JS_EXTENSIONS):
            continue
        exported = imports.exported_names(tools.text(target) or "")
        if exported is None:
            continue
        missing = [n for n in imp.names if n not in exported]
        if imp.default and "default" not in exported:
            missing.append("its default export")
        if missing:
            result.problems.append(
                f"{path} line {imp.line}: imports {', '.join(missing)} from {target}, which does "
                "not export it"
            )


def _lines(text: str | None) -> str | None:
    return None if text is None else "\n".join(text.splitlines())


def _added_lines(before: str | None, after: str | None) -> list[str]:
    diff = difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(), n=0)
    return [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]


def _removed_count(before: str | None, after: str | None) -> int:
    diff = difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(), n=0)
    return sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
