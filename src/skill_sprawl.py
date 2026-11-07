#!/usr/bin/env python3
"""skill-sprawl — a collection-level audit of a folder tree
of agent skill definitions: the cross-scope duplicates,
description collisions and silent discovery truncation
that accumulate as a skill library grows, found before
they cost context and misroute work.

Pure Python standard library. READ-ONLY and local only: it
reads the skill files in the one tree you point it at
(recursively — subfolders included), edits nothing, and
runs nothing.

Skill discovery is description-matching. Runtimes like
Claude Code show the agent a listing built from each
skill's `name` and `description`, and the agent picks from
that text. Three failure classes only appear at collection
scale, and all three are documented in the wild: skill
libraries growing 16 -> 48 in fifteen days with copies of
the same skill piling up across scopes; 170 installed
skills averaging ~103 tokens of frontmatter each — roughly
17,000 tokens of overhead on every turn; and a reported
discovery listing silently truncated at a ~16,000-character
budget, showing 42 of 63 installed skills (the rest were
never offered to the agent at all, and nothing said so).
None of that is visible in any single file. A static pass
over the whole collection is this tool.

THIS IS NOT A PER-FILE LINTER. It has no opinion about one
file's style, headings or prose. Every check is a
collection-level fact (a pair, a duplicate, a total, a
forecast) or a discoverability fact about one entry in the
collection. Per-file SKILL.md linters already exist; this
is the fleet view they do not take.

THE SUPPORTED SHAPE (v0.1.0 — stated precisely): the tree
is walked recursively for `.md` files. A file is a skill
CANDIDATE when it is named `SKILL.md` (any depth), or when
it is any other `.md` file that opens with a `---`
frontmatter line (a loose skill file). Any other `.md`
file — one that does not open with frontmatter and is not
named SKILL.md — is treated as a supporting doc: counted,
and ignored. A candidate is expected to be one skill
definition: a frontmatter block (a `---` line, flat
`key: value` lines, a closing `---` line) carrying `name`
and `description`, followed by the skill's body (its
instructions). Values may be plain scalars, single- or
double-quoted scalars, inline flow lists (`[a, b]`) or
block lists (`- item` lines under the key). Other keys
(`allowed-tools`, `license`, ...) parse fine and are
ignored. Anything outside that subset — nested maps,
block scalars (`|` / `>`), tabs, duplicate keys, a missing
`name` — is reported per file as UNRECOGNIZED_FILE rather
than guessed at. The Python standard library has no YAML
parser; this subset is what these files actually use.

THE RULES (v0.1.0 — thresholds are labelled heuristics
where they are heuristics):

  UNRECOGNIZED_FILE (ERROR) — a candidate file that
    cannot be read as a skill definition in the
    supported shape (the finding names the reason). The
    runtime loads no skill from it either.
  DESCRIPTION_MISSING (ERROR) — no `description` key, or
    an empty one. Discovery is description-matching, so
    this skill can only ever be invoked by exact name,
    never chosen on merit.
  DESCRIPTION_IDENTICAL (ERROR) — two descriptions whose
    token-set similarity is >= 0.90 (see the metric
    below). A router choosing between them has nothing to
    choose on; discovery between them is a coin flip.
  DUPLICATE_NAME (ERROR) — one skill name defined by two
    or more files anywhere in the tree — the cross-scope
    duplicate: the same skill copied into two scopes.
    Which definition loads depends on scope order. When
    the definitions also disagree (description / body),
    the finding says so — that is drift.
  DESCRIPTION_COLLISION (WARN) — token-set similarity
    >= 0.50 and < 0.90: the pair plausibly claims the
    same triggers, so discovery between them will be
    inconsistent.
  DESCRIPTION_SHORT (WARN, heuristic) — a description
    under 40 characters: too little text to match on.
  BODY_OVERSIZED (WARN, heuristic) — a body over 500
    lines. When a skill fires, its whole body enters the
    context; reference material that big belongs in
    linked files loaded only when needed.
  BODY_EMPTY (WARN) — a skill with no body at all: the
    frontmatter promises a skill but there are no
    instructions to run when it is chosen.
  DISCOVERY_BUDGET_EXCEEDED (WARN, forecast) — the
    collection's discovery listing (each skill's name +
    description, in name order) runs past the reported
    ~16,000-character discovery budget; the finding names
    the skills whose entries START past the line — the
    ones at risk of never being shown to the agent. The
    budget is a reported figure, not a constant of any
    runtime, and the listing cost is a lower bound (real
    listings add formatting), so the forecast is
    optimistic: if the lower bound already busts the
    budget, the real listing does too.
  NO_TRIGGER_PHRASE (INFO, heuristic) — the description
    contains none of the trigger phrases ("use when",
    "use for", "use this", "when you", "whenever"). A
    description that never says when to use the skill is
    harder for discovery to match. INFO only: plenty of
    good descriptions phrase the trigger differently.

THE SIMILARITY METRIC: tokenize each description to the
SET of lowercase alphanumeric word tokens it contains
(re.findall(r"[a-z0-9]+", text.lower())); similarity is
the Jaccard index |A and B| / |A or B| over the two sets.
Repeated words count once. That is all it is — a lexical
overlap score. It is stated in full so any number the tool
prints can be recomputed by hand.

THE TOKEN ESTIMATE: a skill body's approximate token
count is ceil(body_chars / 4) — the common chars-per-token
rule of thumb for English prose. It is labelled an
estimate everywhere it appears; it exists to size context
cost, not to bill anyone.

WHAT IT DOES NOT JUDGE (stated plainly): whether a skill's
instructions are any good; whether one file is well
written (that is the per-file linters' job, deliberately
not repeated here); which of two colliding skills SHOULD
win (sharpening the boundary is an editorial act); skill
files in any other product's shape (reported
unrecognized, not misparsed); the folder name a SKILL.md
sits in versus its `name` field (considered and left out:
the frontmatter name is what the discovery listing shows,
and folder-name coupling differs between runtimes — a
mismatch flag would be a false certainty in v0.1.0); and
any frontmatter field beyond name/description (there is
no standard trigger field in the wild — triggers live in
description text, which is why the trigger check reads
descriptions and stays INFO).

THE LIMIT: a finding is a statement about the files, by
these rules — not about any run. A clean audit means no
collisions, duplicates or bloat BY THESE RULES, not that
discovery in a live session will pick well.

Exit codes: 0 = no ERROR findings (WARN/INFO may exist) ·
1 = at least one ERROR finding · 2 = input error — clean
message, never a traceback; with --json the error is
{"error": ...}.
"""
import argparse
import json
import re
import sys
from pathlib import Path

