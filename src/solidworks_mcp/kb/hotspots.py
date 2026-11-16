"""fixes/HOTSPOTS.md: the one file for fix agents, and the commands that keep it in step.

Holds every issue (open, parked, fixed) with the jobs and sessions it was seen in, the
measured cost of the last 10 jobs, and what fixed the closed ones. The server writes all
of it; Claude contributes explanations and fix ideas through kb_explain_digest, and
`solidworks-mcp kb-fixed` moves an issue to Fixed and keeps the digests and tool cards
in step. A Fixed entry is never rewritten. Template: Docs/kb-templates/HOTSPOTS.md;
design: Docs/KNOWLEDGE-BASE.md §10.4-§10.5.
"""

import re

from ..errors import SolidWorksError
from . import digest
from .files import atomic_write, read_text

MAX_OPEN = 20
LAST_JOBS = 10
MINUTE_TOKENS = 1000   # one minute of a job weighs as much as this many tokens when issues are ranked
FIX_IDEA_WORDS = 60
INTRO = ("Everything a fix session needs: where the effort goes, every open\n"
         "issue with the jobs and sessions it came from, and what fixed the closed ones.\n"
         'Close an issue with `solidworks-mcp kb-fixed H-00n --by "…" --commit … --note "…"`, not by hand.\n'
         "Each job's full detail: fixes\\DN<n>\\digest.md (transcript path inside).")
PARKED_NONE = "(none: parked when more than 20 are open, cheapest first)"

_HEAD = re.compile(r"^- \[([ x])\] \*\*(H-\d+)\*\* (.*)$")
_SAME_AS = re.compile(r"same as (H-\d+)", re.IGNORECASE)


# --- issues: parse and render ---------------------------------------------------------------


def _job_key(job: str) -> tuple:
    found = re.fullmatch(r"DN(\d+)(?:~(\d+))?", job)
    return (-int(found.group(1)), int(found.group(2) or 0)) if found else (0, 0)


def cost_text(minutes, tokens) -> str:
    if minutes is None:
        return "not measured"
    parts = []
    if tokens:
        parts.append(f"~{round(tokens):,} tok")
    if minutes >= 0.5 or not tokens:
        parts.append(f"{round(minutes) if minutes >= 1 else round(minutes, 1)} min")
    return " + ".join(parts) + " per job"


def _parse_cost(text: str) -> tuple:
    tokens = re.search(r"~?([\d,]+) tok", text)
    minutes = re.search(r"([\d.]+) min", text)
    if not tokens and not minutes:
        return None, None
    return (float(minutes.group(1)) if minutes else 0.0), (int(tokens.group(1).replace(",", "")) if tokens else 0)


def _seen_text(entry: dict) -> str:
    session = f"`{entry['session']}`" if entry.get("session") else "—"
    return " ".join([entry["job"], session, *entry.get("parts", [])])


def _parse_seen(text: str) -> list:
    found = []
    for item in text.split(" · "):
        match = re.fullmatch(r"(\S+) (?:`([^`]+)`|—)((?: D\d+)*)", item.strip())
        if match:
            found.append({"job": match.group(1), "session": match.group(2), "parts": match.group(3).split()})
    return found


def _score(issue: dict) -> float:
    if issue["minutes"] is None:
        return 0.0
    return len(issue["seen"]) * (issue["tokens"] + MINUTE_TOKENS * issue["minutes"])


def render_issue(issue: dict, parked: bool = False) -> str:
    if issue["status"] == "fixed":
        return "\n".join(issue["lines"])
    tags = [" ".join(f"`{s}`" for s in issue["signatures"])] if issue["signatures"] else []
    if issue.get("again_of"):
        tags.append(f"again after {issue['again_of']}")
    head = f"- [ ] **{issue['id']}** {issue['title']}" + "".join(f" · {t}" for t in tags)
    seen = " · ".join(_seen_text(e) for e in sorted(issue["seen"], key=lambda e: _job_key(e["job"])))
    lines = [head, f"  - Cost: {cost_text(issue['minutes'], issue['tokens'])} · Seen: {seen or '—'}"]
    lines += [f"  - Fix idea (Claude, {job}): {text}" for job, text in issue["ideas"]]
    return "\n".join(lines)


