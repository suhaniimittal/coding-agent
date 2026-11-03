"""The coding model's five calls for one service (2026-11-03). Each call has
one job; between them our code checks what it can without a model:

    1 understand   the task + the context pack -> the checklist (what must
                   be true, each item covering requirements R1.. and test-
                   plan items T1..), where the code decides it, the answers,
                   what else it needs ("need": our code runs it, at most 15)
      code         any R or T no item covers becomes an item of its own
    2 write        the task, the checklist and the kept files -> the change,
                   with "met_by": the edits behind each item
      code         apply (a uniform indentation shift is removed); checks:
                   syntax, imports, scope, ... (code_checks.py), every
                   question answered, every item met by an edit that changed
                   a file
    3 verify       a fresh call: the checklist, the code before and after,
                   who uses it; pass / fail / unsure per item, a pass quoting
                   its line. A quote not in the file is a fail. Everything
                   passed and nothing found: done, 3 calls.
    4 fix          only what failed -> edits
      code         apply and the same checks
    5 verify       the whole checklist again, on the final code

At most MAX_CALLS calls, a resend of an unusable answer included: whatever
is still open when they are spent goes to the pull request, which is then a
draft. Nothing here logs code: only counts, paths and the model's own short
fields.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import time
from dataclasses import dataclass, field

from . import code_checks, context_pack, llm, prompts
from .context_pack import Index, Pack
from .repo_tools import RepoTools, ToolError, is_test_path
from .tdd_reader import Task

MAX_CALLS = 5
MAX_LOOKUPS = 15
LOOKUP_CHARS = 30_000
LOOKUPS_TOTAL = 75_000
_READ_LINES = 1_500
_VERIFY_CONTEXT = 25  # lines of code around each change the check sees
_VERIFY_CHARS = 120_000  # the before/after listings, all files
_USERS_PER_FILE = 20
_KEEP_FILES = 15  # files the model may keep after its first call
# How long the model thinks per call (OpenAI; the gateway ignores it): the
# change, its fix and the check of it need the most.
_EFFORT = {"understand": "medium", "change": "high", "verify": "high", "fix": "high"}
_FIX_FILE_CHARS = 130_000  # a whole "Where" file (context_pack.WHERE_FILE_CHARS), numbered
_NUMBERED = re.compile(r"^\s*\d+  ")
# A blank line as shown is " 1207  "; copied, it often loses its two spaces.
_NUMBER_ONLY = re.compile(r"^\s*\d+\s*$")
_ITEM_ID = re.compile(r"^[RT]\d+$")


def write_tests() -> bool:
    """CODING_WRITE_TESTS=true lets the model add and change tests; by
    default it changes source only and Jenkins runs the existing tests."""
    return os.environ.get("CODING_WRITE_TESTS", "").strip().lower() in {"1", "true", "yes"}


@dataclass
class Outcome:
    status: str  # "changed" | "gave_up" | "no_change" | "failed"
    change: dict = field(default_factory=dict)
    reason: str = ""
    problems: list[str] = field(default_factory=list)  # still open after the fix
    over_limits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    calls: int = 0
    lookups: int = 0
    review: list[str] = field(default_factory=list)  # found by the first check, sent to the fix
    # Each item: "id", "check", "covers", "kind", "where", and once checked
    # "verdict" ("pass" | "fail" | "unsure"), "why", "at" (path:line of a pass).
    checklist: list[dict] = field(default_factory=list)
    verified: bool = False  # the final code is the code the last check judged


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
    review: list[str] = field(default_factory=list)  # what the first check found
    kept: list[str] = field(default_factory=list)  # files the model kept after call 1
    checklist: list[dict] = field(default_factory=list)
    met_by: dict[str, tuple[dict, list]] = field(default_factory=dict)  # id -> (entry, its edits)
    verified_diff: str | None = None  # the diff the last check judged
    notes: list[str] = field(default_factory=list)


async def run(task: Task, service: str, tools: RepoTools, index: Index, pack: Pack) -> Outcome:
    s = _Session(service, tools, index)
    intro = prompts.task_text(task, service)
    full = intro + "\n\n" + pack.text

    # 1. understand: the whole starter pack, once.
    try:
        found = await _ask(
            s, prompts.UNDERSTAND, _join(full, prompts.UNDERSTAND_STEP), "understand", _understood
        )
    except llm.LlmError as e:
        return Outcome("failed", reason=str(e), calls=s.calls, lookups=s.lookups)
    if "give_up" in found:
        return _gave_up(found, s)
    s.checklist = _checklist(task, found, s)
    s.kept = _kept(s, found.get("keep"))
    looked = ""
    if found.get("need"):
        results, ran = _lookups(s, found["need"])
        looked = "## What you asked to see\n\n" + results
        print(f"  [CODER] {service}: ran {ran} request(s) it asked for, kept {len(s.kept)} file(s)")
    understood = _join(_checklist_text(s.checklist), _found_text(found))
    # Later calls carry the files the model kept, not the whole pack.
    base = _focused(s, intro) if s.kept else full

    # 2. write.
    try:
        change = await _ask(
            s,
            prompts.system(write_tests()),
            _join(base, looked, understood, prompts.WRITE_STEP),
            "change",
            _a_change,
        )
    except llm.LlmError as e:
        return Outcome(
            "failed", reason=str(e), calls=s.calls, lookups=s.lookups, checklist=s.checklist
        )
    if "give_up" in change:
        return _gave_up(change, s)
    change["answers"] = _union_answers(found.get("answers"), change.get("answers"))
    problems, result = _apply_and_check(s, change)
    problems += _unanswered(task, change) + _unmet(s, change)

    # 3. verify: only a change whose every edit applied can be judged.
    edits_failed = any(p.startswith("edit ") for p in problems)
    if tools.changed() and not edits_failed:
        failed = await _verify(s, intro, problems)
        if failed is not None:
            s.review = failed
            problems += failed
            if not problems:
                return _changed(change, [], result, s)

    if not tools.changed() and not edits_failed:
        return Outcome(
            "no_change",
            change,
            "the change left every file as it was",
            calls=s.calls,
            lookups=s.lookups,
            checklist=s.checklist,
        )
    if not problems:  # the check could not run, and nothing else was found
        return _changed(change, [], result, s)

    # 4. fix: only what is open.
    print(f"  [CODER] {service}: {len(problems)} problem(s); fix")
    for p in problems:
        print(f"  [CODER] {service}:   {_brief(p)}")
    # Edited files are shown as they are now below, so not again as kept.
    base = _focused(s, intro, skip=set(tools.edited())) if s.kept else full
    user = _join(
        base,
        looked,
        _checklist_text(s.checklist),
        "## Your change\n" + json.dumps(_without_edits(change), indent=1, ensure_ascii=False),
        "## Applied so far, as a diff\n" + tools.diff(),
        "## The files you changed, as they are now\n" + _current_files(tools),
        prompts.FIX.format(problems="\n".join(f"- {p}" for p in problems)),
    )
    try:
        reply = await _ask(s, prompts.system(write_tests()), user, "fix", _a_fix)
    except llm.LlmError as e:
        return _changed(change, problems, result, s, note=f"the fix call failed: {e}")
    if "give_up" in reply:
        reason = str(reply.get("give_up") or "no reason given")
        return _changed(change, problems, result, s, note=f"the fix was given up: {reason}")
    change = _merge(change, reply)
    problems, result = _apply_and_check(s, reply)
    problems += _unanswered(task, change) + _unmet(s, reply)

    # 5. verify again: the whole checklist, on the final code.
    if tools.changed() and not any(p.startswith("edit ") for p in problems):
        failed = await _verify(s, intro, problems)
        problems += failed or []
    if not tools.changed():
        return Outcome(
            "no_change",
            change,
            "the change left every file as it was",
            calls=s.calls,
            lookups=s.lookups,
            checklist=s.checklist,
        )
    return _changed(change, problems, result, s)


def _changed(
    change: dict, problems: list[str], result: code_checks.Result, s: _Session, note: str = ""
) -> Outcome:
    notes = result.notes + s.notes + ([note] if note else [])
    verified = s.verified_diff is not None and s.verified_diff == s.tools.diff()
    passed = sum(1 for i in s.checklist if i.get("verdict") == "pass")
    print(
        f"  [CODER] {s.service}: changed {len(s.tools.changed())} file(s), "
        f"{passed}/{len(s.checklist)} item(s) passed, {len(problems)} open problem(s), "
        f"{'verified' if verified else 'NOT verified'}, {s.calls} model call(s)"
    )
    return Outcome(
        "changed",
        change,
        "",
        problems,
        result.over_limits,
        notes,
        s.calls,
        s.lookups,
        s.review,
        s.checklist,
        verified,
    )


def _gave_up(reply: dict, s: _Session) -> Outcome:
    reason = str(reply.get("give_up") or "no reason given")
    print(f"  [CODER] {s.service}: gave up: {reason[:300]}")
    return Outcome(
        "gave_up", reply, reason, calls=s.calls, lookups=s.lookups, checklist=s.checklist
    )


async def _ask(s: _Session, system: str, user: str, step: str, shape) -> dict:
    """One model call, and one resend (once per service, within the budget)
    when `shape` finds the answer unusable."""
    reply = await _call(s, system, user, step)
    problem = shape(reply)
    if problem and not s.resent and s.calls < MAX_CALLS:
        s.resent = True
        print(f"  [CODER] {s.service}: answer not usable ({problem}); asking once more")
        reply = await _call(
            s,
            system,
            f"{user}\n\n## Your previous answer could not be used\n{problem}\n"
            "Answer again with one JSON object of the shape asked for.",
            step,
        )
        problem = shape(reply)
    if problem:
        raise llm.LlmError(f"the model's answer could not be used: {problem}")
    return reply


async def _call(s: _Session, system: str, user: str, step: str) -> dict:
    """The model's answer as an object. A failed call, or one past the
    budget, raises; an answer that is not JSON comes back as
    {"_unusable": why}, for one resend."""
    if s.calls >= MAX_CALLS:
        raise llm.LlmError(f"the {MAX_CALLS} model calls are spent")
    s.calls += 1
    started = time.monotonic()
    raw = await llm.complete(system, user, effort=_EFFORT[step])
    _timed(s, step, started, len(user))
    try:
        return llm.parse_json(raw)
    except llm.LlmError as e:
        return {"_unusable": str(e)}


def _understood(reply: dict) -> str:
    if "_unusable" in reply:
        return reply["_unusable"]
    if "give_up" in reply:
        return ""
    if not isinstance(reply.get("checklist"), list) or not reply["checklist"]:
        return 'it has no "checklist" list'
    return ""


def _a_fix(reply: dict) -> str:
    if "_unusable" in reply:
        return reply["_unusable"]
    if "give_up" in reply:
        return ""
    if "need" in reply:
        return "you can't ask to see more now"
    if not isinstance(reply.get("edits"), list):
        return 'it has no "edits" list'
    if not isinstance(reply.get("plan"), list):
        return 'it has no "plan" list'
    return ""


def _a_change(reply: dict) -> str:
    problem = _a_fix(reply)
    if problem or "give_up" in reply:
        return problem
    if not isinstance(reply.get("met_by"), list):
        return 'it has no "met_by" list'
    return ""


def _verdicts(reply: dict) -> str:
    if "_unusable" in reply:
        return reply["_unusable"]
    if not isinstance(reply.get("items"), list):
        return 'it has no "items" list'
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
            s.tools.replace_lines(path, _reindented(s, path, group))
        except ToolError as e:
            problems.append(f"edit {numbers} ({path}): {e}")
    s.planned |= {
        _path(s, str(p.get("path") or "")) for p in change.get("plan") or [] if isinstance(p, dict)
    }
    result = code_checks.run(s.tools, s.index.resolver, s.planned)
    result.notes += [f"test file not changed (tests are left to CI): {p}" for p in dropped]
    return problems + result.problems, result


def _reindented(
    s: _Session, path: str, group: list[tuple[int, int, int, str]]
) -> list[tuple[int, int, str]]:
    """A file's line edits as (start, end, new), each without a shift the
    model added to all of it (_reindent). An edit next to another is left as
    it is: together they may wrap code in a new block on purpose."""
    lines = (s.tools.text(path) or "").split("\n")
    spans = [(a, b) for _, a, b, _ in group]
    out = []
    for n, a, b, new in group:
        alone = not any(b2 == a - 1 or a2 == b + 1 for a2, b2 in spans)
        if alone:
            new, shift = _reindent(new, lines[a - 1 : b])
            if shift:
                s.notes.append(
                    f"edit {n} ({path}): every line sat {shift} space(s) deeper than the lines "
                    "it replaced; the shift was removed"
                )
        out.append((a, b, new))
    return out


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


def _timed(s: _Session, step: str, started: float, prompt_chars: int) -> None:
    secs = time.monotonic() - started
    print(
        f"  [CODER] {s.service}: call {s.calls} ({step}): {secs:.0f} s, "
        f"prompt {prompt_chars // 1000}k chars"
    )


def _kept(s: _Session, keep) -> list[str]:
    """The files the model kept in call 1: real files only, at most _KEEP_FILES."""
    out = []
    for p in keep if isinstance(keep, list) else []:
        path = _path(s, str(p or ""))
        if path not in out and s.tools.text(path) is not None:
            out.append(path)
    return out[:_KEEP_FILES]


def _focused(s: _Session, intro: str, skip: set[str] | None = None) -> str:
    """The task and the kept files, whole: the prompt after call 1, in place
    of the whole starter pack."""
    blocks, size = [], 0
    for path in s.kept:
        if path in (skip or set()):
            continue
        block = context_pack.file_block(s.tools, path, "you kept it")
        if block is None or size + len(block) > context_pack.PACK_CHARS:
            continue
        blocks.append(block)
        size += len(block)
    head = "## The files you kept (line numbers are not part of the files)\n\n"
    return _join(intro, head + "\n\n".join(blocks) if blocks else "")


def _unanswered(task: Task, change: dict) -> list[str]:
    """The TDD's risks and the open questions the code can answer ("planner")
    the change gave no answer with evidence for: sent to the fix, as
    answering them can change the code. Questions a person decides ("card")
    are not the change's to answer."""
    answers = [
        a
        for a in change.get("answers") or []
        if isinstance(a, dict) and str(a.get("answer") or "").strip() and a.get("evidence")
    ]
    missing = []
    for item in [*(q.question for q in task.code_questions()), *task.risks]:
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
    merged["answers"] = _union_answers(old.get("answers"), new.get("answers"))
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


