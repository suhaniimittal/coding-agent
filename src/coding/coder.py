"""The coding model's rounds for one service (2026-11-02): few calls, each
with a lot of code, like the planner's old lookup loop.

    round 1 .. LOOK_ROUNDS+1   the task + the context pack (+ what it asked
                               to see). It answers "need" (our code runs
                               every request at once and adds the results),
                               the change, or give_up. The last round must
                               give the change or give up.
    apply                      each edit names its lines by number, checked
                               against its first and last line
    checks                     code_checks.py: syntax, imports, scope, ...;
                               and every TDD open question and risk answered
    review (CODING_REVIEW)     a fresh call reads only the diff against the
                               ticket: unasked behaviour changes, unmet
                               requirements, unanswered questions
    fix (FIX_ROUNDS)           the checks' problems and the review's findings
                               together -> the model corrects them

Typical: 3 calls (look, change, review). At most LOOK_ROUNDS + 1 + review +
FIX_ROUNDS, plus one resend when an answer can't be used: 5 by default. Nothing here logs code: only
counts, paths and the model's own short fields.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field

from . import code_checks, llm, prompts
from .context_pack import Index, Pack
from .repo_tools import RepoTools, ToolError, is_test_path
from .tdd_reader import Task

MAX_LOOKUPS = 15
LOOKUP_CHARS = 30_000
LOOKUPS_TOTAL = 75_000
_READ_LINES = 1_500
_REVIEW_CONTEXT = 25  # lines of code around each change the reviewer sees
_FIX_FILE_CHARS = 130_000  # a whole "Where" file (context_pack.WHERE_FILE_CHARS), numbered
_NUMBERED = re.compile(r"^\s*\d+  ")
# A blank line as shown is " 1207  "; copied, it often loses its two spaces.
_NUMBER_ONLY = re.compile(r"^\s*\d+\s*$")


def _setting(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name) or default))
    except ValueError:
        return default


def look_rounds() -> int:
    return _setting("CODING_LOOK_ROUNDS", 1)


def fix_rounds() -> int:
    return _setting("CODING_FIX_ROUNDS", 1)


def review_enabled() -> bool:
    """CODING_REVIEW=false skips the review call (one model call less)."""
    return os.environ.get("CODING_REVIEW", "").strip().lower() not in {"0", "false", "no"}


def write_tests() -> bool:
    """CODING_WRITE_TESTS=true lets the model add and change tests; by
    default it changes source only and Jenkins runs the existing tests."""
    return os.environ.get("CODING_WRITE_TESTS", "").strip().lower() in {"1", "true", "yes"}


@dataclass
class Outcome:
    status: str  # "changed" | "gave_up" | "no_change" | "failed"
    change: dict = field(default_factory=dict)
    reason: str = ""
    problems: list[str] = field(default_factory=list)  # still open after the fix rounds
    over_limits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    calls: int = 0
    lookups: int = 0
    review: list[str] = field(default_factory=list)  # found by the review, sent to the fix


@dataclass
class _Session:
    service: str
    tools: RepoTools
    index: Index
    calls: int = 0
    lookups: int = 0
    lookup_chars: int = 0
    resent: bool = False
    planned: set[str] = field(default_factory=set)  # every path any plan named
    review: list[str] = field(default_factory=list)  # what the review call found


async def run(task: Task, service: str, tools: RepoTools, index: Index, pack: Pack) -> Outcome:
    s = _Session(service, tools, index)
    base = prompts.task_text(task, service) + "\n\n" + pack.text
    looked: list[str] = []
    rounds = look_rounds() + 1
    change: dict | None = None
    for r in range(1, rounds + 1):
        last = r == rounds
        instruction = (prompts.LAST if last else prompts.LOOK).format(round=r, rounds=rounds)
        try:
            reply = await _ask(s, _join(base, *looked, instruction), allow_need=not last)
        except llm.LlmError as e:
            return Outcome("failed", reason=str(e), calls=s.calls, lookups=s.lookups)
        if "give_up" in reply:
            return _gave_up(reply, s)
        if "need" in reply:
            results = _lookups(s, reply["need"])
            print(f"  [CODER] {service} round {r}: ran {results[1]} request(s) it asked for")
            looked.append(
                f"## What you asked to see in round {r}\nWhy: {reply.get('why') or '-'}\n\n"
                + results[0]
            )
            continue
        change = reply
        break
    if change is None:  # unreachable: the last round only accepts a change or give_up
        return Outcome("failed", reason="no change was given", calls=s.calls, lookups=s.lookups)

    problems, result = _apply_and_check(s, change)
    problems += _unanswered(task, change)
    # The review reads only the applied diff, so it runs when every edit applied;
    # what it finds goes into the same single fix round as the checks' problems.
    if review_enabled() and tools.changed() and not any(p.startswith("edit ") for p in problems):
        s.review = await _review(s, task, change)
        problems += [f"review: {f}" for f in s.review]
    for n in range(1, fix_rounds() + 1):
        if not problems:
            break
        print(f"  [CODER] {service}: {len(problems)} problem(s); fix round {n}")
        for p in problems:
            print(f"  [CODER] {service}:   {_brief(p)}")
        user = _join(
            base,
            *looked,
            "## Your change\n" + json.dumps(_without_edits(change), indent=1, ensure_ascii=False),
            "## Applied so far, as a diff\n" + tools.diff(),
            "## The files you changed, as they are now\n" + _current_files(tools),
            prompts.FIX.format(problems="\n".join(f"- {p}" for p in problems)),
        )
        try:
            reply = await _ask(s, user, allow_need=False)
        except llm.LlmError as e:
            return _changed(change, problems, result, s, note=f"the fix round failed: {e}")
        if "give_up" in reply:
            return _gave_up(reply, s)
        change = _merge(change, reply)
        problems, result = _apply_and_check(s, reply)
        problems += _unanswered(task, change)

    if not tools.changed():
        return Outcome(
            "no_change",
            change,
            "the change left every file as it was",
            calls=s.calls,
            lookups=s.lookups,
        )
    return _changed(change, problems, result, s)


def _changed(
    change: dict, problems: list[str], result: code_checks.Result, s: _Session, note: str = ""
) -> Outcome:
    notes = result.notes + ([note] if note else [])
    print(
        f"  [CODER] {s.service}: changed {len(s.tools.changed())} file(s), "
        f"confidence {change.get('confidence') or '?'}, {len(problems)} open problem(s), "
        f"{s.calls} model call(s)"
    )
    return Outcome(
        "changed", change, "", problems, result.over_limits, notes, s.calls, s.lookups, s.review
    )


def _gave_up(reply: dict, s: _Session) -> Outcome:
    reason = str(reply.get("give_up") or "no reason given")
    print(f"  [CODER] {s.service}: gave up: {reason[:300]}")
    return Outcome("gave_up", reply, reason, calls=s.calls, lookups=s.lookups)


async def _ask(s: _Session, user: str, allow_need: bool) -> dict:
    """One model call, and one resend (once per service) when the answer is
    not a usable shape."""
    reply = await _call(s, user)
    problem = _shape_problem(reply, allow_need)
    if problem and not s.resent:
        s.resent = True
        print(f"  [CODER] {s.service}: answer not usable ({problem}); asking once more")
        allowed = "A, B or C" if allow_need else "B or C"
        reply = await _call(
            s,
            f"{user}\n\n## Your previous answer could not be used\n{problem}\n"
            f"Answer again with one JSON object of shape {allowed}.",
        )
        problem = _shape_problem(reply, allow_need)
    if problem:
        raise llm.LlmError(f"the model's answer could not be used: {problem}")
    return reply


async def _call(s: _Session, user: str) -> dict:
    """The model's answer as an object. A failed call raises; an answer that
    is not JSON comes back as {"_unusable": why}, for one resend."""
    s.calls += 1
    raw = await llm.complete(prompts.system(write_tests()), user)
    try:
        return llm.parse_json(raw)
    except llm.LlmError as e:
        return {"_unusable": str(e)}


def _shape_problem(reply: dict, allow_need: bool) -> str:
    if "_unusable" in reply:
        return reply["_unusable"]
    if "give_up" in reply:
        return ""
    if "need" in reply:
        return "" if allow_need else "you can't ask to see more in this round"
    if not isinstance(reply.get("edits"), list):
        return 'it has no "edits" list'
    if not isinstance(reply.get("plan"), list):
        return 'it has no "plan" list'
    return ""


def _lookups(s: _Session, need) -> tuple[str, int]:
    """Every request in a "need", run on the folder: (their results, how many ran)."""
    need = need if isinstance(need, dict) else {}
    requests = [("read", r) for r in need.get("read") or []]
    requests += [("search", r) for r in need.get("search") or []]
    requests += [("find_files", r) for r in need.get("find_files") or []]
    requests += [("usages", r) for r in need.get("usages") or []]
    parts, ran = [], 0
    for kind, req in requests:
        if s.lookups >= MAX_LOOKUPS or s.lookup_chars >= LOOKUPS_TOTAL:
            parts.append(f"(the limit of {MAX_LOOKUPS} requests was reached; the rest did not run)")
            break
        label, text = _lookup(s, kind, req)
        if len(text) > LOOKUP_CHARS:
            text = text[:LOOKUP_CHARS] + "\n... (cut at the size limit; ask for a smaller part)"
        parts.append(f"### {kind}: {label}\n{text}")
        s.lookups += 1
        s.lookup_chars += len(text)
        ran += 1
    return "\n\n".join(parts) or "(nothing was asked for)", ran


def _lookup(s: _Session, kind: str, req) -> tuple[str, str]:
    tools = s.tools
    try:
        if kind == "read":
            path = _path(s, req.get("path") if isinstance(req, dict) else req)
            start = int(req.get("start_line") or 1) if isinstance(req, dict) else 1
            end = req.get("end_line") if isinstance(req, dict) else None
            label = f"{path} from line {start}"
            return label, tools.read_file(path, start, int(end) if end else None, _READ_LINES)
        if kind == "search":
            text = str(req.get("text") if isinstance(req, dict) else req or "")
            where = str(req.get("path") or ".") if isinstance(req, dict) else "."
            return f"{text!r} in {where}", tools.search(text, _path(s, where), case_sensitive=True)
        if kind == "find_files":
            pattern = str(req.get("pattern") if isinstance(req, dict) else req)
            return pattern, tools.find_files(pattern)
        name = str(req.get("name") if isinstance(req, dict) else req)
        return name, s.index.usages(name)
    except (ToolError, ValueError, TypeError, AttributeError) as e:
        return str(req)[:120], f"error: {e}"


def _apply_and_check(s: _Session, change: dict) -> tuple[list[str], code_checks.Result]:
    problems = []
    # Line edits are numbered on each file as the model saw it, so all of a
    # file's line edits are placed first and then applied together.
    by_line: dict[str, list[tuple[int, int, int, str]]] = {}
    failed_files: set[str] = set()
    dropped: list[str] = []
    for n, edit in enumerate(change.get("edits") or [], start=1):
        if not isinstance(edit, dict):
            problems.append(f"edit {n} is not an object")
            continue
        path = _path(s, str(edit.get("path") or ""))
        if not write_tests() and is_test_path(path):
            if path not in dropped:
                dropped.append(path)
            continue
        try:
            if "start_line" in edit:
                start = int(edit["start_line"])
                end = int(edit.get("end_line") or start)
                first = _unnumber(str(edit.get("first_line") or ""))
                last = _unnumber(str(edit.get("last_line") or ""))
                start, end = s.tools.locate_lines(path, start, end, first, last)
                new = _unnumber(str(edit.get("new") or ""))
                by_line.setdefault(path, []).append((n, start, end, new))
            elif edit.get("delete"):
                s.tools.delete_file(path)
            elif "create" in edit:
                s.tools.create_file(path, str(edit["create"]))
            else:
                _replace(s.tools, path, str(edit.get("old") or ""), str(edit.get("new") or ""))
        except (ToolError, ValueError, TypeError) as e:
            problems.append(f"edit {n} ({path}): {e}")
            if "start_line" in edit:
                failed_files.add(path)
    for path, group in by_line.items():
        numbers = ", ".join(str(n) for n, *_ in group)
        if path in failed_files:
            # Half a file's edits would leave it matching neither listing.
            problems.append(
                f"edit {numbers} ({path}): not applied, because another edit to this file "
                "failed; give all edits to this file again"
            )
            continue
        try:
            s.tools.replace_lines(path, [(a, b, new) for _, a, b, new in group])
        except ToolError as e:
            problems.append(f"edit {numbers} ({path}): {e}")
    s.planned |= {
        _path(s, str(p.get("path") or "")) for p in change.get("plan") or [] if isinstance(p, dict)
    }
    result = code_checks.run(s.tools, s.index.resolver, s.planned)
    result.notes += [f"test file not changed (tests are left to CI): {p}" for p in dropped]
    return problems + result.problems, result


def _replace(tools: RepoTools, path: str, old: str, new: str) -> None:
    """The edit as given; failing that, once more without line numbers the
    model may have copied from the shown code."""
    try:
        tools.replace_in_file(path, old, new)
    except ToolError:
        plain_old, plain_new = _unnumber(old), _unnumber(new)
        if plain_old == old:
            raise
        tools.replace_in_file(path, plain_old, plain_new)


def _unnumber(text: str) -> str:
    lines = text.split("\n")
    if not all(_NUMBERED.match(line) or _NUMBER_ONLY.match(line) for line in lines if line.strip()):
        return text
    return "\n".join(
        "" if _NUMBER_ONLY.match(line) else _NUMBERED.sub("", line, count=1) for line in lines
    )


def _path(s: _Session, path: str) -> str:
    """A path as the model gave it, made relative to the repository: without
    a leading "/" or "./", or a leading service name that is not a folder."""
    clean = (path or "").strip().strip("`").removeprefix("./").lstrip("/")
    prefix = s.service + "/"
    if clean.startswith(prefix) and not (s.tools.root / clean).exists():
        rest = clean[len(prefix) :]
        if (s.tools.root / rest).exists() or (s.tools.root / rest).parent.exists():
            return rest
    return clean or "."


def _current_files(tools: RepoTools) -> str:
    parts, size = [], 0
    for path in tools.edited():
        text = tools.text(path)
        if text is None:
            parts.append(f"### {path}\n(deleted)")
            continue
        numbered = "\n".join(f"{n:>5}  {line}" for n, line in enumerate(text.splitlines(), 1))
        if size + len(numbered) > _FIX_FILE_CHARS:
            parts.append(f"### {path}\n(too long to show again: see the diff above)")
            continue
        parts.append(f"### {path}\n{numbered}")
        size += len(numbered)
    return "\n\n".join(parts)


async def _review(s: _Session, task: Task, change: dict) -> list[str]:
    """One fresh call that reads only the applied diff against the ticket and
    returns its findings; a review that fails is noted, never fatal."""
    user = _join(
        prompts.task_text(task, s.service),
        "## The author's account of the change\n"
        + json.dumps(_without_edits(change), indent=1, ensure_ascii=False),
        f"## The diff, with {_REVIEW_CONTEXT} lines around each change\n"
        + s.tools.diff(context=_REVIEW_CONTEXT),
    )
    s.calls += 1
    try:
        reply = llm.parse_json(await llm.complete(prompts.REVIEW, user))
    except llm.LlmError as e:
        print(f"  [CODER] {s.service}: review not run ({e})")
        return []
    findings = []
    for f in reply.get("findings") or []:
        if not isinstance(f, dict) or not f.get("problem"):
            continue
        where = f"{f.get('path') or '?'}:{f.get('line') or '?'}"
        fix = f" Fix: {f['fix']}" if f.get("fix") else ""
        findings.append(f"{where}: {f['problem']}{fix}")
    print(f"  [CODER] {s.service}: review: {len(findings)} finding(s)")
    for f in findings:
        print(f"  [CODER] {s.service}:   {f.split(chr(10), 1)[0][:200]}")
    return findings


def _unanswered(task: Task, change: dict) -> list[str]:
    """The TDD's open questions and risks the change gave no answer with
    evidence for: sent to the fix round, as answering them can change the code."""
    answers = [
        a
        for a in change.get("answers") or []
        if isinstance(a, dict) and str(a.get("answer") or "").strip() and a.get("evidence")
    ]
    missing = []
    for item in [*task.open_questions, *task.risks]:
        if not any(_same_item(item, str(a.get("item") or "")) for a in answers):
            missing.append(f"open question or risk not answered with evidence: {item[:200]}")
    return missing


def _same_item(tdd_item: str, answered: str) -> bool:
    a, b = tdd_item.lower().strip(), answered.lower().strip()
    if not b:
        return False
    return a[:60] in b or b[:60] in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.5


def _brief(problem: str) -> str:
    """A problem's first line for the log, without any code it quotes."""
    first = problem.split("\n", 1)[0]
    return first.split(": remove the leftover line:", 1)[0][:200]


def _without_edits(change: dict) -> dict:
    return {k: v for k, v in change.items() if k != "edits"}


def _merge(old: dict, new: dict) -> dict:
    """The fix's answer, keeping fields of the first change it left out."""
    merged = {**old, **{k: v for k, v in new.items() if v not in (None, "", [])}}
    merged["plan"] = _union(old.get("plan"), new.get("plan"))
    return merged


def _union(a, b) -> list:
    out, seen = [], set()
    for item in [*(a or []), *(b or [])]:
        key = item.get("path") if isinstance(item, dict) else str(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _join(*parts: str) -> str:
    return "\n\n".join(p for p in parts if p)
