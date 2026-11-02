"""Fix A: real repository context for every TEST file the LLM writes.

Without it the model writes a test from the TDD text alone and invents
imports, helpers, providers and APIs (validateSync from 'yup', a guessed
SettingsFormContext setup). This collects, READ-ONLY, from the repo already
downloaded for this service:

1. the production file(s) the test covers (same function_or_symbol, R7 pairing)
2. existing files the TDD's text names by path (e.g. "reuse the setup from
   src/.../view.test.tsx"), else the nearest existing test as a style example
3. CLAUDE.md, where teams usually write down their conventions
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .models import VerifiedChange
from .test_pairing import is_test_path

logger = logging.getLogger(__name__)

_MAX_FILE_CHARS = 12_000  # per reference file; long files are cut, not dropped
_MAX_NAMED_FILES = 3
_PATH_IN_TEXT = re.compile(r"[\w@.\-/]+/[\w@.\-]+\.(?:tsx|ts|jsx|js|kt|java|scala|py)\b")
_FAMILY = {
    ".ts": "js",
    ".tsx": "js",
    ".js": "js",
    ".jsx": "js",
    ".kt": "jvm",
    ".java": "jvm",
    ".scala": "jvm",
    ".py": "py",
}
_SKIP_DIRS = {"node_modules", ".git", "dist", "build", "coverage", "target", ".next"}


def _read(repo_root: Path, rel: str) -> str | None:
    path = repo_root / rel
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return None
    if len(text) > _MAX_FILE_CHARS:
        text = text[:_MAX_FILE_CHARS] + "\n/* … truncated for length … */\n"
    return text


def _resolve(repo_root: Path, raw: str) -> str | None:
    """A path as written in the TDD, made repo-relative if it exists. The
    planner sometimes prefixes the service name ("ui/src/...")."""
    candidate = raw.strip("`'\".,;:()").lstrip("./")
    parts = candidate.split("/")
    for start in range(min(2, len(parts))):
        rel = "/".join(parts[start:])
        if rel and (repo_root / rel).is_file():
            return rel
    return None


def _paths_in(repo_root: Path, text: str, exclude: set[str], only_tests: bool) -> list[str]:
    # PDF text wraps long paths ("…/AtsIntegrationSettings/\nview.test.tsx");
    # rejoin a path broken right after a slash before matching.
    text = re.sub(r"/\s+(?=[\w@.\-])", "/", text)
    found: list[str] = []
    for raw in _PATH_IN_TEXT.findall(text):
        rel = _resolve(repo_root, raw)
        if (
            rel
            and rel not in exclude
            and rel not in found
            and (is_test_path(rel) or not only_tests)
        ):
            found.append(rel)
    return found


def _named_paths(repo_root: Path, group: list[VerifiedChange], exclude: set[str]) -> list[str]:
    text = " ".join(
        part
        for v in group
        for part in (
            v.change.implementation_notes,
            v.change.change_description,
            v.change.reasoning,
            v.change.pseudocode_sketch,
        )
        if part
    )
    return _paths_in(repo_root, text, exclude, only_tests=False)[:_MAX_NAMED_FILES]


def _nearest_test(repo_root: Path, test_path: str, exclude: set[str]) -> str | None:
    family = _FAMILY.get(Path(test_path).suffix)
    folder = (repo_root / test_path).parent
    root = repo_root.resolve()
    for _ in range(5):  # the file's folder, then up to four parents
        if not folder.exists():
            folder = folder.parent
            continue
        candidates = []
        for p in folder.rglob("*"):
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            if not p.is_file():
                continue
            rel = p.relative_to(repo_root).as_posix()
            if rel in exclude or not is_test_path(rel) or _FAMILY.get(p.suffix) != family:
                continue
            candidates.append(rel)
            if len(candidates) >= 200:
                break
        if candidates:
            same_ext = [c for c in candidates if c.endswith(Path(test_path).suffix)]
            pool = same_ext or candidates
            return min(pool, key=lambda c: (c.count("/"), c))
        if folder.resolve() == root:
            break
        folder = folder.parent
    return None


def build_test_context(
    repo_root: Path,
    llm_groups: dict[str, list[VerifiedChange]],
    all_changes: list[VerifiedChange],
    doc_text: str | None = None,
) -> dict[str, list[dict]]:
    """{test_file_path: [{"path", "role", "content"}, ...]} for every test
    file among `llm_groups`. Files being changed in this run are never added
    as references — the LLM already sees them as files to change."""
    changing = set(llm_groups)
    claude_md = _read(repo_root, "CLAUDE.md")
    context: dict[str, list[dict]] = {}

    for test_path, group in llm_groups.items():
        if not is_test_path(test_path):
            continue
        refs: list[dict] = []
        symbols = {v.change.function_or_symbol for v in group if v.change.function_or_symbol}
        production = sorted(
            {
                v.change.file_path
                for v in all_changes
                if v.change.function_or_symbol in symbols
                and v.change.file_path
                and not is_test_path(v.change.file_path)
            }
        )
        for rel in production:
            content = _read(repo_root, rel)
            if content is not None:
                note = (
                    " — it is ALSO being changed in this same call; test the NEW behaviour "
                    "its requested_changes describe"
                    if rel in changing
                    else ""
                )
                refs.append(
                    {"path": rel, "role": f"production file under test{note}", "content": content}
                )

        exclude = changing | {r["path"] for r in refs}
        named = _named_paths(repo_root, group, exclude)
        if not named and doc_text:
            # The PDF-to-JSON step can paraphrase "How:" and drop paths; any TEST
            # file the original document names that exists in this repo is an
            # example the planner chose on purpose.
            named = _paths_in(repo_root, doc_text, exclude, only_tests=True)[:_MAX_NAMED_FILES]
        for rel in named:
            content = _read(repo_root, rel)
            if content is not None:
                refs.append(
                    {
                        "path": rel,
                        "role": "existing file named in the TDD — copy its patterns",
                        "content": content,
                    }
                )
        if not named:
            nearest = _nearest_test(repo_root, test_path, exclude)
            if nearest:
                content = _read(repo_root, nearest)
                if content is not None:
                    refs.append(
                        {
                            "path": nearest,
                            "role": "nearest existing test — copy its imports, setup and style",
                            "content": content,
                        }
                    )

        if claude_md:
            refs.append(
                {"path": "CLAUDE.md", "role": "repository conventions", "content": claude_md}
            )
        if refs:
            context[test_path] = refs
        logger.info(
            "coding_agent:   test context for %s: %s",
            test_path,
            ", ".join(f"{r['path']} ({r['role'].split(' —')[0]})" for r in refs) or "none found",
        )
    return context
