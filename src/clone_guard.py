#!/usr/bin/env python3
"""clone-guard — scan a repo BEFORE you or your agent opens
it, and report the surfaces that execute code on open, on
install, or on git events.

Pure Python standard library. Local only: no network, no
uploads, no accounts. And read-only in the strongest sense:
the scanner opens the files it checks and reads them — it
never executes, installs, sources or modifies anything in
the folder it scans. (The test suite proves this by hashing
the whole scanned tree before and after a scan.)

Why this exists: in August 2026 a worm campaign planted
files in public repos that run code the moment a developer
— or an AI coding agent — opens the cloned folder, with no
install step at all. The vectors were editor/agent config
files, not the code itself. clone-guard checks exactly
those config surfaces, prints every command it finds
verbatim, and lets a human judge before anything opens the
folder.

What is checked (and ONLY this — ordinary project files
like Makefile, setup.py, *.sh, flake.nix are never flagged;
false positives would kill the tool's trust):

  .claude/settings.json and settings.local.json
      hooks: any "command" string under "hooks", per event
      (SessionStart, PreToolUse, ...) — AUTO-RUN. Plus
      "apiKeyHelper" — AUTO-RUN.
  .vscode/tasks.json
      tasks with runOptions.runOn == "folderOpen" —
      AUTO-RUN.
  .vscode/settings.json
      "task.allowAutomaticTasks": "on" — PRESENT (it
      removes the prompt that otherwise gates folderOpen
      tasks).
  .devcontainer/devcontainer.json
      initializeCommand / postCreateCommand /
      postStartCommand / postAttachCommand — AUTO-RUN
      (initializeCommand runs on the HOST).
  package.json
      scripts preinstall / install / postinstall —
      INSTALL-TIME.
  .envrc
      direnv executes it on cd — AUTO-RUN.
  .git/hooks/ (files not ending in .sample)
      git hooks — PRESENT. They do NOT travel with a fresh
      clone, but they do travel in archives, tarballs and
      copied folders — so a hook already sitting in a
      "fresh clone" is itself a signal.
  .husky/ (hook files, not the _ internals)
      husky hooks — PRESENT (they fire on git events once
      husky is installed).

Config files are JSON or JSON-with-comments in the wild,
so parsing is lenient: // and /* */ comments and trailing
commas are tolerated (string-aware — a // inside a URL in a
string is NOT treated as a comment). A checked file that
still cannot be parsed is reported as an UNPARSEABLE
finding — it could not be cleared — never a crash.

Verdicts say WHEN a thing runs, not WHETHER it is evil:
plenty of legitimate projects use hooks, devcontainers and
install scripts. The finding shows the exact command; the
human judges the command.

Exit codes: 0 = CLEAN (no findings) · 1 = FINDINGS (any
auto-run, install-time, present or unparseable finding) ·
2 = ERROR — clean message, never a traceback; with --json
the error is {"error": ...}. --json prints one JSON object
with the same facts and exit codes as the text view.
"""
import argparse
import json
import os
import sys
from pathlib import Path

__version__ = "0.1.0"
TOOL = "clone-guard"

AUTO_RUN = "AUTO-RUN"
INSTALL_TIME = "INSTALL-TIME"
PRESENT = "PRESENT"
UNPARSEABLE = "UNPARSEABLE"
_VERDICT_ORDER = [AUTO_RUN, INSTALL_TIME, PRESENT, UNPARSEABLE]

_GROUP_HEADERS = {
    AUTO_RUN: "AUTO-RUN — executes on open, session start "
              "or cd, with no install step",
    INSTALL_TIME: "INSTALL-TIME — executes when "
                  "dependencies are installed",
    PRESENT: "PRESENT — on disk; executes only via its "
             "tool (git / husky) or by removing a prompt",
    UNPARSEABLE: "UNPARSEABLE — could not be parsed, so "
                 "its contents could not be checked",
}


class ToolError(Exception):
    """A clean, user-facing failure (printed as ERROR, exit 2)."""


# --------------------------------------------------------------------------
# Lenient JSON (JSONC): comments + trailing commas, string-aware
# --------------------------------------------------------------------------