def _parse_issue(match, lines: list) -> dict:
    status = "fixed" if match.group(1) == "x" else "open"
    title, *tags = match.group(3).split(" · ")
    issue = {"id": match.group(2), "status": status, "title": title, "signatures": [], "again_of": None,
             "seen": [], "ideas": [], "minutes": None, "tokens": 0, "lines": lines}
    for tag in tags:
        if tag.startswith("again after "):
            issue["again_of"] = tag[len("again after "):].strip()
        else:
            issue["signatures"] += re.findall(r"`([^`]+)`", tag)
    for line in lines[1:]:
        cost = re.match(r"\s+- Cost: (.*?) · Seen: (.*)$", line)
        idea = re.match(r"\s+- Fix idea \(Claude, (\S+)\): (.*)$", line)
        fixed = re.match(r"\s+- Fixed (\d{4}-\d{2}-\d{2}) in (\S+) by: (.*)$", line)
        note = re.match(r"\s+- If it happens again: (.*)$", line)
        if cost:
            issue["minutes"], issue["tokens"] = _parse_cost(cost.group(1))
            issue["seen"] = _parse_seen(cost.group(2))
        elif idea:
            issue["ideas"].append((idea.group(1), idea.group(2)))
        elif fixed:
            issue["fixed"], issue["commit"], issue["by"] = fixed.group(1), fixed.group(2), fixed.group(3)
        elif note:
            issue["note"] = note.group(1)
    return issue


def parse(text: str) -> dict:
    """{issues, updated, after}: every issue of an existing HOTSPOTS.md, in the order written."""
    issues, head, lines = [], None, []
    for line in text.splitlines() + [""]:
        found = _HEAD.match(line)
        if found or line.startswith("## ") or not line.startswith(" "):
            if head is not None:
                issues.append(_parse_issue(head, lines))
            head, lines = found, [line]
        elif head is not None:
            lines.append(line)
    stamp = re.search(r"^Updated (\d{4}-\d{2}-\d{2})(?: after (\S+?))?\.", text, re.M)
    return {"issues": issues, "updated": stamp.group(1) if stamp else None, "after": stamp.group(2) if stamp else None}


def load(kb) -> list:
    path = kb.hotspots_file
    return parse(read_text(path))["issues"] if path.is_file() else []


# --- the measured table and the jobs behind it -------------------------------------------------


def jobs(kb) -> list:
    """Every job with a digest: {job, day, digest, rows, sessions}, newest last."""
    found = []
    for job in kb.jobs():
        parsed = digest.read_digest(kb, job)
        if parsed is None:
            continue
        day, rows = kb.read_timeline(job)
        found.append({"job": job, "day": parsed["date"] or day, "digest": parsed, "rows": rows,
                      "sessions": kb.meta(job)["sessions"]})
    return sorted(found, key=lambda j: (j["day"], tuple(-v for v in _job_key(j["job"]))))


def session_of(job: dict, call: int) -> str | None:
    found = None
    for entry in sorted(job["sessions"], key=lambda s: s["from_call"]):
        if entry["from_call"] <= call:
            found = entry["session_id"][:8]
    return found


def _quick_cost(rows: list, numbers: list) -> tuple:
    chosen = [r for r in rows if r["n"] in numbers]
    minutes = sum(r["run"] + (0 if r.get("wait") else r["pause"]) for r in chosen) / 60
    return minutes, sum(r["tok"] for r in chosen)


