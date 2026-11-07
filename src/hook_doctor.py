#!/usr/bin/env python3
"""hook-doctor — a static audit of your OWN Claude Code hook
configuration: the guards that can never fire, and the guards
that can never block, found before you rely on them.

Pure Python standard library. READ-ONLY and local only: it
reads the ONE settings file you point it at (plus the hook
scripts that file itself names, to judge their intent), edits
nothing, and NEVER executes a hook command.

Agent hooks fail silently. A safety guard that never ran and
a guard that was never configured look identical from inside
a session — successful hooks are quiet, failed ones are
non-blocking. The causes, though, are mostly static: readable
off two files, the settings JSON and the hook scripts,
without running anything. Four debugging guides in two months
enumerate the same list. The runtime offers `/hooks` (shows
what loaded, diagnoses nothing) and `--debug` (after the
fact). This tool is the check that runs BEFORE: point it at a
settings file and it applies an explicit rule table (see
RULES below and SKILL.md) to every hook in it.

THE RULES (v0.1.0 — each with its basis in the documented
hook semantics; where behaviour is version-dependent or the
check is a heuristic, the finding says so):

  INVALID_JSON (ERROR) — the settings file is not valid
    JSON, or its top level is not an object. Claude Code
    loads nothing from a file it cannot parse — every hook
    AND permission rule in it is inert. The parse position
    is reported.
  HOOKS_SHAPE (ERROR / WARN) — the hooks structure cannot
    be used as written: `hooks` not an object, an event's
    value not a list, a group without a `hooks` list, a
    command handler with no command (ERRORs); or an event
    name outside the documented set this tool knows — if
    that is a typo, the group never fires (WARN).
  MATCHER_CASE (ERROR) — on a tool event, a matcher
    alternative that is a case-variant of a documented tool
    name ("bash" for "Bash"). Matchers are case-sensitive,
    so it can never match the tool it names.
  MATCHER_NEVER_MATCHES (WARN) — a matcher on an event that
    takes no matcher (it is silently ignored and the hook
    runs unfiltered); a tool name used as the matcher on an
    event that matches a different field (SessionStart
    matches the session source, not tools); or a matcher
    alternative that names an event instead of a tool.
  EXIT_CODE_TRAP (WARN, heuristic) — a guard-intent hook
    (the command or its script says guard / block / deny /
    prevent / forbid / protect) on an event that can block,
    with no blocking signal in the command or the script:
    no `exit 2`, no JSON decision. Only exit 2 blocks; exit
    1 — and every other code — is a non-blocking error and
    the action proceeds. On PermissionRequest only a JSON
    decision counts: exit codes alone never deny there.
  EVENT_NOT_BLOCKING (WARN) — a guard-intent hook on an
    event that cannot block at all (PostToolUse and
    PostToolUseFailure fire after the call already ran;
    Notification, SessionStart, SessionEnd decide nothing).
    The guard watches the thing happen and cannot stop it.
  COMMAND_PATH (ERROR / WARN) — a path the hook command
    itself names does not exist, resolved against the
    settings file's folder and the home folder (ERROR: the
    hook can never run); or a script the command runs
    directly exists but is not executable (WARN).
  DUPLICATE_HOOK (WARN) — the identical handler registered
    twice for the same event and matcher: it runs once per
    registration.
  TIMEOUT_MISSING (INFO) — a handler with no `timeout`:
    if it hangs, the (version-dependent) default applies.

WHAT IT DOES NOT JUDGE (stated plainly): matchers that use
regex characters are treated as regexes by current Claude
Code versions and as literal strings by older ones — this
tool takes no side and does not flag them. Paths behind
$CLAUDE_PROJECT_DIR cannot be resolved statically and are
skipped. Unknown matcher names on tool events may be MCP
tools (mcp__…) or newer tools and are not flagged. Whether a
guard's LOGIC is right is not a static question — only
whether it can fire and can block.

THE LIMIT: a finding means "the configuration, as written,
has this defect by the documented rules" — it is a statement
about the file, not a run. A clean audit means no hook is
dead or misfiring BY THESE RULES, not that the hooks do
their jobs well.

Exit codes: 0 = no ERROR findings (WARN/INFO may exist) ·
1 = at least one ERROR finding · 2 = input error — clean
message, never a traceback; with --json the error is
{"error": ...}.
"""
import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path