def _strip_comments(text):
    """Remove // line and /* */ block comments, honouring
    string literals (a // inside a quoted URL is data)."""
    out = []
    i, n = 0, len(text)
    in_str = esc = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*"
                                     and text[i + 1] == "/"):
                i += 1
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _strip_trailing_commas(text):
    """Remove a comma whose next non-whitespace character is
    } or ], outside string literals."""
    out = []
    i, n = 0, len(text)
    in_str = esc = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_lenient(text):
    """(obj, None) on success, (None, error message) on
    failure. Tries strict JSON first, then the JSONC-tolerant
    form (comments stripped, trailing commas stripped)."""
    text = text.lstrip("\ufeff")
    try:
        return json.loads(text), None
    except ValueError:
        pass
    try:
        return json.loads(
            _strip_trailing_commas(_strip_comments(text))), None
    except ValueError as exc:
        return None, f"could not be parsed as JSON: {exc}"


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------

def _finding(file, rule, verdict, trigger, command=None,
             detail=None):
    return {"file": file, "rule": rule, "verdict": verdict,
            "trigger": trigger, "command": command,
            "detail": detail}


def _unparseable(rel, detail):
    return _finding(
        rel, "unparseable", UNPARSEABLE,
        "this file is one clone-guard checks, but it could "
        "not be parsed — its contents could not be checked",
        detail=detail)


def _load_json(d, rel, findings, probed):
    """Parse DIR/rel leniently. Returns the top-level dict,
    or None when the file is absent. A file that exists but
    cannot be read, decoded or parsed (or is not a JSON
    object) adds an UNPARSEABLE finding and returns None."""
    probed.append(rel)
    p = d / rel
    if not p.is_file():
        return None
    try:
        text = p.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        findings.append(_unparseable(
            rel, f"could not be read as UTF-8 text: {exc}"))
        return None
    data, err = parse_lenient(text)
    if data is None:
        findings.append(_unparseable(rel, err))
        return None
    if not isinstance(data, dict):
        findings.append(_unparseable(
            rel, "the top level is not a JSON object"))
        return None
    return data


def _line_count(path):
    try:
        return len(path.read_bytes().splitlines())
    except OSError:
        return 0


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