# --- the checklist and its check ------------------------------------------------------------


def _checklist(task: Task, found: dict, s: _Session) -> list[dict]:
    """Call 1's checklist, numbered C1, C2, ... by our code. A requirement or
    test-plan item no item covers becomes an item of its own, in its own
    words, so nothing the TDD asks for goes unchecked."""
    items: list[dict] = []
    for raw in found.get("checklist") or []:
        if not isinstance(raw, dict) or not str(raw.get("check") or "").strip():
            continue
        covers = [str(c).strip().upper() for c in raw.get("covers") or []]
        items.append(
            {
                "id": f"C{len(items) + 1}",
                "check": str(raw["check"]).strip(),
                "covers": [c for c in covers if _ITEM_ID.match(c)],
                "kind": "unchanged" if raw.get("kind") == "unchanged" else "change",
                "where": str(raw.get("where") or "").strip(),
            }
        )
    covered = {c for item in items for c in item["covers"]}
    missing = [(key, text) for key, text in prompts.item_ids(task).items() if key not in covered]
    for key, text in missing:
        item_id = f"C{len(items) + 1}"
        items.append({"id": item_id, "check": text, "covers": [key], "kind": "change", "where": ""})
    if missing:
        note = "checklist: added what no item covered: " + ", ".join(k for k, _ in missing)
        print(f"  [CODER] {s.service}: {note}")
        s.notes.append(note)
    return items