__version__ = "0.1.0"
TOOL = "hook-doctor"

# Command excerpts in findings are truncated to this many
# characters, in text and JSON output alike.
EXCERPT_LIMIT = 120

ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"
_SEVERITY_RANK = {ERROR: 0, WARN: 1, INFO: 2}

# How much of a referenced hook script is read when judging
# guard intent and blocking signals.
_SCRIPT_READ_LIMIT = 65536


class ToolError(Exception):
    """A clean, user-facing failure (printed as ERROR, exit 2)."""


# --------------------------------------------------------------------------
# the documented landscape the rules are checked against.
# Tool and event names are the commonly documented ones —
# labelled as such because both lists grow between versions.
# --------------------------------------------------------------------------

KNOWN_TOOLS = {
    "AskUserQuestion", "Bash", "BashOutput", "Edit",
    "EnterPlanMode", "ExitPlanMode", "Glob", "Grep",
    "KillShell", "LS", "MultiEdit", "NotebookEdit", "Read",
    "Skill", "Task", "TodoWrite", "WebFetch", "WebSearch",
    "Write",
}
_TOOLS_BY_LOWER = {t.lower(): t for t in KNOWN_TOOLS}

# Events whose matcher filters on the tool name.
TOOL_MATCHER_EVENTS = {
    "PreToolUse", "PostToolUse", "PostToolUseFailure",
    "PermissionRequest",
}

# Events that take no matcher at all — one is silently
# ignored and the hook fires every time.
NO_MATCHER_EVENTS = {
    "CwdChanged", "MessageDisplay", "PostToolBatch", "Stop",
    "TaskCompleted", "TaskCreated", "TeammateIdle",
    "UserPromptSubmit", "WorktreeCreate", "WorktreeRemove",
}

# Events whose matcher filters on something else — the value
# it is matched against, for the finding messages.
OTHER_MATCHER_EVENTS = {
    "Notification": "the notification type",
    "PreCompact": "the compaction trigger (manual or auto)",
    "SessionStart": "how the session started (startup, "
                    "resume, clear, compact)",
    "SubagentStart": "the subagent's name",
    "SubagentStop": "the subagent's name",
}

# Events that can refuse the action they guard.
BLOCKING_EVENTS = {
    "PermissionRequest", "PreToolUse", "Stop", "SubagentStop",
    "UserPromptSubmit",
}

# Events that cannot block the action they are attached to.
NON_BLOCKING_EVENTS = {
    "Notification", "PostToolUse", "PostToolUseFailure",
    "SessionEnd", "SessionStart",
}

KNOWN_EVENTS = (
    TOOL_MATCHER_EVENTS | NO_MATCHER_EVENTS
    | set(OTHER_MATCHER_EVENTS) | BLOCKING_EVENTS
    | NON_BLOCKING_EVENTS
    | {"ConfigChange", "Elicitation", "ElicitationResult",
       "FileChanged", "InstructionsLoaded",
       "PermissionDenied", "SessionEnd", "Setup",
       "StopFailure"}
)

# Words that mark a hook as a guard: it exists to STOP
# something. Read off the command, the script's name, and
# the script's content when the script exists.
_GUARD_INTENT_RE = re.compile(
    r"(?<![a-z])(?:guard|block|deny|prevent|forbid|protect)"
    r"(?![a-z])",
    re.IGNORECASE)

