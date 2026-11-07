#!/usr/bin/env python3
"""fleet-audit — a static audit of a folder of Claude Code
style subagent definitions: the description collisions that
make routing a coin flip, and the tool grants that do not
match the job the description promises, found before the
runtime misroutes anything.

Pure Python standard library. READ-ONLY and local only: it
reads the agent definition files in the one folder you point
it at (recursively — subfolders included), edits nothing,
and runs nothing.

Agent routing is description-matching. The runtime compares
the task against each agent's `description` text and picks
the best match; Anthropic documents no tie-break rule. A
controlled study of that behaviour (mer.vin, 2026) found a
near-duplicate "twin" agent winning 0 of 25 routing trials
against a better lexical match, one verb change in the task
flipping delegation 10 of 10 times, and 27.3% of delegations
silently falling through to the built-in general-purpose
agent. A published self-audit of a 34-agent fleet found two
descriptions overlapping enough that the same task routed to
both on separate runs — and a reviewer agent still carrying
edit access left over from an earlier draft, occasionally
producing diffs instead of verdicts. These are fleet-scale
failures: invisible in any single file, obvious in a static
pass over the whole folder. That pass is this tool.

THE SUPPORTED SHAPE (v0.1.0 — stated precisely): every
`.md` file in the tree is expected to be one agent
definition: a Markdown file opening with a frontmatter
block (a `---` line, flat `key: value` lines, a closing
`---` line), carrying `name` and `description`, optionally
`tools` and `model`. Values may be plain scalars, single-
or double-quoted scalars, inline flow lists (`[a, b]`) or
block lists (`- item` lines under the key); `tools` may
also be one comma-separated scalar. Anything outside that
subset — nested maps, block scalars (`|` / `>`), tabs,
duplicate keys, a missing `name` — is reported per file as
UNRECOGNIZED_FILE rather than guessed at. The Python
standard library has no YAML parser; this subset is what
these files actually use.

THE RULES (v0.1.0 — thresholds and intent inferences are
labelled heuristics where they are heuristics):

  UNRECOGNIZED_FILE (ERROR) — a `.md` file that cannot be
    read as an agent definition in the supported shape
    (the finding names the reason). The runtime loads no
    agent from it either.
  DESCRIPTION_MISSING (ERROR) — no `description` key, or
    an empty one. Routing is description-matching, so this
    agent can only ever be reached by explicit name, never
    chosen on merit.
  DESCRIPTION_IDENTICAL (ERROR) — two descriptions whose
    token-set similarity is >= 0.90 (see the metric below).
    A router choosing between them has nothing to choose
    on; delegation between them is a coin flip.
  DUPLICATE_NAME (ERROR) — one agent name defined by two
    or more files. Which definition loads depends on scope
    order, so the fleet does not reliably contain the
    agent you think it does. When the definitions also
    disagree (description / tools / model), the finding
    says so — that is cross-scope drift.
  DESCRIPTION_COLLISION (WARN) — token-set similarity
    >= 0.50 and < 0.90: the pair plausibly claims the
    same tasks, so routing between them will be
    inconsistent.
  DESCRIPTION_SHORT (WARN, heuristic) — a description
    under 40 characters: too little text for a router to
    match on.
  ROLE_TOOL_MISMATCH (WARN, heuristic) — role intent is
    inferred from words in the agent's name and description
    (the word lists are in SKILL.md; an inference is not a
    fact and the finding says so). A read-intent agent
    (review / audit / verify / check / inspect /
    read-only) whose tools grant write access (Edit,
    Write, MultiEdit, NotebookEdit, Bash) — unless it also
    declares write intent, in which case the grant matches
    the promise. Or a write-intent agent (build / edit /
    write word family) whose explicit tool list grants
    nothing that can modify a file.
  TOOLS_EMPTY (WARN) — a `tools` field that is present but
    empty (`tools: []`): the agent can call no tool at
    all. Deliberately distinct from an absent field.
  TOOLS_UNSPECIFIED (INFO) — no `tools` field at all: the
    agent inherits every tool the runtime offers, the
    widest grant there is.

THE SIMILARITY METRIC: tokenize each description to the
SET of lowercase alphanumeric word tokens it contains
(re.findall(r"[a-z0-9]+", text.lower())); similarity is
the Jaccard index |A and B| / |A or B| over the two sets.
Repeated words count once. That is all it is — a lexical
overlap score. It is stated in full so any number the tool
prints can be recomputed by hand.

WHAT IT DOES NOT JUDGE (stated plainly): whether an agent's
prompt body is any good; whether the model choice is right;
which of two colliding agents SHOULD win (sharpening the
boundary is an editorial act, the tool only shows where it
is missing); agent files in any other product's format;
and the filename-vs-name relationship — the `name` field
is the identity the runtime routes on, so a filename that
differs from it is not a finding. Intent inference is a
heuristic over two word lists: a poetically named reviewer
will be missed, and the finding text labels itself so a
human can overrule it in seconds.

THE LIMIT: a finding is a statement about the files, by
these rules — not about any run. A clean audit means no
collision or mismatch BY THESE RULES, not that delegation
in a live session will be correct.

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
TOOL = "fleet-audit"

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

# Tools that let an agent modify files. Bash is counted
# write-capable: a shell can write anything the user can.
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
WRITE_CAPABLE_TOOLS = WRITE_TOOLS | {"Bash"}
_WRITE_BY_LOWER = {t.lower(): t for t in WRITE_CAPABLE_TOOLS}

# Role-intent word families, matched against the agent's
# name + description. Both lists are deliberately narrow,
# they are printed in SKILL.md, and every finding they
# produce names the words that matched — intent inference
# is a heuristic, never a fact.
_READ_INTENT_RE = re.compile(
    r"\b(?:review|audit|verif|check|inspect)\w*"
    r"|\bread[-\s]only\b",
    re.IGNORECASE)
_WRITE_INTENT_RE = re.compile(
    r"\b(?:build|builds|builder|building"
    r"|edit|edits|editor|editing"
    r"|write|writes|writer)\b",
    re.IGNORECASE)

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


def _read_agent(path):
    """Read one `.md` file. Returns (agent, reason): exactly
    one of the two is not None. `agent` carries name, raw
    description (or None), tools (list or None when the
    field is absent), tools_empty (True for an explicitly
    empty tools list), and model (or None)."""
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
    tools_field = fields.get("tools", _ABSENT)
    tools_empty = False
    if tools_field is _ABSENT or tools_field is None:
        tools = None  # field absent: inherits everything
    elif isinstance(tools_field, list):
        tools = [str(t).strip() for t in tools_field
                 if str(t).strip()]
        tools_empty = len(tools) == 0
    else:
        tools = [t.strip() for t in str(tools_field).split(",")
                 if t.strip()]
        tools_empty = len(tools) == 0
    model = fields.get("model")
    if isinstance(model, list):
        return None, "the `model` field is a list, not a model"
    if model is not None:
        model = str(model).strip() or None
    return {
        "name": name,
        "description": description,
        "tools": tools,
        "tools_empty": tools_empty,
        "model": model,
    }, None


_ABSENT = object()


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


def _intent(text):
    """(read_words, write_words): the intent words found in
    a name + description, in order of appearance, deduped.
    Inference, not fact — the lists are the whole method."""
    read_words, write_words = [], []
    for match in _READ_INTENT_RE.finditer(text):
        word = match.group(0).lower()
        if word not in read_words:
            read_words.append(word)
    for match in _WRITE_INTENT_RE.finditer(text):
        word = match.group(0).lower()
        if word not in write_words:
            write_words.append(word)
    return read_words, write_words


class _Audit:
    def __init__(self, folder):
        self.folder = folder
        self.findings = []
        self.agents = []        # parsed agent dicts + file
        self.files = 0

    def add(self, rule, severity, message, fix, agents=None,
            files=None, similarity_value=None, excerpts=None):
        self.findings.append({
            "rule": rule,
            "severity": severity,
            "agents": agents or [],
            "files": files or [],
            "similarity": similarity_value,
            "excerpts": excerpts,
            "message": message,
            "fix": fix,
        })


def _check_agent(audit, agent):
    """Per-agent rules: description presence and length,
    the tools-field states, role vs tools."""
    name, rel = agent["name"], agent["file"]
    desc = agent["description"]
    if desc is None or not desc.strip():
        audit.add(
            "DESCRIPTION_MISSING", ERROR,
            f"agent \"{name}\" has no description — "
            f"routing is description-matching, so this "
            f"agent can only ever be reached by explicit "
            f"name, never chosen on merit",
            "write a description that says what the agent "
            "does and when to use it",
            agents=[name], files=[rel])
    elif len(desc.strip()) < SHORT_DESCRIPTION_CHARS:
        audit.add(
            "DESCRIPTION_SHORT", WARN,
            f"agent \"{name}\" has a "
            f"{len(desc.strip())}-character description — "
            f"under the {SHORT_DESCRIPTION_CHARS}-character "
            f"floor (heuristic): too little text for a "
            f"router to match on",
            "expand the description: what it does, what it "
            "works on, when to use it",
            agents=[name], files=[rel],
            excerpts=[_truncate(desc.strip())])
    if agent["tools"] is None:
        audit.add(
            "TOOLS_UNSPECIFIED", INFO,
            f"agent \"{name}\" declares no tools field — "
            f"it inherits every tool the runtime offers, "
            f"the widest grant there is",
            "declare the tools it actually needs; an "
            "explicit list is the fleet's permission "
            "boundary",
            agents=[name], files=[rel])
    elif agent["tools_empty"]:
        audit.add(
            "TOOLS_EMPTY", WARN,
            f"agent \"{name}\" declares an explicitly "
            f"empty tools list — it can call no tool at all",
            "grant the tools it needs, or remove the empty "
            "field if inheriting everything was the intent",
            agents=[name], files=[rel])
    # Role vs tools (heuristic — intent is inferred).
    text = name + " " + (desc or "")
    read_words, write_words = _intent(text)
    if agent["tools"]:
        granted = []
        for tool in agent["tools"]:
            canonical = _WRITE_BY_LOWER.get(tool.lower())
            if canonical and canonical not in granted:
                granted.append(canonical)
        if read_words and not write_words and granted:
            audit.add(
                "ROLE_TOOL_MISMATCH", WARN,
                f"agent \"{name}\" reads as read-only work "
                f"(heuristic: intent inferred from "
                f"{', '.join(read_words)} in its name / "
                f"description) but its tools grant write "
                f"access: {', '.join(granted)} — a reviewer "
                f"that can edit will eventually produce "
                f"diffs instead of verdicts",
                "drop the write tools — or, if it really "
                "should write, change the description so "
                "the intent is honest",
                agents=[name], files=[rel])
        elif write_words and not read_words and not granted:
            audit.add(
                "ROLE_TOOL_MISMATCH", WARN,
                f"agent \"{name}\" reads as writing work "
                f"(heuristic: intent inferred from "
                f"{', '.join(write_words)} in its name / "
                f"description) but its tool list "
                f"({', '.join(agent['tools'])}) grants "
                f"nothing that can modify a file — it "
                f"cannot do the job its description promises",
                "grant the write tools the job needs "
                "(Edit / Write / Bash), or fix the "
                "description",
                agents=[name], files=[rel])


def _check_pairs(audit):
    """Pairwise description similarity over every pair of
    agents that HAVE descriptions. Each pair earns at most
    one finding: identical beats collision."""
    described = [a for a in audit.agents
                 if a["description"]
                 and a["description"].strip()]
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
                    f"nothing to choose on, so delegation "
                    f"between them is a coin flip",
                    "differentiate the descriptions until "
                    "each names work the other does not "
                    "claim — or merge the two agents",
                    agents=names, files=files,
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
                    f"they plausibly claim the same tasks, "
                    f"so routing between them will be "
                    f"inconsistent",
                    "sharpen the boundary: name the tasks "
                    "each one owns, and in each description "
                    "say what the other is for",
                    agents=names, files=files,
                    similarity_value=round(score, 3),
                    excerpts=excerpts)


def _check_duplicates(audit):
    """One name defined by 2+ files. When the definitions
    disagree on description / tools / model, the finding
    names the drift."""
    by_name = {}
    for agent in audit.agents:
        by_name.setdefault(agent["name"], []).append(agent)
    for name in sorted(by_name):
        group = by_name[name]
        if len(group) < 2:
            continue
        files = sorted(a["file"] for a in group)
        drifted = []
        for field in ("description", "tools", "model"):
            values = {json.dumps(a[field], sort_keys=True)
                      if field == "tools" else a[field]
                      for a in group}
            if len(values) > 1:
                drifted.append(field)
        message = (
            f"the name \"{name}\" is defined by "
            f"{len(group)} files ({', '.join(files)}) — "
            f"which definition loads depends on scope "
            f"order, so the fleet does not reliably contain "
            f"the agent you think it does")
        if drifted:
            message += (
                f"; and the definitions disagree on "
                f"{', '.join(drifted)} — the scopes have "
                f"drifted apart")
        audit.add(
            "DUPLICATE_NAME", ERROR, message,
            "keep one definition per name in the fleet, "
            "or rename until names are unique",
            agents=[name], files=files)


def audit_folder(folder):
    """Walk + check one folder of agent definitions.
    Returns the full audit dict (the JSON shape; the text
    view renders from it). Raises ToolError only for input
    failures — a file that exists but is outside the
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
            f"no agent files (*.md) found under {folder}")
    audit = _Audit(folder)
    audit.files = len(paths)
    for path in paths:
        rel = path.relative_to(folder).as_posix()
        agent, reason = _read_agent(path)
        if agent is None:
            audit.add(
                "UNRECOGNIZED_FILE", ERROR,
                f"this file cannot be read as an agent "
                f"definition in the supported shape: "
                f"{reason} — the runtime loads no agent "
                f"from it either",
                "give the file frontmatter in the "
                "supported shape (--- name / description / "
                "tools / model ---), or move it out of the "
                "agents folder",
                files=[rel])
            continue
        agent["file"] = rel
        audit.agents.append(agent)
    for agent in audit.agents:
        _check_agent(audit, agent)
    _check_pairs(audit)
    _check_duplicates(audit)
    return _finish(audit)


