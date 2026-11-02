"""A file's local imports, found in its text and resolved to files on disk
(2026-11-02). Used to gather the code around the TDD's files
(context_pack.py) and to check that the model's new imports point at real
files and real exports (code_checks.py). No build and no Node: the
statements are read with patterns, the paths resolved the way TypeScript and
the bundlers do (relative paths, `tsconfig`/`jsconfig` path aliases and
baseUrl, a bare `@/` or `~/` meaning `src/`, extensions and index files).

JavaScript and TypeScript (and JSX/TSX) are covered fully; Python's relative
and package imports are resolved to modules, without checking names.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_RESOLVE_EXTENSIONS = (*JS_EXTENSIONS, ".d.ts", ".json")

# import x from '...', import { a, b as c } from '...', import type {...} from,
# export { a } from '...', export * from '...'; no quote or ";" before "from".
_JS_FROM = re.compile(
    r"""\b(?P<kw>import|export)\s+(?:type\s+)?(?P<what>[^;'"`]*?)\s*\bfrom\s*['"](?P<spec>[^'"]+)['"]"""
)
_JS_BARE = re.compile(r"""\bimport\s*['"](?P<spec>[^'"]+)['"]""")
_JS_CALL = re.compile(r"""\b(?:require|import)\s*\(\s*['"](?P<spec>[^'"]+)['"]\s*\)""")
_PY_FROM = re.compile(r"^\s*from\s+(?P<spec>\.+[\w.]*|[\w.]+)\s+import\s+(?P<what>.+)$", re.M)
_PY_IMPORT = re.compile(r"^\s*import\s+(?P<spec>[\w.]+)", re.M)

_EXPORT_DECL = re.compile(
    r"\bexport\s+(?:declare\s+)?(?:default\s+)?(?:abstract\s+)?(?:async\s+)?"
    r"(?:const|let|var|function\*?|class|interface|type|enum|namespace)\s+([A-Za-z_$][\w$]*)"
)
_EXPORT_LIST = re.compile(r"\bexport\s+(?:type\s+)?\{([^}]*)\}")
_EXPORT_DEFAULT = re.compile(r"\bexport\s+default\b")
_EXPORT_ALL = re.compile(r"\bexport\s+\*\s+from\b")


@dataclass
class Import:
    spec: str  # as written: "./Grid", "@/components/Grid", "react"
    line: int
    names: list[str] = field(default_factory=list)  # named imports, by their exported name
    default: bool = False  # `import X from` (needs a default export)


def language(path: str) -> str:
    if path.endswith(JS_EXTENSIONS):
        return "js"
    if path.endswith(".py"):
        return "py"
    return ""


def is_local(spec: str) -> bool:
    """Relative, or one of the usual source aliases. A package name is not;
    an alias from tsconfig is decided by Resolver.resolve."""
    return spec.startswith((".", "/", "@/", "~/", "src/"))


def parse(text: str, path: str) -> list[Import]:
    lang = language(path)
    if lang == "js":
        return _parse_js(text)
    if lang == "py":
        return _parse_py(text)
    return []


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _parse_js(text: str) -> list[Import]:
    out = []
    for m in _JS_FROM.finditer(text):
        what = m.group("what").strip()
        names, default = [], False
        brace = re.search(r"\{([^}]*)\}", what)
        if brace:
            for part in brace.group(1).split(","):
                part = re.sub(r"^\s*type\s+", "", part).strip()
                if part:
                    names.append(part.split(" as ")[0].strip())
        head = what[: brace.start()] if brace else what
        head = head.strip().rstrip(",").strip()
        if m.group("kw") == "import" and head and not head.startswith("*"):
            default = True
        out.append(Import(m.group("spec"), _line(text, m.start()), names, default))
    for pattern in (_JS_BARE, _JS_CALL):
        for m in pattern.finditer(text):
            out.append(Import(m.group("spec"), _line(text, m.start())))
    return out


def _parse_py(text: str) -> list[Import]:
    out = []
    for m in _PY_FROM.finditer(text):
        out.append(Import(m.group("spec"), _line(text, m.start())))
    for m in _PY_IMPORT.finditer(text):
        out.append(Import(m.group("spec"), _line(text, m.start())))
    return out