# Signals that a hook CAN block: exit 2, or a JSON decision.
_EXIT_TWO_RES = [
    re.compile(r"\bexit\s+2\b"),
    re.compile(r"\bexit\s*\(\s*2\s*\)"),
    re.compile(r"\bsys\.exit\s*\(\s*2\s*\)"),
]
_DECISION_RES = [
    re.compile(r"permissionDecision"),
    re.compile(r"decision\W{0,4}block", re.IGNORECASE),
    re.compile(r"hookSpecificOutput"),
]

# Characters that take a matcher out of exact-name territory
# (regex land — version-dependent, deliberately not judged).
_REGEX_CHARS = set(".^$+?()[]{}\\")


def _truncate(text):
    if len(text) <= EXCERPT_LIMIT:
        return text
    return text[:EXCERPT_LIMIT - 1].rstrip() + "…"


# --------------------------------------------------------------------------
# command scanning — tokens, paths, intent, signals.
# Only the paths the command itself names are ever touched,
# and only to ask whether they exist.
# --------------------------------------------------------------------------

def _command_tokens(command):
    """Split a hook command the way a shell would (quotes
    honoured); fall back to a plain split on unbalanced
    quotes so scanning can continue."""
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.replace('"', " ").replace("'", " ") \
            .split()


def _is_command_position(tokens, index):
    """True when tokens[index] is run as a program: the first
    token, or the first token after a shell operator."""
    if index == 0:
        return True
    return tokens[index - 1] in ("&&", "||", ";", "|", "&")


def _path_candidate(token):
    """Classify a token: "check" (a path this tool resolves
    and existence-checks), "skip" (a path it deliberately
    does not judge — behind $CLAUDE_PROJECT_DIR, or a glob),
    or None (not a path at all)."""
    if not token or token.startswith("-"):
        return None
    if token in ("&&", "||", ";", "|", "&"):
        return None
    if token.startswith("$CLAUDE_PROJECT_DIR") \
            or token.startswith("${CLAUDE_PROJECT_DIR}"):
        return "skip"
    if any(c in token for c in "*?["):
        return "skip"
    if token.startswith(("/", "./", "../", "~/", "$HOME")):
        return "check"
    if "/" in token and "=" not in token.split("/", 1)[0]:
        return "check"
    return None


def _resolve(token, settings_dir, home):
    """The candidate filesystem paths for a token, in the
    order they are tried: absolute as-is; ~/ and $HOME
    against the home folder; relative against the settings
    file's folder, then the home folder."""
    if token.startswith("/"):
        return [Path(token)]
    if token.startswith("~/"):
        return [home / token[2:]]
    if token.startswith("${HOME}/"):
        return [home / token[8:]]
    if token.startswith("$HOME/"):
        return [home / token[6:]]
    if token == "$HOME" or token == "${HOME}":
        return [home]
    return [settings_dir / token, home / token]


def _read_script(path, cache):
    """The first bytes of a referenced script, for intent and
    signal checks — read only, never executed. Unreadable or
    binary content yields whatever text could be decoded."""
    key = str(path)
    if key not in cache:
        try:
            data = path.read_bytes()[:_SCRIPT_READ_LIMIT]
            cache[key] = data.decode("utf-8", errors="replace")
        except OSError:
            cache[key] = ""
    return cache[key]


def _guard_intent(command, script_text):
    """Does this hook present as a guard? The command or the
    script it runs says guard / block / deny / prevent /
    forbid / protect."""
    return bool(_GUARD_INTENT_RE.search(command)
                or _GUARD_INTENT_RE.search(script_text))


def _blocking_signal(command, script_text, event):
    """Is there a signal this hook can actually block with?
    Exit 2, or a JSON decision — except on PermissionRequest,
    where exit codes alone never deny and only a JSON
    decision counts (documented semantics)."""
    texts = (command, script_text)
    if event != "PermissionRequest":
        if any(rx.search(t) for rx in _EXIT_TWO_RES for t in
               texts):
            return True
    return any(rx.search(t) for rx in _DECISION_RES
               for t in texts)