__version__ = "0.1.0"
TOOL = "skill-sprawl"

# Description excerpts in findings are truncated to this
# many characters, in text and JSON output alike.
EXCERPT_LIMIT = 120

ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"
_SEVERITY_RANK = {ERROR: 0, WARN: 1, INFO: 2}

# The thresholds. Stated here, in SKILL.md, and in the text
# output header, so a printed verdict can always be checked
# against the numbers that produced it.
COLLISION_THRESHOLD = 0.50
IDENTICAL_THRESHOLD = 0.90
SHORT_DESCRIPTION_CHARS = 40
BODY_OVERSIZED_LINES = 500
# A reported figure (a discovery listing observed to cut
# at ~16,000 characters), not a measured constant of any
# runtime. Labelled a forecast wherever it is used.
DISCOVERY_BUDGET_CHARS = 16000

# Trigger phrases searched (lowercase substring) in a
# description for the NO_TRIGGER_PHRASE check. INFO-only
# and labelled heuristic: the list is the whole method.
TRIGGER_PHRASES = ("use when", "use for", "use this",
                   "when you", "whenever")

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class ToolError(Exception):
    """A clean, user-facing failure (printed as ERROR,
    exit 2)."""


def _truncate(text):
    if len(text) <= EXCERPT_LIMIT:
        return text
    return text[:EXCERPT_LIMIT - 1].rstrip() + "…"


# --------------------------------------------------------------------------
# frontmatter — a strict parser for the flat subset these
# files actually use. Anything outside the subset is an
# error with a reason, never a guess.
# --------------------------------------------------------------------------