def _checklist_text(checklist: list[dict]) -> str:
    if not checklist:
        return ""
    lines = ["## The checklist (what must be true when the change is done)"]
    for item in checklist:
        covers = f" covers {', '.join(item['covers'])}" if item["covers"] else ""
        where = f" Where: {item['where']}" if item["where"] else ""
        lines.append(f"- {item['id']} [{item['kind']}{covers}]: {item['check']}{where}")
    return "\n".join(lines)


def _found_text(found: dict) -> str:
    """What call 1 found besides the checklist, for the change."""
    lines = ["## What call 1 found", f"Cause: {found.get('cause') or '-'}"]
    places = [
        f"- {p['path']}: {p.get('what') or ''}" + (f" Why: {p['why']}" if p.get("why") else "")
        for p in found.get("locations") or []
        if isinstance(p, dict) and p.get("path")
    ]
    answers = [
        f"- {a['item']}: {a.get('answer') or '-'} ({a.get('evidence') or 'no evidence given'})"
        for a in found.get("answers") or []
        if isinstance(a, dict) and a.get("item")
    ]
    doubts = [f"- {d}" for d in found.get("doubts") or [] if d]
    for title, rows in (("Where to change:", places), ("Answers so far:", answers)):
        if rows:
            lines += [title, *rows]
    if doubts:
        lines += ["Doubts:", *doubts]
    return "\n".join(lines)