# --------------------------------------------------------------------------
# the audit
# --------------------------------------------------------------------------

class _Audit:
    def __init__(self, settings_path, home):
        self.settings_path = settings_path
        self.settings_dir = settings_path.parent
        self.home = home
        self.findings = []
        self.hooks = 0
        self.events = []
        self.parse_error = False
        self._script_cache = {}
        self._seen_handlers = set()

    def add(self, rule, severity, message, fix, event=None,
            matcher=None, command=None):
        self.findings.append({
            "rule": rule,
            "severity": severity,
            "event": event,
            "matcher": matcher,
            "command": _truncate(command) if command else None,
            "message": message,
            "fix": fix,
        })


def _check_matcher(audit, event, matcher, command):
    """Matcher rules for one group. Only ever adds findings
    it can stand behind: unknown plain names on tool events
    may be MCP or newer tools and are left alone, and
    regex-flavoured matchers are version-dependent and left
    alone (both stated limits of v0.1.0)."""
    if matcher.strip() in ("", "*"):
        return
    alternatives = [alt.strip() for alt in
                    re.split(r"[|,]", matcher) if alt.strip()]
    if event in NO_MATCHER_EVENTS:
        audit.add(
            "MATCHER_NEVER_MATCHES", WARN,
            f"{event} hooks do not take a matcher — this one "
            f"is silently ignored and the hook runs on every "
            f"{event}",
            "remove the matcher, or move the hook to an event "
            "that filters (tool events match on the tool name)",
            event=event, matcher=matcher, command=command)
        return
    if event in OTHER_MATCHER_EVENTS:
        field = OTHER_MATCHER_EVENTS[event]
        for alt in alternatives:
            if alt.startswith("mcp__") or "*" in alt \
                    or any(c in alt for c in _REGEX_CHARS):
                continue
            if alt in KNOWN_TOOLS \
                    or alt.lower() in _TOOLS_BY_LOWER:
                audit.add(
                    "MATCHER_NEVER_MATCHES", WARN,
                    f"{event} matchers match {field}, not tool "
                    f"names — \"{alt}\" can never match",
                    "use one of the values this event actually "
                    "matches on, or move the hook to a tool "
                    "event",
                    event=event, matcher=matcher,
                    command=command)
        return
    if event not in TOOL_MATCHER_EVENTS:
        return  # unknown event: shape finding already added
    for alt in alternatives:
        if alt == "*" or alt.startswith("mcp__") \
                or "*" in alt \
                or any(c in alt for c in _REGEX_CHARS):
            continue
        if alt in KNOWN_TOOLS:
            continue
        proper = _TOOLS_BY_LOWER.get(alt.lower())
        if proper is not None:
            audit.add(
                "MATCHER_CASE", ERROR,
                f"matcher \"{alt}\" can never match the "
                f"{proper} tool — matchers are case-sensitive "
                f"and tool names are exact",
                f"change the matcher to \"{proper}\"",
                event=event, matcher=matcher, command=command)
        elif alt in KNOWN_EVENTS:
            audit.add(
                "MATCHER_NEVER_MATCHES", WARN,
                f"matcher \"{alt}\" names an event, not a "
                f"tool — no tool call will ever match it on "
                f"{event}",
                "match on the tool name this hook should run "
                "for (or remove the matcher to run for every "
                "tool)",
                event=event, matcher=matcher, command=command)