def measured_table(found: list) -> str:
    recent = found[-LAST_JOBS:]
    head = ["## Measured: last 10 jobs"]
    if not recent:
        return "\n".join(head + ["(no digested jobs yet)"])
    per_tool = {}
    for job in recent:
        for row in job["rows"]:
            entry = per_tool.setdefault(row["tool"], [0, 0.0, 0])
            entry[0] += 1
            entry[1] += (row["run"] + (0 if row.get("wait") else row["pause"])) / 60
            entry[2] += row["tok"]
    n, total = len(recent), sum(e[2] for e in per_tool.values()) or 1
    ranked = sorted(per_tool.items(), key=lambda kv: (-kv[1][2], kv[0]))
    shown, rest = ranked[:5], ranked[5:]
    table = ["| Tool | Calls/job | Min/job | Tokens/job | Share of tokens |", "|---|---|---|---|---|"]
    for tool, (calls, minutes, tokens) in shown:
        table.append(f"| {tool} | {calls / n:.1f} | {minutes / n:.1f} | {tokens / n:,.0f} | {tokens / total:.0%} |")
    if rest:
        calls, minutes, tokens = (sum(e[1][i] for e in rest) for i in range(3))
        table.append(f"| everything else | {calls / n:.1f} | {minutes / n:.1f} | {tokens / n:,.0f} | "
                     f"{tokens / total:.0%} |")
    return "\n".join(head + table)


# --- keeping the issues in step with the jobs -------------------------------------------------


def _next_id(issues: list) -> str:
    return f"H-{max((int(i['id'][2:]) for i in issues), default=0) + 1:03d}"


def new_issue(issues: list, title: str, signatures=(), again_of=None) -> dict:
    issue = {"id": _next_id(issues), "status": "open", "title": title.replace(" · ", " - ").strip(),
             "signatures": list(signatures), "again_of": again_of, "seen": [], "ideas": [], "minutes": None,
             "tokens": 0, "lines": []}
    issues.append(issue)
    return issue


def add_seen(issue: dict, job: str, session, part: str | None, minutes, tokens) -> None:
    """Record that `job` met the issue (once per job, with each of its hard parts); the cost per job is a running mean."""
    entry = next((e for e in issue["seen"] if e["job"] == job), None)
    if entry is not None:
        if part and part not in entry["parts"]:
            entry["parts"].append(part)
        return
    count = len(issue["seen"])
    issue["seen"].append({"job": job, "session": session, "parts": [part] if part else []})
    if minutes is not None:
        known = issue["minutes"] is not None
        base_m, base_t = (issue["minutes"], issue["tokens"]) if known else (0.0, 0)
        weight = count if known else 0
        issue["minutes"] = (base_m * weight + minutes) / (weight + 1)
        issue["tokens"] = round((base_t * weight + tokens) / (weight + 1))


def _fixed_for(issues: list, signature: str) -> dict | None:
    owners = [i for i in issues if i["status"] == "fixed" and signature in i["signatures"]]
    return max(owners, key=lambda i: (i.get("fixed", ""), i["id"])) if owners else None


def fixed_signatures(kb) -> dict:
    """{signature: (issue id, fix date)} for every signature of a fixed issue (the latest fix wins)."""
    found = {}
    for issue in sorted((i for i in load(kb) if i["status"] == "fixed"), key=lambda i: (i.get("fixed", ""), i["id"])):
        for signature in issue["signatures"]:
            found[signature] = (issue["id"], issue["fixed"])
    return found


def hint_note(kb, signature: str) -> str | None:
    """A fixed issue's 'If it happens again' note for this failure, if the signature belongs to one."""
    issue = next((i for i in sorted(load(kb), key=lambda i: i.get("fixed", ""), reverse=True)
                  if i["status"] == "fixed" and signature in i["signatures"] and i.get("note")), None)
    return issue["note"] if issue else None


def _signature_jobs(found: list) -> dict:
    """{signature: {job label: {tool, sample, day of its first failure, call numbers}}}."""
    seen = {}
    for job in found:
        for row in job["rows"]:
            if row["ok"]:
                continue
            entry = seen.setdefault(row["signature"], {}).setdefault(
                job["job"], {"tool": row["tool"], "sample": row["sample"] or "", "day": row["when"].date().isoformat(),
                             "calls": [], "job": job})
            entry["calls"].append(row["n"])
    return seen