class _FrontmatterError(Exception):
    """The reason a file is outside the supported shape."""


_PENDING = object()  # key seen, value not yet determined


def _parse_scalar(token):
    """One scalar token: plain, 'single-quoted' ('' escapes
    a quote) or "double-quoted" (\\ and \" unescaped)."""
    token = token.strip()
    if token.startswith('"'):
        if len(token) < 2 or not token.endswith('"'):
            raise _FrontmatterError(
                "unterminated double-quoted scalar")
        body = token[1:-1]
        return body.replace('\\"', '"').replace("\\\\", "\\")
    if token.startswith("'"):
        if len(token) < 2 or not token.endswith("'"):
            raise _FrontmatterError(
                "unterminated single-quoted scalar")
        return token[1:-1].replace("''", "'")
    if token in ("|", ">", "|-", ">-", "|+", ">+"):
        raise _FrontmatterError(
            "block scalars (| and >) are outside the "
            "supported subset")
    return token


def _split_flow(inner):
    """Split the inside of a flow list on top-level commas,
    honouring quotes."""
    parts, current, quote = [], [], None
    for ch in inner:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            current.append(ch)
        elif ch == ",":
            parts.append("".join(current))
            current = []
        elif ch in "[]":
            raise _FrontmatterError(
                "nested flow lists are outside the supported "
                "subset")
        else:
            current.append(ch)
    if quote:
        raise _FrontmatterError(
            "unterminated quote in a flow list")
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _parse_frontmatter(lines):
    """Parse the frontmatter lines into a dict of
    str -> str | list[str] | None. Raises
    _FrontmatterError with the reason anything falls
    outside the supported subset."""
    fields = {}
    current_key = None
    for offset, raw in enumerate(lines):
        lineno = offset + 2  # line 1 is the opening ---
        if "\t" in raw[:len(raw) - len(raw.lstrip())]:
            raise _FrontmatterError(
                f"line {lineno}: tab indentation is outside "
                f"the supported subset")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw[0] in (" ",):
            item = stripped
            if item == "-" or item.startswith("- "):
                if current_key is None:
                    raise _FrontmatterError(
                        f"line {lineno}: a list item with no "
                        f"key above it")
                value = fields[current_key]
                if value is _PENDING:
                    value = fields[current_key] = []
                if not isinstance(value, list):
                    raise _FrontmatterError(
                        f"line {lineno}: list items under "
                        f"\"{current_key}\", which already has "
                        f"a scalar value")
                value.append(_parse_scalar(item[1:]))
                continue
            raise _FrontmatterError(
                f"line {lineno}: indented content is outside "
                f"the supported subset (nested maps and "
                f"folded scalars are not supported)")
        match = re.match(r"^([A-Za-z0-9_-]+):(.*)$", raw)
        if not match:
            raise _FrontmatterError(
                f"line {lineno}: not a \"key: value\" line")
        key, rest = match.group(1), match.group(2).strip()
        if key in fields:
            raise _FrontmatterError(
                f"line {lineno}: duplicate key \"{key}\"")
        current_key = key
        if rest == "":
            fields[key] = _PENDING
        elif rest.startswith("["):
            if not rest.endswith("]"):
                raise _FrontmatterError(
                    f"line {lineno}: unterminated flow list")
            fields[key] = [_parse_scalar(p)
                           for p in _split_flow(rest[1:-1])]
        else:
            fields[key] = _parse_scalar(rest)
    return {k: (None if v is _PENDING else v)
            for k, v in fields.items()}


_ABSENT = object()


def _is_candidate(path):
    """A `.md` file is a skill candidate when it is named
    SKILL.md, or when it opens with a `---` frontmatter
    line (a loose skill file). Decided on raw bytes so a
    non-UTF-8 supporting doc is still just ignored."""
    if path.name == "SKILL.md":
        return True
    try:
        with path.open("rb") as handle:
            first = handle.readline()
    except OSError:
        return True  # let _read_skill report the failure
    return first.strip() == b"---"