def _unmet(s: _Session, change: dict) -> list[str]:
    """The "change" items that no edit which changed a file, and no quoted
    line that is in the code, makes true. The answer's "met_by" entries
    replace earlier ones for the same item (the fix's count its own edits)."""
    edits = change.get("edits") if isinstance(change.get("edits"), list) else []
    for entry in change.get("met_by") or []:
        if isinstance(entry, dict) and entry.get("item"):
            s.met_by[str(entry["item"]).strip().upper()] = (entry, edits)
    changed = set(s.tools.changed())
    missing = []
    for item in s.checklist:
        if item["kind"] != "change":
            continue
        entry, its_edits = s.met_by.get(item["id"], (None, []))
        if entry is None:
            missing.append(
                f"{item['id']} has no edit in met_by that makes it true: {item['check']}"
            )
            continue
        numbers = [int(n) for n in entry.get("edits") or [] if str(n).isdigit()]
        paths = {
            _path(s, str(its_edits[n - 1].get("path") or ""))
            for n in numbers
            if 1 <= n <= len(its_edits) and isinstance(its_edits[n - 1], dict)
        }
        if paths & changed:
            continue
        already = str(entry.get("already") or "").split(":", 1)[0]
        if entry.get("quote") and _quoted(s, already, str(entry["quote"])):
            continue
        missing.append(
            f"{item['id']} is not met: met_by names no edit that changed a file and no line in "
            f"the code that already makes it true: {item['check']}"
        )
    return missing