def _apply_signature_rules(issues: list, found: list) -> None:
    """A failure signature seen in 2+ jobs becomes an issue; one seen after its fix opens a new issue
    pointing back at the fix; jobs that meet an existing open issue's signature are added to it."""
    for signature, by_job in sorted(_signature_jobs(found).items()):
        owners = [i for i in issues if i["status"] == "open" and signature in i["signatures"]]
        if owners:
            for issue in owners:
                since = next((i["fixed"] for i in issues if i["id"] == issue.get("again_of")), "")
                for label, entry in sorted(by_job.items(), key=lambda kv: _job_key(kv[0])):
                    if entry["day"] > since:
                        add_seen(issue, label, session_of(entry["job"], entry["calls"][0]), None,
                                 *_quick_cost(entry["job"]["rows"], entry["calls"]))
            continue
        fixed = _fixed_for(issues, signature)
        if fixed is not None:
            later = {label: e for label, e in by_job.items() if e["day"] > fixed["fixed"]}
            if later:
                tool = next(iter(later.values()))["tool"]
                issue = new_issue(issues, f"{tool} fails with {signature} again", [signature], fixed["id"])
                for label, entry in sorted(later.items(), key=lambda kv: _job_key(kv[0])):
                    add_seen(issue, label, session_of(entry["job"], entry["calls"][0]), None,
                             *_quick_cost(entry["job"]["rows"], entry["calls"]))
            continue
        if len(by_job) >= 2:
            sample = next(iter(by_job.values()))
            title = f"{sample['tool']} fails: {digest_title(sample['sample'] or signature)}"
            issue = new_issue(issues, title, [signature])
            for label, entry in sorted(by_job.items(), key=lambda kv: _job_key(kv[0])):
                add_seen(issue, label, session_of(entry["job"], entry["calls"][0]), None,
                         *_quick_cost(entry["job"]["rows"], entry["calls"]))


def digest_title(text: str, limit: int = 100) -> str:
    one = " ".join((text or "").split())
    first = re.split(r"(?<=[.!?])\s", one, maxsplit=1)[0]
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"


def save(kb, issues: list, after: str | None = None) -> str:
    """Apply the signature rules, rank the open issues, and write HOTSPOTS.md."""
    found = jobs(kb)
    _apply_signature_rules(issues, found)
    previous = parse(read_text(kb.hotspots_file)) if kb.hotspots_file.is_file() else {"after": None}
    after = after or previous["after"]
    open_issues = sorted((i for i in issues if i["status"] == "open"), key=lambda i: (-_score(i), i["id"]))
    fixed = sorted((i for i in issues if i["status"] == "fixed"), key=lambda i: (i.get("fixed", ""), i["id"]), reverse=True)
    stamp = f"Updated {kb.today()}" + (f" after {after}" if after else "") + "."
    text = ["# Hotspots and known issues", "", f"{stamp} {INTRO}", "", measured_table(found), "",
            "## Open (worst first: jobs × cost per job)"]
    text += [render_issue(i) for i in open_issues[:MAX_OPEN]] or ["(none)"]
    text += ["", "## Fixed"] + ([render_issue(i) for i in fixed] or ["(none yet)"])
    text += ["", "## Parked"] + ([render_issue(i) for i in open_issues[MAX_OPEN:]] or [PARKED_NONE])
    atomic_write(kb.hotspots_file, "\n".join(text) + "\n")
    return str(kb.hotspots_file)


def rebuild(kb, after: str | None = None) -> str:
    return save(kb, load(kb), after)


# --- kb_explain_digest -------------------------------------------------------------------------


def _words(label: str, text, cap: int) -> str:
    if not isinstance(text, str) or not text.strip() or "\n" in text.strip():
        raise SolidWorksError(f"{label} must be one line of text (got {text!r}).", code="KB_BAD_EXPLANATION")
    count = len(text.split())
    if count > cap:
        raise SolidWorksError(f"{label} is {count} words; the cap is {cap}. Shorten it.", code="KB_EXPLANATION_TOO_LONG",
                              params={"words": count, "cap": cap})
    return text.strip()