def exported_names(text: str) -> set[str] | None:
    """The names a JS/TS file exports ("default" for a default export), or
    None when it re-exports everything from another file (`export * from`),
    so its names can't be known from its own text."""
    if _EXPORT_ALL.search(text):
        return None
    names = set(_EXPORT_DECL.findall(text))
    for group in _EXPORT_LIST.findall(text):
        for part in group.split(","):
            part = re.sub(r"^\s*type\s+", "", part).strip()
            if part:
                names.add(part.split(" as ")[-1].strip())
    if _EXPORT_DEFAULT.search(text):
        names.add("default")
    return names


class Resolver:
    """Resolves an import to a file of the repository, or None (a package,
    or a path that does not exist)."""

    def __init__(self, files: list[str], read) -> None:
        """`files`: every path in the repository. `read(path) -> str | None`."""
        self.files = set(files)
        self._dirs = {str(PurePosixPath(f).parent) for f in files}
        self.aliases: list[tuple[str, list[str]]] = []  # ("@/", ["src/"]), longest first
        self.base_urls: list[str] = []
        self._load_configs(read)

    def resolve(self, from_file: str, spec: str) -> str | None:
        if language(from_file) == "py":
            return self._resolve_py(from_file, spec)
        if spec.startswith("."):
            base = str(PurePosixPath(from_file).parent / spec)
            return self._file(_normalize(base))
        for prefix, targets in self.aliases:
            if spec == prefix.rstrip("/") or spec.startswith(prefix):
                rest = spec[len(prefix) :]
                for target in targets:
                    found = self._file(_normalize(target + rest))
                    if found:
                        return found
        if spec.startswith(("@/", "~/")):
            found = self._file(_normalize("src/" + spec[2:]))
            if found:
                return found
        for base in self.base_urls:
            found = self._file(_normalize(f"{base}/{spec}" if base else spec))
            if found:
                return found
        return None

    def _file(self, base: str) -> str | None:
        if base in self.files:
            return base
        for ext in _RESOLVE_EXTENSIONS:
            if base + ext in self.files:
                return base + ext
        if base in self._dirs:
            for ext in _RESOLVE_EXTENSIONS:
                if f"{base}/index{ext}" in self.files:
                    return f"{base}/index{ext}"
        return None

    def _resolve_py(self, from_file: str, spec: str) -> str | None:
        if spec.startswith("."):
            dots = len(spec) - len(spec.lstrip("."))
            base = PurePosixPath(from_file).parent
            for _ in range(dots - 1):
                base = base.parent
            rest = spec.lstrip(".").replace(".", "/")
            root = str(base / rest) if rest else str(base)
        else:
            root = spec.replace(".", "/")
        for candidate in (f"{root}.py", f"{root}/__init__.py", f"src/{root}.py"):
            if _normalize(candidate) in self.files:
                return _normalize(candidate)
        return None

    def _load_configs(self, read) -> None:
        """Path aliases and baseUrl from every tsconfig/jsconfig in the repo
        (a monorepo has one per package), nearest the root first."""
        configs = sorted(
            (
                f
                for f in self.files
                if PurePosixPath(f).name in ("tsconfig.json", "jsconfig.json")
                or re.fullmatch(r"tsconfig\.[\w.-]+\.json", PurePosixPath(f).name)
            ),
            key=lambda f: (f.count("/"), f),
        )
        aliases: dict[str, list[str]] = {}
        for config in configs[:20]:
            data = _jsonc(read(config) or "")
            options = data.get("compilerOptions") if isinstance(data, dict) else None
            if not isinstance(options, dict):
                continue
            folder = str(PurePosixPath(config).parent)
            folder = "" if folder == "." else folder
            base = (
                _normalize(f"{folder}/{options['baseUrl']}") if options.get("baseUrl") else folder
            )
            if options.get("baseUrl") is not None and base not in self.base_urls:
                self.base_urls.append(base)
            for key, targets in (options.get("paths") or {}).items():
                if not isinstance(targets, list):
                    continue
                prefix = key.removesuffix("*")
                resolved = [
                    _normalize(f"{base}/{t.removesuffix('*')}") + ("/" if t.endswith("/*") else "")
                    for t in targets
                    if isinstance(t, str)
                ]
                aliases.setdefault(prefix, []).extend(resolved)
        self.aliases = sorted(aliases.items(), key=lambda kv: -len(kv[0]))


def _normalize(path: str) -> str:
    parts: list[str] = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _jsonc(text: str) -> dict:
    """JSON with comments and trailing commas, as tsconfig files are written."""
    out, i, in_string = [], 0, False
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    clean = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