def _check_command(audit, event, matcher, handler):
    """Command rules for one handler: the paths it names,
    the guard traps, duplicates, the missing timeout."""
    command = handler["command"]
    tokens = _command_tokens(command)
    script_text = ""
    for index, token in enumerate(tokens):
        kind = _path_candidate(token)
        if kind != "check":
            continue
        candidates = _resolve(token, audit.settings_dir,
                              audit.home)
        found = next((c for c in candidates if c.exists()),
                     None)
        if found is None:
            audit.add(
                "COMMAND_PATH", ERROR,
                f"the command path {token} does not exist "
                f"(resolved against the settings folder and "
                f"the home folder) — this hook can never run",
                "create the script at that path, or point the "
                "command at a script that exists",
                event=event, matcher=matcher, command=command)
            continue
        if not script_text:
            script_text = _read_script(found,
                                       audit._script_cache)
        if _is_command_position(tokens, index) \
                and found.is_file() \
                and not os.access(found, os.X_OK):
            audit.add(
                "COMMAND_PATH", WARN,
                f"{token} exists but is not executable, and "
                f"the hook runs it directly — the run will "
                f"fail with a permission error",
                "make it executable (chmod +x), or invoke it "
                "through its interpreter (e.g. python3 "
                "<script>)",
                event=event, matcher=matcher, command=command)

    intent = _guard_intent(command, script_text)
    if intent and event in NON_BLOCKING_EVENTS:
        audit.add(
            "EVENT_NOT_BLOCKING", WARN,
            f"guard-intent hook on {event}: this event "
            f"cannot block the action it guards — it fires "
            f"after the fact or decides nothing, so the "
            f"guard watches the thing happen",
            "move the guard to PreToolUse, the tool event "
            "that can refuse a call — or drop the blocking "
            "intent and keep this as an observer",
            event=event, matcher=matcher, command=command)
    elif intent and event in BLOCKING_EVENTS \
            and not _blocking_signal(command, script_text,
                                     event):
        audit.add(
            "EXIT_CODE_TRAP", WARN,
            f"guard-intent hook on {event} shows no way to "
            f"block: no exit 2 and no JSON decision in the "
            f"command or the script it runs — exit 1 (and "
            f"every other code) is a non-blocking error and "
            f"the action proceeds. Heuristic: the intent was "
            f"read from guard words (guard / block / deny / "
            f"prevent / forbid / protect) in the command or "
            f"script",
            "exit 2 with the reason on stderr, or print a "
            "JSON decision (permissionDecision: deny on "
            "PreToolUse) when the guard should stop the "
            "action",
            event=event, matcher=matcher, command=command)

    key = (event, matcher or "", command)
    if key in audit._seen_handlers:
        audit.add(
            "DUPLICATE_HOOK", WARN,
            f"this exact handler is registered more than "
            f"once for {event} — it will run once per "
            f"registration",
            "remove the duplicate registration",
            event=event, matcher=matcher, command=command)
    else:
        audit._seen_handlers.add(key)


def _check_handler(audit, event, matcher, handler):
    if not isinstance(handler, dict):
        audit.add(
            "HOOKS_SHAPE", ERROR,
            f"a handler under {event} is a "
            f"{type(handler).__name__}, not an object — it "
            f"can never run",
            "make each handler an object with a \"type\" and "
            "its \"command\" (or other handler fields)",
            event=event, matcher=matcher)
        return
    audit.hooks += 1
    htype = handler.get("type", "command")
    command = handler.get("command")
    if htype == "command":
        if not isinstance(command, str) or not command.strip():
            audit.add(
                "HOOKS_SHAPE", ERROR,
                f"a command handler under {event} has no "
                f"command — it can never run",
                "give the handler the command to run, or "
                "remove the handler",
                event=event, matcher=matcher)
            command = None
    else:
        command = None  # prompt / http / agent handlers:
        # no command to check (stated limit)
    if command is not None:
        _check_command(audit, event, matcher, handler)
    if "timeout" not in handler:
        audit.add(
            "TIMEOUT_MISSING", INFO,
            f"a handler under {event} sets no timeout — if "
            f"it hangs, the (version-dependent) default "
            f"timeout applies before anything else can "
            f"proceed",
            "set an explicit \"timeout\" in seconds on the "
            "handler",
            event=event, matcher=matcher,
            command=command)