def _digest_to_explain(kb, dn: str | None) -> tuple:
    if dn:
        job = kb.resolve_job(dn if dn.upper().startswith("DN") else f"DN{dn}")
        parsed = digest.read_digest(kb, job)
        if parsed is None:
            raise SolidWorksError(f"{job} has no digest (it is written when a case is finalised or abandoned).",
                                  code="KB_NO_DIGEST")
        return job, parsed
    waiting = []
    for job in kb.jobs():
        parsed = digest.read_digest(kb, job)
        if parsed is not None and digest.awaiting(parsed):
            waiting.append((parsed["date"] or "", kb.digest_file(job).stat().st_mtime, job, parsed))
    if not waiting:
        raise SolidWorksError("No digest is waiting for explanations.", code="KB_NO_DIGEST_TO_EXPLAIN")
    _, _, job, parsed = max(waiting, key=lambda w: (w[0], w[1]))
    return job, parsed


def explain_digest(kb, explanations: dict, not_measured: list | None = None, fix_ideas: dict | None = None,
                   dn: str | None = None) -> dict:
    """Fill in a digest's hard parts (one explanation each, at most 40 words), link each to an issue of
    HOTSPOTS.md (an existing one when the text says `same as H-00n`, else a new one) and update that file."""
    job, parsed = _digest_to_explain(kb, dn)
    if not digest.awaiting(parsed):
        raise SolidWorksError(f"{job}'s digest is already explained.", code="KB_DIGEST_EXPLAINED")
    if not isinstance(explanations, dict):
        raise SolidWorksError('explanations is {"D1": "what it was doing and why it took long", ...}.',
                              code="KB_BAD_EXPLANATION")
    parts = parsed["parts"]
    waiting = [p["id"] for p in parts if not p["not_measured"] and p["claude"] is None]
    unknown = sorted(set(explanations) - set(waiting))
    if unknown:
        raise SolidWorksError(f"{job} has no hard part(s) {unknown} to explain: they are {waiting}.",
                              code="KB_BAD_EXPLANATION")
    missing = [d for d in waiting if d not in explanations]
    if missing:
        raise SolidWorksError(f"Every hard part needs an explanation (\"not sure\" is allowed): missing {missing}.",
                              code="KB_BAD_EXPLANATION")
    said = {d: _words(f"The explanation of {d}", explanations[d], digest.EXPLANATION_WORDS) for d in waiting}
    extra = [_words("A not-measured part", text, digest.EXPLANATION_WORDS) for text in (not_measured or [])]
    already = sum(1 for p in parts if p["not_measured"])
    if already + len(extra) > digest.MAX_NOT_MEASURED:
        raise SolidWorksError(f"At most {digest.MAX_NOT_MEASURED} not-measured parts ({already} already there).",
                              code="KB_TOO_MANY_PARTS")
    for text in extra:
        parts.append({"id": f"D{len(parts) + 1}", "text": f"not measured, noted by Claude: {text}", "claude": None,
                      "issue": None, "link_note": None, "not_measured": True})
    ideas = {d: _words(f"The fix idea of {d}", t, FIX_IDEA_WORDS) for d, t in (fix_ideas or {}).items()}
    stray = sorted(set(ideas) - {p["id"] for p in parts})
    if stray:
        raise SolidWorksError(f"fix_ideas names {stray}, which are not hard parts of {job}.", code="KB_BAD_EXPLANATION")

    issues = load(kb)
    by_id = {i["id"]: i for i in issues}
    run = {"sessions": kb.meta(job)["sessions"]}
    linked = {}
    for part in parts:
        if part["issue"]:
            continue
        said_text = said.get(part["id"], part["text"].split(": ", 1)[-1])
        named = _SAME_AS.search(f"{said_text} {ideas.get(part['id'], '')}")
        issue = None
        if named:
            issue = by_id.get(named.group(1).upper())
            if issue is None:
                raise SolidWorksError(f"{named.group(0)}: there is no issue {named.group(1)} in HOTSPOTS.md.",
                                      code="KB_NO_SUCH_ISSUE")
        signature = digest.part_signature(part)
        again = None
        if issue is None and signature:
            issue = next((i for i in issues if i["status"] == "open" and signature in i["signatures"]), None)
            again = _fixed_for(issues, signature) if issue is None else None
        if issue is not None and issue["status"] == "fixed":
            again, issue = issue, None
        if issue is None:
            title = digest_title(_plain(part) if said_text.lower().startswith("not sure") else said_text)
            issue = new_issue(issues, title, [signature] if signature else [], again["id"] if again else None)
            by_id[issue["id"]] = issue
        minutes, tokens = digest.part_cost(part)
        calls = digest.part_calls(part)
        add_seen(issue, job, session_of(run, calls[0]) if calls else _first_session(run), part["id"], minutes, tokens)
        if part["id"] in ideas and (job, ideas[part["id"]]) not in issue["ideas"]:
            issue["ideas"].append((job, ideas[part["id"]]))
        part["issue"] = issue["id"]
        linked[part["id"]] = issue["id"]
    for part in parts:
        if part["id"] in said:
            part["claude"] = said[part["id"]]
    atomic_write(kb.digest_file(job), digest.render_parts(parsed["head"], parts))
    save(kb, issues, after=job)
    return {"ok": True, "dn": job, "linked": linked}