async def _verify(s: _Session, intro: str, problems: list[str]) -> list[str] | None:
    """A check call (3 or 5) on the code as it is now: sets each item's
    verdict, and returns what failed and the findings, as problems for the
    fix. None when no check could run (the budget is spent, or no usable
    answer came back)."""
    found = "\n".join(f"- {p}" for p in problems) or "- nothing"
    user = _join(
        intro,
        _checklist_text(s.checklist),
        "## What the automatic checks found\n" + found,
        "## The change, file by file\n" + _before_after(s.tools),
        _users(s),
        prompts.VERIFY_STEP,
    )
    diff = s.tools.diff()
    try:
        reply = await _ask(s, prompts.VERIFY, user, "verify", _verdicts)
    except llm.LlmError as e:
        print(f"  [CODER] {s.service}: check not run ({e})")
        return None
    s.verified_diff = diff
    by_id = {
        str(v.get("id") or "").strip().upper(): v for v in reply["items"] if isinstance(v, dict)
    }
    failed = []
    for item in s.checklist:
        v = by_id.get(item["id"]) or {}
        verdict, why = v.get("verdict"), str(v.get("why") or "").strip()
        item.pop("at", None)
        if verdict == "pass":
            path = str(v.get("path") or "")
            if _quoted(s, path, str(v.get("quote") or "")):
                item["at"] = f"{_path(s, path)}:{v.get('line') or '?'}" if path else ""
            else:
                verdict = "fail"
                why = f"its pass quotes no line that is in the code ({why or 'no trace given'})"
        elif verdict not in ("fail", "unsure"):
            verdict, why = "fail", "the check gave it no verdict"
        item["verdict"], item["why"] = verdict, why
        if verdict == "fail":
            failed.append(f"{item['id']} not met: {item['check']} ({why or 'no reason given'})")
    dropped = 0
    for f in reply.get("findings") or []:
        if isinstance(f, dict) and f.get("problem") and f.get("fix") and f.get("path"):
            failed.append(f"{f['path']}:{f.get('line') or '?'}: {f['problem']} Fix: {f['fix']}")
        else:
            dropped += 1
    counts = {k: 0 for k in ("pass", "fail", "unsure")}
    for item in s.checklist:
        counts[item["verdict"]] += 1
    print(
        f"  [CODER] {s.service}: check: {counts['pass']} pass, {counts['fail']} fail, "
        f"{counts['unsure']} unsure; {len(failed) - counts['fail']} finding(s)"
        + (f", {dropped} without a place or a fix dropped" if dropped else "")
    )
    return failed