def _finish(audit):
    audit.findings.sort(key=lambda f: (
        _SEVERITY_RANK[f["severity"]],
        f["rule"],
        ", ".join(f["agents"]),
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
        "name": a["name"],
        "file": a["file"],
        "description": a["description"],
        "description_chars":
            len(a["description"].strip())
            if a["description"] else 0,
        "tools": a["tools"],
        "model": a["model"],
    } for a in audit.agents]
    unrecognized = audit.files - len(audit.agents)
    return {
        "tool": TOOL, "version": __version__,
        "command": "audit",
        "folder": audit.folder.name,
        "files": audit.files,
        "agents_parsed": len(audit.agents),
        "unrecognized": unrecognized,
        "thresholds": {
            "collision": COLLISION_THRESHOLD,
            "identical": IDENTICAL_THRESHOLD,
            "short_description_chars": SHORT_DESCRIPTION_CHARS,
        },
        "agents": inventory,
        "findings": audit.findings,
        "summary": summary,
        "clean": summary["errors"] == 0,
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

SCOPE_LINE = ("Read-only: it edits nothing and runs "
              "nothing. It reads the agent definition "
              "files in the folder you point it at — "
              "every .md file in the tree is expected to "
              "be an agent file in the supported shape.")
METRIC_LINE = ("Similarity: the Jaccard index over each "
               "description's set of lowercase alphanumeric "
               "word tokens. Collision at "
               f"{COLLISION_THRESHOLD:.2f}, near-identical "
               f"at {IDENTICAL_THRESHOLD:.2f}; the short-"
               f"description floor is "
               f"{SHORT_DESCRIPTION_CHARS} characters. "
               "Thresholds and intent words are heuristics "
               "— see SKILL.md.")


def _where(finding):
    parts = []
    if finding["agents"]:
        parts.append(" + ".join(f"\"{n}\""
                                for n in finding["agents"]))
    if finding["files"]:
        parts.append(", ".join(finding["files"]))
    return " · ".join(parts) if parts else "fleet"


def render_text(audit):
    lines = [
        f"fleet-audit v{__version__} — audit · folder "
        f"{audit['folder']}",
        f"{audit['files']} agent files · "
        f"{audit['agents_parsed']} agents parsed · "
        f"{audit['unrecognized']} unrecognized · walk: "
        f"recursive (subfolders included)",
        SCOPE_LINE,
        METRIC_LINE,
        "",
    ]
    if audit["agents"]:
        lines.append(
            f"INVENTORY ({len(audit['agents'])}):")
        for a in audit["agents"]:
            if a["tools"] is None:
                tools = "(not specified — inherits all)"
            elif not a["tools"]:
                tools = "(explicitly empty)"
            else:
                tools = ", ".join(a["tools"])
            model = a["model"] or "(default)"
            lines.append(
                f"  {a['name']} — "
                f"{a['description_chars']} chars · "
                f"tools: {tools} · model: {model} · "
                f"{a['file']}")
        lines.append("")
    findings = audit["findings"]
    if not findings:
        lines.append("No findings — every agent passes "
                     "the static rules.")
    else:
        sections = [
            (ERROR, "ERRORS — agents that cannot be routed "
                    "to, collide head-on, or cannot be read"),
            (WARN, "WARNINGS — collisions and mismatches "
                   "that will misroute work"),
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
            f"fleet has agents that cannot be routed to, "
            f"collide head-on, or cannot be read at all. "
            f"Fix the errors before trusting delegation "
            f"to this fleet.")
    elif s["warnings"]:
        lines.append(
            f"Verdict: NO BROKEN AGENTS — but "
            f"{s['warnings']} warning(s) describe "
            f"collisions or mismatches that will misroute "
            f"work.")
    else:
        lines.append("Verdict: CLEAN — no collisions, "
                     "mismatches or unreadable files found "
                     "by the static rules.")
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
        description="Statically audit a folder of Claude "
                    "Code style subagent definitions: "
                    "description collisions that make "
                    "routing a coin flip, tool grants that "
                    "do not match the promised job, "
                    "duplicate names, unreadable files. "
                    "Read-only, runs nothing (stdlib only, "
                    "local only).")
    sub = ap.add_subparsers(dest="command", required=True,
                            parser_class=_JsonAwareParser)

    p_audit = sub.add_parser(
        "audit", help="audit one folder of agent "
                      "definitions (walked recursively): "
                      "exit 1 when any agent is broken as "
                      "defined (an ERROR finding)")
    p_audit.add_argument(
        "folder",
        help="the folder of agent definition files "
             "(e.g. .claude/agents)")
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