def _check_group(audit, event, group):
    if not isinstance(group, dict):
        audit.add(
            "HOOKS_SHAPE", ERROR,
            f"a matcher group under {event} is a "
            f"{type(group).__name__}, not an object — its "
            f"hooks can never run",
            "make each group an object with \"matcher\" and "
            "a \"hooks\" list",
            event=event)
        return
    matcher = group.get("matcher")
    if matcher is not None and not isinstance(matcher, str):
        audit.add(
            "HOOKS_SHAPE", ERROR,
            f"the matcher under {event} is a "
            f"{type(matcher).__name__}, not a string — the "
            f"group can never match",
            "use a string matcher (a tool name, a \"|\"-list "
            "of names, or omit it to match everything)",
            event=event)
        matcher = None
    handlers = group.get("hooks")
    if not isinstance(handlers, list):
        audit.add(
            "HOOKS_SHAPE", ERROR,
            f"a group under {event} has no \"hooks\" list — "
            f"its hooks can never run",
            "give the group a \"hooks\" list of handlers",
            event=event,
            matcher=matcher if isinstance(matcher, str)
            else None)
        return
    first_command = next(
        (h.get("command") for h in handlers
         if isinstance(h, dict)
         and isinstance(h.get("command"), str)),
        None)
    if isinstance(matcher, str):
        _check_matcher(audit, event, matcher, first_command)
    for handler in handlers:
        _check_handler(audit, event,
                       matcher if isinstance(matcher, str)
                       else None,
                       handler)


def audit_settings(path, home):
    """Read + check one settings file. Returns the full
    audit dict (the JSON shape; the text view renders from
    it). Raises ToolError only for input failures — a file
    that exists but is broken JSON is a FINDING, because
    that is exactly the defect the tool exists to report."""
    if not path.is_file():
        raise ToolError(f"settings file not found: {path}")
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolError(
            f"could not read settings file {path.name} "
            f"({exc.__class__.__name__})")
    audit = _Audit(path, home)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        audit.parse_error = True
        audit.add(
            "INVALID_JSON", ERROR,
            f"the file is not valid JSON — line {exc.lineno}"
            f", column {exc.colno}: {exc.msg}. Claude Code "
            f"loads nothing from a file it cannot parse: "
            f"every hook and permission rule in it is inert",
            "fix the JSON at the reported position (a "
            "trailing comma and unquoted keys are the usual "
            "causes)")
        return _finish(audit)
    if not isinstance(data, dict):
        audit.parse_error = True
        audit.add(
            "INVALID_JSON", ERROR,
            f"the top level of the file is a JSON "
            f"{type(data).__name__}, not an object — settings "
            f"are read as an object, so nothing in this file "
            f"loads",
            "make the file a single JSON object with a "
            "\"hooks\" key")
        return _finish(audit)
    hooks = data.get("hooks")
    if hooks is None:
        return _finish(audit)  # no hooks configured: clean
    if not isinstance(hooks, dict):
        audit.add(
            "HOOKS_SHAPE", ERROR,
            f"\"hooks\" is a {type(hooks).__name__}, not an "
            f"object keyed by event name — no hook in it can "
            f"load",
            "shape \"hooks\" as an object: event name → list "
            "of matcher groups",
            event=None)
        return _finish(audit)
    for event in sorted(hooks):
        audit.events.append(event)
        groups = hooks[event]
        if event not in KNOWN_EVENTS:
            audit.add(
                "HOOKS_SHAPE", WARN,
                f"\"{event}\" is not one of the documented "
                f"hook events this tool knows — if it is a "
                f"typo, every hook under it never fires",
                "check the event name against the hooks "
                "documentation for your version",
                event=event)
        if not isinstance(groups, list):
            audit.add(
                "HOOKS_SHAPE", ERROR,
                f"the value under \"{event}\" is a "
                f"{type(groups).__name__}, not a list of "
                f"matcher groups — its hooks can never run",
                "make it a list of groups, each with a "
                "\"hooks\" list",
                event=event)
            continue
        for group in groups:
            _check_group(audit, event, group)
    return _finish(audit)