def _first_session(run: dict) -> str | None:
    """The job's first session: the one a part with no calls of its own (not measured) is put under."""
    sessions = sorted(run.get("sessions") or [], key=lambda s: s["from_call"])
    return sessions[0]["session_id"][:8] if sessions else None


def _plain(part: dict) -> str:
    """A hard part's text without its cost and the 'not measured' label."""
    return re.sub(r" · cost [\d.]+ min, [\d,]+ tok$", "", part["text"]).replace("not measured, noted by Claude: ", "")


# --- solidworks-mcp kb-fixed ---------------------------------------------------------------------


def kb_fixed(kb, issue_id: str, commit: str, by: str, note: str, tool_reference=None, today: str | None = None) -> dict:
    """Move an issue to Fixed; mark every digest hard part linked to it; rebuild the tool cards it touched."""
    from . import cards

    issue_id = issue_id.upper()
    for label, value in (("--commit", commit), ("--by", by), ("--note", note)):
        if not (value or "").strip():
            raise SolidWorksError(f"{label} must say something.", code="KB_BAD_FIX")
    issues = load(kb)
    issue = next((i for i in issues if i["id"] == issue_id), None)
    if issue is None:
        raise SolidWorksError(f"No issue {issue_id} in HOTSPOTS.md.", code="KB_NO_SUCH_ISSUE")
    if issue["status"] == "fixed":
        raise SolidWorksError(f"{issue_id} is already fixed ({issue['fixed']}, {issue['commit']}).",
                              code="KB_ALREADY_FIXED")
    day = today or kb.today()
    seen = sorted(issue["seen"], key=lambda e: _job_key(e["job"]))
    shown = " · ".join(_seen_text(e) for e in seen[:5]) + (f" · +{len(seen) - 5} jobs" if len(seen) > 5 else "")
    tags = (" · " + " ".join(f"`{s}`" for s in issue["signatures"])) if issue["signatures"] else ""
    if issue.get("again_of"):
        tags += f" · again after {issue['again_of']}"
    lines = [f"- [x] **{issue['id']}** {issue['title']}{tags}",
             f"  - Fixed {day} in {commit.strip()} by: {' '.join(by.split())}"]
    if seen:
        lines.append(f"  - Seen before the fix: {shown}")
    lines.append(f"  - If it happens again: {' '.join(note.split())}")
    issue.update(status="fixed", fixed=day, commit=commit.strip(), by=by, note=note, lines=lines)
    save(kb, issues)
    suffix = f" · ✓ fixed by {' '.join(by.split())} ({commit.strip()}, {day})"
    marked = []
    for job in kb.jobs():
        parsed = digest.read_digest(kb, job)
        if parsed is None:
            continue
        touched = [p for p in parsed["parts"] if p["issue"] == issue_id and p["link_note"] != suffix]
        for part in touched:
            part["link_note"] = suffix
        if touched:
            atomic_write(kb.digest_file(job), digest.render_parts(parsed["head"], parsed["parts"]))
            marked.append(job)
    rebuilt = cards.rebuild_for_signatures(kb, issue["signatures"], tool_reference) if issue["signatures"] else []
    return {"ok": True, "issue": issue_id, "fixed": day, "digests_marked": marked, "cards_rebuilt": rebuilt}