def _collect_commands(node, out):
    """Every string value of a "command" key, anywhere in
    the subtree (Claude hook groups nest commands two levels
    deep, and shapes vary by version)."""
    if isinstance(node, dict):
        cmd = node.get("command")
        if isinstance(cmd, str):
            out.append(cmd)
        for v in node.values():
            _collect_commands(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_commands(v, out)


def rule_claude(d, findings, probed):
    for rel in (".claude/settings.json",
                ".claude/settings.local.json"):
        data = _load_json(d, rel, findings, probed)
        if data is None:
            continue
        hooks = data.get("hooks")
        if isinstance(hooks, dict):
            for event in sorted(hooks):
                commands = []
                _collect_commands(hooks[event], commands)
                for cmd in dict.fromkeys(commands):
                    findings.append(_finding(
                        rel, "claude-hook", AUTO_RUN,
                        f"Claude Code {event} hook — runs "
                        f"automatically when Claude Code "
                        f"works in this folder",
                        command=cmd))
        helper = data.get("apiKeyHelper")
        if isinstance(helper, str):
            findings.append(_finding(
                rel, "claude-api-key-helper", AUTO_RUN,
                "Claude Code apiKeyHelper — a command "
                "Claude Code runs to obtain API credentials",
                command=helper))


def rule_vscode(d, findings, probed):
    rel = ".vscode/tasks.json"
    data = _load_json(d, rel, findings, probed)
    if data is not None:
        tasks = data.get("tasks")
        if isinstance(tasks, list):
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                run_options = task.get("runOptions")
                if not (isinstance(run_options, dict)
                        and run_options.get("runOn")
                        == "folderOpen"):
                    continue
                label = task.get("label") or "(no label)"
                command = task.get("command")
                detail = None
                if isinstance(command, str):
                    args = task.get("args")
                    if (isinstance(args, list) and args
                            and all(isinstance(a, str)
                                    for a in args)):
                        command = command + " " + " ".join(args)
                else:
                    command = None
                    detail = (f"task '{label}' declares no "
                              f"command of its own — it may "
                              f"chain other tasks via dependsOn")
                findings.append(_finding(
                    rel, "vscode-folderopen-task", AUTO_RUN,
                    f"VS Code task '{label}' with runOn: "
                    f"folderOpen — runs when this folder is "
                    f"opened in VS Code",
                    command=command, detail=detail))
    rel = ".vscode/settings.json"
    data = _load_json(d, rel, findings, probed)
    if data is not None:
        if data.get("task.allowAutomaticTasks") == "on":
            findings.append(_finding(
                rel, "vscode-automatic-tasks", PRESENT,
                "VS Code setting task.allowAutomaticTasks "
                "is \"on\"",
                detail="with automatic tasks on, VS Code "
                       "runs folderOpen tasks without "
                       "prompting first — the prompt is the "
                       "only gate on the tasks above"))


_DEVCONTAINER_TRIGGERS = {
    "initializeCommand":
        "devcontainer initializeCommand — runs on the "
        "HOST when the folder is reopened in a container, "
        "before the container exists",
    "postCreateCommand":
        "devcontainer postCreateCommand — runs inside "
        "the container right after it is created",
    "postStartCommand":
        "devcontainer postStartCommand — runs inside "
        "the container every time it starts",
    "postAttachCommand":
        "devcontainer postAttachCommand — runs inside "
        "the container when an editor or tool attaches",
}


def _cmd_value(v):
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return None


def _command_strings(value):
    """A devcontainer command may be a string, an argv
    list, or an object of name -> command. Flatten to the
    command strings."""
    if isinstance(value, dict):
        out = []
        for v in value.values():
            c = _cmd_value(v)
            if c is not None:
                out.append(c)
        return out
    c = _cmd_value(value)
    return [c] if c is not None else []


def rule_devcontainer(d, findings, probed):
    rel = ".devcontainer/devcontainer.json"
    data = _load_json(d, rel, findings, probed)
    if data is None:
        return
    for key, trigger in _DEVCONTAINER_TRIGGERS.items():
        if key in data:
            for cmd in _command_strings(data[key]):
                findings.append(_finding(
                    rel, "devcontainer-command", AUTO_RUN,
                    trigger, command=cmd))


def rule_npm(d, findings, probed):
    rel = "package.json"
    data = _load_json(d, rel, findings, probed)
    if data is None:
        return
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return
    for key in ("preinstall", "install", "postinstall"):
        cmd = scripts.get(key)
        if isinstance(cmd, str):
            findings.append(_finding(
                rel, "npm-install-script", INSTALL_TIME,
                f"npm \"{key}\" script — npm runs it "
                f"automatically during npm install",
                command=cmd))


def rule_direnv(d, findings, probed):
    rel = ".envrc"
    probed.append(rel)
    p = d / rel
    if p.is_file():
        findings.append(_finding(
            rel, "direnv-envrc", AUTO_RUN,
            "direnv file — direnv executes it automatically "
            "when you cd into this folder (when direnv is "
            "installed and the folder is allowed)",
            detail=f"{_line_count(p)} line(s) of shell — "
                   f"direnv sources the whole file; open "
                   f"it to inspect"))


def rule_git_hooks(d, findings, probed):
    probed.append(".git/hooks")
    hooks_dir = d / ".git" / "hooks"
    if not hooks_dir.is_dir():
        return
    for entry in sorted(os.scandir(hooks_dir),
                        key=lambda e: e.name):
        if entry.name.endswith(".sample"):
            continue
        if entry.is_dir(follow_symlinks=False):
            continue
        p = Path(entry.path)
        executable = os.access(p, os.X_OK)
        findings.append(_finding(
            f".git/hooks/{entry.name}", "git-hook", PRESENT,
            f"git hook '{entry.name}' — runs on git "
            f"events when executable",
            detail=f"{_line_count(p)} line(s); executable: "
                   f"{'yes' if executable else 'no'}. Git "
                   f"hooks do not travel with a fresh clone — "
                   f"but they do travel in archives, tarballs "
                   f"and copied folders, so a hook already "
                   f"here in a \"fresh clone\" is itself a "
                   f"signal. Open the file to inspect it"))


def rule_husky(d, findings, probed):
    probed.append(".husky")
    husky = d / ".husky"
    if not husky.is_dir():
        return
    for entry in sorted(os.scandir(husky),
                        key=lambda e: e.name):
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_dir(follow_symlinks=False):
            continue
        p = Path(entry.path)
        findings.append(_finding(
            f".husky/{entry.name}", "husky-hook", PRESENT,
            f"husky hook '{entry.name}' — runs on git "
            f"events once husky is installed (npm install "
            f"wires it up)",
            detail=f"{_line_count(p)} line(s); open the "
                   f"file to inspect it"))


_RULES = (rule_claude, rule_vscode, rule_devcontainer,
          rule_npm, rule_direnv, rule_git_hooks, rule_husky)


def scan(d):
    """All findings for DIR, sorted by verdict severity,
    then file, then trigger. Reads only — never writes,
    never executes."""
    findings, probed = [], []
    for rule in _RULES:
        rule(d, findings, probed)
    rank = {v: i for i, v in enumerate(_VERDICT_ORDER)}
    findings.sort(key=lambda f: (rank[f["verdict"]],
                                 f["file"], f["trigger"],
                                 f["command"] or ""))
    summary = {"total": len(findings),
               "auto_run": sum(1 for f in findings
                               if f["verdict"] == AUTO_RUN),
               "install_time": sum(
                   1 for f in findings
                   if f["verdict"] == INSTALL_TIME),
               "present": sum(1 for f in findings
                              if f["verdict"] == PRESENT),
               "unparseable": sum(
                   1 for f in findings
                   if f["verdict"] == UNPARSEABLE)}
    return {"findings": findings, "summary": summary,
            "surfaces_probed": len(probed),
            "clean": not findings}


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


def require_dir(raw):
    d = Path(raw)
    if not d.exists():
        raise ToolError(f"no such folder: {raw}")
    if not d.is_dir():
        raise ToolError(f"not a folder: {raw}")
    return d


def cmd_scan(a):
    d = require_dir(a.dir)
    result = scan(d)
    if a.json:
        print(json.dumps(
            {"tool": TOOL, "version": __version__,
             "command": "scan", "dir": str(d),
             "surfaces_probed": result["surfaces_probed"],
             "findings": result["findings"],
             "summary": result["summary"],
             "clean": result["clean"]}, indent=2))
        return 0 if result["clean"] else 1
    s = result["summary"]
    print(f"{TOOL} v{__version__} — scan · folder {d.name}")
    print(f"{d.name} · {s['total']} finding(s) — "
          f"{s['auto_run']} AUTO-RUN · "
          f"{s['install_time']} INSTALL-TIME · "
          f"{s['present']} PRESENT · "
          f"{s['unparseable']} UNPARSEABLE")
    if result["clean"]:
        print()
        print("CLEAN — none of the auto-execution surfaces "
              "clone-guard checks were found (Claude / VS "
              "Code / devcontainer configs, npm install "
              "scripts, direnv, git and husky hooks).")
        print("clone-guard only reads files: it never "
              "executes, installs or modifies anything in "
              "the scanned folder. CLEAN is not a guarantee "
              "the code itself is safe — it means these "
              "known open-time and install-time surfaces "
              "are absent. Read the code before you run it.")
        return 0
    for verdict in _VERDICT_ORDER:
        group = [f for f in result["findings"]
                 if f["verdict"] == verdict]
        if not group:
            continue
        print()
        print(f"{_GROUP_HEADERS[verdict]} ({len(group)}):")
        for f in group:
            print(f"  {f['file']} — {f['trigger']}")
            if f["command"] is not None:
                print(f"    command: {f['command']}")
            if f["detail"] is not None:
                print(f"    {f['detail']}")
    print()
    print("VERDICT: FINDINGS — read every command above "
          "before opening this folder in an editor or with "
          "an agent.")
    print("clone-guard only reads files: it never executes, "
          "installs or modifies anything in the scanned "
          "folder. Findings are auto-execution surfaces, "
          "not proof of malice — legitimate projects use "
          "hooks and install scripts too. The command text "
          "is the evidence; judge it.")
    return 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    _JsonAwareParser.json_mode = "--json" in argv
    ap = _JsonAwareParser(
        prog=TOOL,
        description="Scan a repo BEFORE opening it: report "
                    "the config surfaces that execute code "
                    "on open, on install, or on git events "
                    "(Claude/VS Code/devcontainer configs, "
                    "npm install scripts, direnv, git and "
                    "husky hooks), with every command shown "
                    "verbatim. Read-only: never executes "
                    "anything. (stdlib only, local only).")
    ap.add_argument("dir", help="the cloned repo / folder "
                                "to scan (not yet opened)")
    ap.add_argument("--json", action="store_true",
                    help="print one JSON object instead of "
                         "text")
    a = ap.parse_args(argv)

    def fail(msg):
        if getattr(a, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"ERROR: {msg}")
        return 2

    try:
        return cmd_scan(a)
    except ToolError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())