def _finish(audit):
    audit.findings.sort(key=lambda f: (
        _SEVERITY_RANK[f["severity"]],
        f["event"] or "",
        f["rule"],
        f["matcher"] or "",
        f["command"] or "",
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
    return {
        "tool": TOOL, "version": __version__,
        "command": "audit",
        "settings": audit.settings_path.name,
        "home": str(audit.home),
        "hooks": audit.hooks,
        "events": audit.events,
        "parse_error": audit.parse_error,
        "findings": audit.findings,
        "summary": summary,
        "clean": summary["errors"] == 0,
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

SCOPE_LINE = ("Read-only: it edits nothing and never runs a "
              "hook command. It reads the settings file, "
              "plus the scripts that file itself names.")


def _where(finding):
    parts = [finding["event"] or "settings file"]
    if finding["matcher"]:
        parts.append(f"matcher \"{finding['matcher']}\"")
    if finding["command"]:
        parts.append(finding["command"])
    return " · ".join(parts)


def render_text(audit):
    lines = [
        f"hook-doctor v{__version__} — audit · settings "
        f"{audit['settings']}",
    ]
    if audit["parse_error"]:
        lines.append("The settings file does not parse — no "
                     "hook in it can load.")
    else:
        lines.append(
            f"{audit['hooks']} hook handlers across "
            f"{len(audit['events'])} events · home for ~ and "
            f"$HOME paths: {audit['home']}")
    lines.append(SCOPE_LINE)
    lines.append("")
    findings = audit["findings"]
    if not findings:
        if audit["hooks"] == 0 and not audit["parse_error"]:
            lines.append("No hooks configured in this file — "
                         "nothing to audit.")
        else:
            lines.append("No findings — every hook passes "
                         "the static rules.")
    else:
        sections = [
            (ERROR, "ERRORS — hooks that can never work as "
                    "configured"),
            (WARN, "WARNINGS — hooks that probably do not do "
                   "what you intend"),
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
            f"Verdict: FAIL — {s['errors']} error(s): at "
            f"least one hook is dead as configured. Fix the "
            f"errors before relying on these hooks.")
    elif s["warnings"]:
        lines.append(
            f"Verdict: NO DEAD HOOKS — but {s['warnings']} "
            f"warning(s) describe hooks that probably do not "
            f"do what you intend.")
    elif audit["hooks"] == 0 and not audit["parse_error"]:
        lines.append("Verdict: CLEAN — nothing was "
                     "configured, so nothing is dead.")
    else:
        lines.append("Verdict: CLEAN — no dead hooks or "
                     "misfires found by the static rules.")
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
    path = Path(a.settings)
    home = Path(a.home) if a.home else Path.home()
    audit = audit_settings(path, home)
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
        description="Statically audit a Claude Code "
                    "settings file's hooks: matchers that "
                    "can never fire, guards that can never "
                    "block, commands pointing at missing "
                    "files, invalid JSON. Read-only, executes "
                    "nothing (stdlib only, local only).")
    sub = ap.add_subparsers(dest="command", required=True,
                            parser_class=_JsonAwareParser)

    p_audit = sub.add_parser(
        "audit", help="audit one settings file: check every "
                      "hook against the static rule table; "
                      "exit 1 when any hook is dead as "
                      "configured (an ERROR finding)")
    p_audit.add_argument("settings",
                         help="the settings file to audit "
                              "(e.g. .claude/settings.json)")
    p_audit.add_argument(
        "--home", default=None,
        help="the home folder ~ and $HOME paths resolve "
             "against (default: your real home folder)")
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