def _read_skill(path):
    """Read one candidate file. Returns (skill, reason):
    exactly one of the two is not None. `skill` carries
    name, raw description (or None), and the body metrics:
    body_text (kept for drift comparison), body_lines,
    body_chars, est_tokens."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, (f"the file could not be read "
                      f"({exc.__class__.__name__})")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "the file is not UTF-8 text"
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, "no frontmatter block (the file does " \
                     "not open with a --- line)"
    closing = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing = i
            break
    if closing is None:
        return None, "the frontmatter block is never closed"
    try:
        fields = _parse_frontmatter(lines[1:closing])
    except _FrontmatterError as exc:
        return None, str(exc)
    name = fields.get("name")
    if isinstance(name, list):
        return None, "the `name` field is a list, not a name"
    if name is None or not str(name).strip():
        return None, "the frontmatter has no `name`"
    name = str(name).strip()
    description = fields.get("description")
    if isinstance(description, list):
        return None, "the `description` field is a list, " \
                     "not a description"
    if description is not None:
        description = str(description)
    # The body: everything after the closing --- line, with
    # leading/trailing blank lines trimmed. Interior blank
    # lines count, in both the line count and the chars.
    body_lines = lines[closing + 1:]
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()
    body_text = "\n".join(body_lines)
    body_chars = len(body_text)
    est_tokens = (body_chars + 3) // 4 if body_chars else 0
    return {
        "name": name,
        "description": description,
        "body_text": body_text,
        "body_lines": len(body_lines),
        "body_chars": body_chars,
        "est_tokens": est_tokens,
    }, None


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def token_set(text):
    """The set of lowercase alphanumeric word tokens in a
    description — the metric's whole input."""
    return set(_TOKEN_RE.findall(text.lower()))