def _quoted(s: _Session, path: str, quote: str) -> bool:
    """Whether `quote` is in the code (spaces aside): in the file at `path`,
    else in any file the change edited. Too short a quote (a lone "}") never
    counts."""
    lines = [_squash(line) for line in _unnumber(quote).split("\n") if line.strip()]
    if sum(len(line) for line in lines) < 10:
        return False
    candidates = [_path(s, path)] if path.strip() else []
    candidates += [p for p in s.tools.edited() if p not in candidates]
    for candidate in candidates:
        text = s.tools.text(candidate) if (s.tools.root / candidate).is_file() else None
        if text is None:
            continue
        squashed = "\n".join(_squash(line) for line in text.split("\n"))
        if all(line in squashed for line in lines):
            return True
    return False


def _squash(line: str) -> str:
    return re.sub(r"\s+", "", line)


def _before_after(tools: RepoTools) -> str:
    """Every changed file's code before and after, numbered, around each
    change: what the check judges, with the line numbers it quotes."""
    parts, size = [], 0
    for path in tools.changed():
        before, after = tools.before(path), tools.text(path)
        if after is None:
            block = f"### {path}: deleted"
        elif before is None:
            new = after.split("\n")
            block = f"### {path}: a new file\nAFTER:\n" + _numbered(new, [(0, len(new))])
        else:
            old, new = "\n".join(before.splitlines()).split("\n"), after.split("\n")
            ops = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
            ops = [op for op in ops if op[0] != "equal"]
            block = (
                f"### {path}\nBEFORE:\n{_numbered(old, [(i1, i2) for _, i1, i2, _, _ in ops])}"
                f"\n\nAFTER:\n{_numbered(new, [(j1, j2) for _, _, _, j1, j2 in ops])}"
            )
        if size + len(block) > _VERIFY_CHARS:
            block = f"### {path}: too long to show here"
        parts.append(block)
        size += len(block)
    return "\n\n".join(parts) or "(no file changed)"


def _numbered(lines: list[str], spans: list[tuple[int, int]]) -> str:
    """`lines` numbered from 1, only _VERIFY_CONTEXT lines either side of
    each span (0-based, end excluded); "..." where lines are left out."""
    windows: list[tuple[int, int]] = []
    for a, b in spans:
        lo, hi = max(0, a - _VERIFY_CONTEXT), min(len(lines), max(b, a + 1) + _VERIFY_CONTEXT)
        if windows and lo <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], hi))
        else:
            windows.append((lo, hi))
    out, last = [], 0
    for lo, hi in windows:
        if lo > last:
            out.append("  ...")
        out += [f"{n + 1:>5}  {lines[n]}" for n in range(lo, hi)]
        last = hi
    if last < len(lines):
        out.append("  ...")
    return "\n".join(out)


def _users(s: _Session) -> str:
    rows = []
    for path in s.tools.changed():
        users = s.index.importers(path)
        if users:
            rows.append(f"{path} is imported by {len(users)} file(s):")
            rows += [f"  {f}:{n}: {line}" for f, n, line in users[:_USERS_PER_FILE]]
    return "## Who uses them\n" + "\n".join(rows) if rows else ""


def _reindent(new: str, old: list[str]) -> tuple[str, int]:
    """`new` without a shift the model added to all of it: it takes the two
    spaces after a shown line number for indentation (KS-17531: every block
    it replaced moved two spaces right). When every line of `new` sits deeper
    than the least indented line it replaces, the difference is removed.
    (the text, the spaces removed)."""

    def depth(line: str) -> int:
        return len(line) - len(line.lstrip(" \t"))

    lines = new.split("\n")
    was = [depth(line) for line in old if line.strip()]
    now = [depth(line) for line in lines if line.strip()]
    if not was or not now or min(now) <= min(was):
        return new, 0
    shift = min(now) - min(was)
    return "\n".join(line[shift:] if line.strip() else line for line in lines), shift


def _union_answers(first, later) -> list:
    """Both answers lists, the later answer kept for the same item."""
    out, seen = [], set()
    for a in [*(later or []), *(first or [])]:
        if isinstance(a, dict):
            key = str(a.get("item") or "").strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(a)
    return out