def similarity(desc_a, desc_b):
    """Jaccard index |A and B| / |A or B| over the two
    descriptions' token sets."""
    a, b = token_set(desc_a), token_set(desc_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def listing_chars(skill):
    """What one skill costs the discovery listing: its name
    plus its (stripped) description. A lower bound — real
    listings add formatting around both."""
    desc = skill["description"] or ""
    return len(skill["name"]) + len(desc.strip())


class _Audit:
    def __init__(self, folder):
        self.folder = folder
        self.findings = []
        self.skills = []        # parsed skill dicts + file
        self.files_seen = 0     # every .md file in the tree
        self.candidates = 0     # SKILL.md + frontmatter .md
        self.ignored = 0        # supporting docs

    def add(self, rule, severity, message, fix, skills=None,
            files=None, similarity_value=None, excerpts=None):
        self.findings.append({
            "rule": rule,
            "severity": severity,
            "skills": skills or [],
            "files": files or [],
            "similarity": similarity_value,
            "excerpts": excerpts,
            "message": message,
            "fix": fix,
        })


def _check_skill(audit, skill):
    """Per-skill rules: description presence, length and
    trigger phrasing; body presence and size."""
    name, rel = skill["name"], skill["file"]
    desc = skill["description"]
    if desc is None or not desc.strip():
        audit.add(
            "DESCRIPTION_MISSING", ERROR,
            f"skill \"{name}\" has no description — "
            f"discovery is description-matching, so this "
            f"skill can only ever be invoked by exact "
            f"name, never chosen on merit",
            "write a description that says what the skill "
            "does and when to use it",
            skills=[name], files=[rel])
    else:
        stripped = desc.strip()
        if len(stripped) < SHORT_DESCRIPTION_CHARS:
            audit.add(
                "DESCRIPTION_SHORT", WARN,
                f"skill \"{name}\" has a "
                f"{len(stripped)}-character description — "
                f"under the {SHORT_DESCRIPTION_CHARS}-"
                f"character floor (heuristic): too little "
                f"text for discovery to match on",
                "expand the description: what it does, "
                "what it works on, when to use it",
                skills=[name], files=[rel],
                excerpts=[_truncate(stripped)])
        lowered = stripped.lower()
        if not any(p in lowered for p in TRIGGER_PHRASES):
            audit.add(
                "NO_TRIGGER_PHRASE", INFO,
                f"the description of \"{name}\" names no "
                f"trigger (heuristic: none of \"use when / "
                f"use for / use this / when you / whenever\" "
                f"appears in it) — discovery matches on "
                f"descriptions, and a description that never "
                f"says when to use the skill is harder to "
                f"match; plenty of good descriptions phrase "
                f"the trigger differently, so judge this "
                f"one yourself",
                "add a \"Use when …\" clause naming the "
                "situation that should invoke this skill",
                skills=[name], files=[rel],
                excerpts=[_truncate(stripped)])
    if skill["body_lines"] == 0:
        audit.add(
            "BODY_EMPTY", WARN,
            f"skill \"{name}\" has an empty body — the "
            f"frontmatter promises a skill but there are no "
            f"instructions to run when it is chosen",
            "write the instructions, or delete the skill "
            "so it stops occupying a discovery slot",
            skills=[name], files=[rel])
    elif skill["body_lines"] > BODY_OVERSIZED_LINES:
        audit.add(
            "BODY_OVERSIZED", WARN,
            f"skill \"{name}\" has a "
            f"{skill['body_lines']}-line body — over the "
            f"{BODY_OVERSIZED_LINES}-line line "
            f"(heuristic): when this skill fires, its "
            f"whole body enters the context "
            f"(~{skill['est_tokens']} tokens, est. at "
            f"chars/4) — reference material that big "
            f"belongs in linked files, loaded only when "
            f"needed",
            "move reference sections into files beside "
            "the SKILL.md and link them from a shorter "
            "body",
            skills=[name], files=[rel])


def _check_pairs(audit):
    """Pairwise description similarity over every pair of
    skills that HAVE descriptions. Each pair earns at most
    one finding: identical beats collision."""
    described = [s for s in audit.skills
                 if s["description"]
                 and s["description"].strip()]
    for i in range(len(described)):
        for j in range(i + 1, len(described)):
            a, b = described[i], described[j]
            score = similarity(a["description"],
                               b["description"])
            if score < COLLISION_THRESHOLD:
                continue
            pair = sorted(
                [(a["name"], a["file"], a["description"]),
                 (b["name"], b["file"], b["description"])])
            names = [p[0] for p in pair]
            files = [p[1] for p in pair]
            excerpts = [_truncate(p[2].strip()) for p in pair]
            if score >= IDENTICAL_THRESHOLD:
                audit.add(
                    "DESCRIPTION_IDENTICAL", ERROR,
                    f"the descriptions of "
                    f"\"{names[0]}\" and \"{names[1]}\" are "
                    f"near-identical (token-set similarity "
                    f"{score:.3f}, at or above the "
                    f"{IDENTICAL_THRESHOLD:.2f} line) — a "
                    f"router choosing between them has "
                    f"nothing to choose on, so discovery "
                    f"between them is a coin flip",
                    "differentiate the descriptions until "
                    "each names work the other does not "
                    "claim — or merge the two skills",
                    skills=names, files=files,
                    similarity_value=round(score, 3),
                    excerpts=excerpts)
            else:
                audit.add(
                    "DESCRIPTION_COLLISION", WARN,
                    f"the descriptions of "
                    f"\"{names[0]}\" and \"{names[1]}\" "
                    f"overlap heavily (token-set similarity "
                    f"{score:.3f}, at or above the "
                    f"{COLLISION_THRESHOLD:.2f} line) — "
                    f"they plausibly claim the same "
                    f"triggers, so discovery between them "
                    f"will be inconsistent",
                    "sharpen the boundary: name the tasks "
                    "each one owns, and in each description "
                    "say what the other is for",
                    skills=names, files=files,
                    similarity_value=round(score, 3),
                    excerpts=excerpts)


def _check_duplicates(audit):
    """One name defined by 2+ files anywhere in the tree —
    the cross-scope duplicate. When the definitions
    disagree on description / body, the finding names the
    drift."""
    by_name = {}
    for skill in audit.skills:
        by_name.setdefault(skill["name"], []).append(skill)
    for name in sorted(by_name):
        group = by_name[name]
        if len(group) < 2:
            continue
        files = sorted(s["file"] for s in group)
        drifted = []
        if len({(s["description"] or "") for s in group}) > 1:
            drifted.append("description")
        if len({s["body_text"] for s in group}) > 1:
            drifted.append("body")
        message = (
            f"the name \"{name}\" is defined by "
            f"{len(group)} files ({', '.join(files)}) — "
            f"which definition loads depends on scope "
            f"order, so the collection does not reliably "
            f"contain the skill you think it does")
        if drifted:
            message += (
                f"; and the definitions disagree on "
                f"{', '.join(drifted)} — the copies have "
                f"drifted apart")
        audit.add(
            "DUPLICATE_NAME", ERROR, message,
            "keep one definition per name across the "
            "whole tree, or rename until names are unique",
            skills=[name], files=files)


def _check_discovery(audit):
    """The truncation forecast. The discovery listing is
    modelled as each parsed skill's name + description,
    laid out in name order (plain string sort) against
    the reported budget; a skill is at risk when its
    entry STARTS at or past the budget line. Returns the
    discovery dict for the report."""
    ordered = sorted(audit.skills, key=lambda s: s["name"])
    total = sum(listing_chars(s) for s in ordered)
    at_risk, offset = [], 0
    for skill in ordered:
        if offset >= DISCOVERY_BUDGET_CHARS:
            at_risk.append(skill)
        offset += listing_chars(skill)
    if at_risk:
        names = [s["name"] for s in at_risk]
        audit.add(
            "DISCOVERY_BUDGET_EXCEEDED", WARN,
            f"the discovery listing for this collection "
            f"totals {total} characters (name + "
            f"description per skill — a lower bound) "
            f"against a reported budget of "
            f"~{DISCOVERY_BUDGET_CHARS} characters "
            f"(forecast, heuristic): laid out in name "
            f"order, {len(at_risk)} skill(s) start past "
            f"the budget line and risk never being shown "
            f"to the agent at all: {', '.join(names)}",
            "cut the listing: shorter descriptions, "
            "fewer duplicate skills — or split the "
            "collection across scopes so each scope's "
            "listing fits",
            skills=names,
            files=[s["file"] for s in at_risk])
    return {"budget_chars": DISCOVERY_BUDGET_CHARS,
            "total_listing_chars": total,
            "at_risk": [s["name"] for s in at_risk]}


def audit_folder(folder):
    """Walk + check one tree of skill definitions.
    Returns the full audit dict (the JSON shape; the text
    view renders from it). Raises ToolError only for input
    failures — a candidate file that is outside the
    supported shape is a FINDING, because that is exactly
    the defect the tool exists to report."""
    if not folder.exists():
        raise ToolError(f"folder not found: {folder}")
    if not folder.is_dir():
        raise ToolError(f"not a folder: {folder}")
    paths = sorted(
        (p for p in folder.rglob("*.md") if p.is_file()),
        key=lambda p: p.relative_to(folder).as_posix())
    if not paths:
        raise ToolError(
            f"no markdown files (*.md) found under {folder}")
    audit = _Audit(folder)
    audit.files_seen = len(paths)
    for path in paths:
        rel = path.relative_to(folder).as_posix()
        if not _is_candidate(path):
            audit.ignored += 1
            continue
        audit.candidates += 1
        skill, reason = _read_skill(path)
        if skill is None:
            audit.add(
                "UNRECOGNIZED_FILE", ERROR,
                f"this file cannot be read as a skill "
                f"definition in the supported shape: "
                f"{reason} — the runtime loads no skill "
                f"from it either",
                "give the file frontmatter in the "
                "supported shape (--- name / description "
                "---) followed by the skill's instructions, "
                "or move it out of the skills tree",
                files=[rel])
            continue
        skill["file"] = rel
        audit.skills.append(skill)
    if audit.candidates == 0:
        raise ToolError(
            f"no skill definitions found under {folder} "
            f"({audit.files_seen} markdown file(s) seen, "
            f"none named SKILL.md or opening with a "
            f"frontmatter block)")
    for skill in audit.skills:
        _check_skill(audit, skill)
    _check_pairs(audit)
    _check_duplicates(audit)
    discovery = _check_discovery(audit)
    return _finish(audit, discovery)


def _finish(audit, discovery):
    audit.findings.sort(key=lambda f: (
        _SEVERITY_RANK[f["severity"]],
        f["rule"],
        ", ".join(f["skills"]),
        ", ".join(f["files"]),
        f["message"],
    ))
    summary = {
        "findings": len(audit.findings),
        "errors": sum(1 for f in audit.findings
                      if f["severity"] == ERROR),
        "warnings": sum(1 for f in audit.findings
                        if f["severity"] == WARN),
        "info": sum(1 for f in audit.findings
                    if f["severity"] == INFO),
    }
    inventory = [{
        "name": s["name"],
        "file": s["file"],
        "description": s["description"],
        "description_chars":
            len(s["description"].strip())
            if s["description"] else 0,
        "body_lines": s["body_lines"],
        "body_chars": s["body_chars"],
        "est_tokens": s["est_tokens"],
        "listing_chars": listing_chars(s),
    } for s in audit.skills]
    totals = {
        "body_lines": sum(s["body_lines"]
                          for s in audit.skills),
        "body_chars": sum(s["body_chars"]
                          for s in audit.skills),
        "est_tokens": sum(s["est_tokens"]
                          for s in audit.skills),
        "listing_chars": discovery["total_listing_chars"],
    }
    return {
        "tool": TOOL, "version": __version__,
        "command": "audit",
        "folder": audit.folder.name,
        "files_seen": audit.files_seen,
        "candidates": audit.candidates,
        "ignored": audit.ignored,
        "skills_parsed": len(audit.skills),
        "unrecognized": audit.candidates - len(audit.skills),
        "thresholds": {
            "collision": COLLISION_THRESHOLD,
            "identical": IDENTICAL_THRESHOLD,
            "short_description_chars":
                SHORT_DESCRIPTION_CHARS,
            "body_oversized_lines": BODY_OVERSIZED_LINES,
            "discovery_budget_chars": DISCOVERY_BUDGET_CHARS,
        },
        "totals": totals,
        "discovery": discovery,
        "skills": inventory,
        "findings": audit.findings,
        "summary": summary,
        "clean": summary["errors"] == 0,
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

SCOPE_LINE = ("Read-only: it edits nothing and runs "
              "nothing. It reads the skill definition "
              "files in the tree you point it at — files "
              "named SKILL.md, plus loose .md files that "
              "open with a frontmatter block; other .md "
              "files are counted as supporting docs and "
              "ignored.")
METRIC_LINE = ("Similarity: the Jaccard index over each "
               "description's set of lowercase alphanumeric "
               "word tokens. Collision at "
               f"{COLLISION_THRESHOLD:.2f}, near-identical "
               f"at {IDENTICAL_THRESHOLD:.2f}; the short-"
               f"description floor is "
               f"{SHORT_DESCRIPTION_CHARS} characters; the "
               f"oversized-body line is "
               f"{BODY_OVERSIZED_LINES} lines; the "
               f"discovery budget is "
               f"{DISCOVERY_BUDGET_CHARS} listing "
               "characters (a reported figure). Token "
               "counts are estimates at chars/4. Thresholds "
               "are heuristics — see SKILL.md.")


def _where(finding):
    parts = []
    if finding["skills"]:
        parts.append(" + ".join(f"\"{n}\""
                                for n in finding["skills"]))
    if finding["files"]:
        parts.append(", ".join(finding["files"]))
    return " · ".join(parts) if parts else "collection"


def render_text(audit):
    t = audit["totals"]
    lines = [
        f"skill-sprawl v{__version__} — audit · folder "
        f"{audit['folder']}",
        f"{audit['files_seen']} markdown files seen · "
        f"{audit['candidates']} skill candidates · "
        f"{audit['skills_parsed']} skills parsed · "
        f"{audit['unrecognized']} unrecognized · "
        f"{audit['ignored']} ignored (supporting docs) · "
        f"walk: recursive (subfolders included)",
        SCOPE_LINE,
        METRIC_LINE,
        "",
    ]
    if audit["skills"]:
        lines.append(
            f"INVENTORY ({len(audit['skills'])}):")
        for s in audit["skills"]:
            lines.append(
                f"  {s['name']} — "
                f"{s['description_chars']} desc chars · "
                f"body {s['body_lines']} lines · "
                f"~{s['est_tokens']} tokens est. · "
                f"{s['file']}")
        lines.append(
            f"COLLECTION TOTALS: "
            f"{len(audit['skills'])} skills · "
            f"{t['body_lines']} body lines · "
            f"~{t['est_tokens']} tokens of body text "
            f"(est., chars/4) · discovery listing "
            f"{t['listing_chars']} of "
            f"{DISCOVERY_BUDGET_CHARS} budget chars")
        lines.append("")
    findings = audit["findings"]
    if not findings:
        lines.append("No findings — every skill passes "
                     "the static rules.")
    else:
        sections = [
            (ERROR, "ERRORS — skills that cannot be "
                    "discovered, collide head-on, are "
                    "defined more than once, or cannot be "
                    "read"),
            (WARN, "WARNINGS — collisions and bloat that "
                   "misroute work or burn context"),
            (INFO, "INFO — worth knowing"),
        ]
        for severity, title in sections:
            group = [f for f in findings
                     if f["severity"] == severity]
            if not group:
                continue
            lines.append(f"{title} ({len(group)}):")
            for f in group:
                lines.append(
                    f"[{f['rule']}] {_where(f)}")
                lines.append(f"  {f['message']}")
                lines.append(f"  fix: {f['fix']}")
            lines.append("")
    s = audit["summary"]
    lines.append(
        f"Summary: {s['findings']} findings · "
        f"{s['errors']} errors · {s['warnings']} warnings · "
        f"{s['info']} info")
    if s["errors"]:
        lines.append(
            f"Verdict: FAIL — {s['errors']} error(s): the "
            f"collection has skills that cannot be "
            f"discovered, collide head-on, are defined "
            f"more than once, or cannot be read at all. "
            f"Fix the errors before trusting discovery in "
            f"this collection.")
    elif s["warnings"]:
        lines.append(
            f"Verdict: NO UNDISCOVERABLE SKILLS — but "
            f"{s['warnings']} warning(s) describe "
            f"collisions or bloat that will misroute work "
            f"or burn context.")
    else:
        lines.append("Verdict: CLEAN — no collisions, "
                     "duplicates or bloat found by the "
                     "static rules.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

class _JsonAwareParser(argparse.ArgumentParser):
    """ArgumentParser that reports usage errors as JSON in
    --json mode ({"error": ...} on stdout, same exit code 2)
    — an agent driving this tool with --json never gets
    plain text or a traceback. The flag lives on the class
    so subparsers share it."""

    json_mode = False

    def error(self, message):
        if self.json_mode:
            print(json.dumps({"error": message}))
            self.exit(2)
        super().error(message)


def cmd_audit(a):
    audit = audit_folder(Path(a.folder))
    if a.json:
        print(json.dumps(audit, indent=2))
    else:
        print(render_text(audit))
    return 0 if audit["clean"] else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    _JsonAwareParser.json_mode = "--json" in argv
    ap = _JsonAwareParser(
        prog=TOOL,
        description="Statically audit a collection of "
                    "skill definitions (SKILL.md files and "
                    "loose frontmatter .md files, walked "
                    "recursively): cross-scope duplicate "
                    "names, description collisions that "
                    "make discovery a coin flip, oversized "
                    "bodies, and a discovery-budget "
                    "truncation forecast. Read-only, runs "
                    "nothing (stdlib only, local only).")
    sub = ap.add_subparsers(dest="command", required=True,
                            parser_class=_JsonAwareParser)

    p_audit = sub.add_parser(
        "audit", help="audit one tree of skill "
                      "definitions (walked recursively): "
                      "exit 1 when any skill is broken as "
                      "defined (an ERROR finding)")
    p_audit.add_argument(
        "folder",
        help="the root folder of the skill tree "
             "(e.g. ~/.claude/skills)")
    p_audit.add_argument("--json", action="store_true",
                         help="print one JSON object instead "
                              "of text")
    p_audit.set_defaults(func=cmd_audit)

    a = ap.parse_args(argv)

    def fail(msg):
        if getattr(a, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"ERROR: {msg}")
        return 2

    try:
        return a.func(a)
    except ToolError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())
