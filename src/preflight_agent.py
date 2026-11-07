#!/usr/bin/env python3
"""preflight-agent — scan your AI-agent setup before it runs.

One suite, five auditors that used to be five separate tools,
merged (FLEET-AUDIT-2026-11-04, flagship B1) without changing
a single check:

    preflight-agent clone-guard  DIR
        What executes the moment this folder is opened,
        installed, or committed to? (Claude / VS Code /
        devcontainer configs, npm install scripts, direnv,
        git and husky hooks — every command shown verbatim.)
    preflight-agent secret-guard scan|fix FILE...
        Inline secrets in agent / MCP config files — found
        by key path and fingerprint, never by value; fix
        rewrites them as environment-variable references.
    preflight-agent hook-doctor  audit SETTINGS
        Hooks that can never fire, guards that can never
        block, commands pointing at missing files.
    preflight-agent fleet-audit  audit FOLDER
        A folder of agent definitions: description
        collisions, duplicate names, tool grants that do
        not match the promised job.
    preflight-agent skill-sprawl audit FOLDER
        A tree of skills: cross-scope duplicates,
        discovery collisions, oversized bodies, a
        truncation forecast.
    preflight-agent scan         DIR
        All five, pointed at one agent-setup folder, as a
        single combined receipt.

Every module keeps its source tool's checks, verdict words
and honesty limits — the engines are ported, not rewritten.
Read-only: no module's scan ever writes to, executes, or
installs anything in the scanned folder. The single
exception is secret-guard's fix command, which writes ONLY
when you run it with --apply (dry run otherwise, a
timestamped backup kept beside each changed file) — and
scan never calls it.

(stdlib only, local only, no network.)
"""

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import clone_guard
import fleet_audit
import hook_doctor
import secret_guard
import skill_sprawl

__version__ = "1.0.0"

TOOL = "preflight-agent"

MODULES = {
    "clone-guard": clone_guard,
    "secret-guard": secret_guard,
    "hook-doctor": hook_doctor,
    "fleet-audit": fleet_audit,
    "skill-sprawl": skill_sprawl,
}

_MODULE_BLURBS = {
    "clone-guard": "what runs when this folder is opened",
    "secret-guard": "inline secrets in agent/MCP configs",
    "hook-doctor": "dead hooks and guards that cannot block",
    "fleet-audit": "agent-definition collisions and grants",
    "skill-sprawl": "skill duplicates, collisions, bloat",
}

HELP = """usage: preflight-agent <command> [args...]

Scan your AI-agent setup before it runs — five auditors,
one suite. Every command accepts --json (one JSON object,
exit 0 clean / 1 findings / 2 usage or input error).

commands:
  clone-guard   DIR              what runs when this folder is opened
  secret-guard  scan|fix FILE... inline secrets in agent/MCP configs
  hook-doctor   audit SETTINGS   dead hooks and guards that cannot block
  fleet-audit   audit FOLDER     agent-definition collisions and grants
  skill-sprawl  audit FOLDER     skill duplicates, collisions, bloat
  scan          DIR              all five over one setup folder,
                                 as a single combined receipt

Run `preflight-agent <command> --help` for a module's own
options. Read-only: nothing here executes, installs or
modifies the scanned folder (secret-guard fix --apply is
the one deliberate exception, and scan never calls it)."""


class ToolError(Exception):
    """A user-facing failure: printed without a traceback,
    exit code 2 (same convention as the five modules)."""


class _JsonAwareParser(argparse.ArgumentParser):
    """ArgumentParser that reports usage errors as JSON in
    --json mode ({"error": ...} on stdout, same exit code 2).
    The flag lives on the class so subparsers share it."""

    json_mode = False

    def error(self, message):
        if self.json_mode:
            print(json.dumps({"error": message}))
            self.exit(2)
        super().error(message)


# ---------------------------------------------------------------------------
# Combined scan
# ---------------------------------------------------------------------------

_CONFIG_NAMES = {"settings.json", "settings.local.json",
                 ".mcp.json", "mcp.json"}
_CONFIG_DIRS = {".claude", ".vscode", ".cursor"}


def _discover_config_files(d):
    """Files secret-guard is pointed at during a combined
    scan: the config shapes its README promises to check —
    settings.json / settings.local.json / .mcp.json /
    mcp.json anywhere under DIR, plus any JSON file inside
    a .claude, .vscode or .cursor folder. Sorted and
    deterministic; .git is never entered."""
    found = []
    for p in sorted(d.rglob("*.json")):
        rel = p.relative_to(d)
        if ".git" in rel.parts:
            continue
        if p.name in _CONFIG_NAMES or any(
                part in _CONFIG_DIRS for part in rel.parts[:-1]):
            found.append(p)
    return found


def _discover_settings(d):
    """The one settings file hook-doctor audits during a
    combined scan, in preference order."""
    for cand in (d / ".claude" / "settings.json",
                 d / "settings.json",
                 d / ".claude" / "settings.local.json"):
        if cand.is_file():
            return cand
    return None


def _discover_subfolder(d, *names):
    """First existing DIR/<name> folder, else DIR itself —
    fleet-audit and skill-sprawl walk recursively and report
    honestly on whatever shape they find."""
    for name in names:
        cand = d / name
        if cand.is_dir():
            return cand
    return d


def _run_capture(module_main, args):
    """Run a module's own CLI in-process and capture what it
    prints. Returns (exit_code, stdout_text). The module's
    main() may raise SystemExit (argparse errors, --help);
    its code is honoured either way."""
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            code = module_main(args)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    return code, buf.getvalue()


def _run_json_section(module_main, args):
    """Run a module in --json mode; its section is the exact
    object the module itself prints, plus its exit code."""
    code, text = _run_capture(module_main, args)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {"error": "module produced no JSON output",
                   "raw": text.strip()[:500]}
    payload = dict(payload)
    payload["exit_code"] = code
    return payload


def _skipped_section(module, reason):
    return {"tool": module.TOOL, "version": module.__version__,
            "status": "skipped", "reason": reason,
            "clean": None, "exit_code": None}


def scan_setup(d, home, want_text=False):
    """Run all five modules over DIR. Returns the combined
    payload dict (JSON mode) — text mode renders from the
    same structure plus each module's own text output,
    captured only when want_text is set (modules are
    read-only, so a second run would be identical, just
    slower)."""
    sections_json = {}
    sections_text = {}

    def _text(name, module_main, args):
        if not want_text:
            return (None, None)
        return _run_capture(module_main, args)

    # clone-guard: the folder itself.
    sections_json["clone-guard"] = _run_json_section(
        clone_guard.main, [str(d), "--json"])
    sections_text["clone-guard"] = _text(
        "clone-guard", clone_guard.main, [str(d)])

    # secret-guard: discovered config files only, scan only
    # (never fix — the combined scan is read-only).
    configs = _discover_config_files(d)
    if configs:
        sections_json["secret-guard"] = _run_json_section(
            secret_guard.main,
            ["scan"] + [str(p) for p in configs] + ["--json"])
        sections_text["secret-guard"] = _text(
            "secret-guard", secret_guard.main,
            ["scan"] + [str(p) for p in configs])
    else:
        sections_json["secret-guard"] = _skipped_section(
            secret_guard,
            "no candidate config files found under the "
            "scanned folder (looked for settings.json, "
            "settings.local.json, .mcp.json, mcp.json, and "
            "JSON inside .claude/.vscode/.cursor folders)")
        sections_text["secret-guard"] = (None, None)

    # hook-doctor: the one discovered settings file, with
    # the setup folder standing in for home (the source
    # tool's own fake-home pattern) unless --home overrides.
    settings = _discover_settings(d)
    if settings is not None:
        sections_json["hook-doctor"] = _run_json_section(
            hook_doctor.main,
            ["audit", str(settings), "--home", str(home),
             "--json"])
        sections_text["hook-doctor"] = _text(
            "hook-doctor", hook_doctor.main,
            ["audit", str(settings), "--home", str(home)])
    else:
        sections_json["hook-doctor"] = _skipped_section(
            hook_doctor,
            "no settings file found (looked for "
            ".claude/settings.json, settings.json, "
            ".claude/settings.local.json)")
        sections_text["hook-doctor"] = (None, None)

    # fleet-audit / skill-sprawl: their natural subfolder
    # when present, else the folder itself.
    agents = _discover_subfolder(d, ".claude/agents", "agents")
    sections_json["fleet-audit"] = _run_json_section(
        fleet_audit.main, ["audit", str(agents), "--json"])
    sections_text["fleet-audit"] = _text(
        "fleet-audit", fleet_audit.main, ["audit", str(agents)])

    skills = _discover_subfolder(d, ".claude/skills", "skills")
    sections_json["skill-sprawl"] = _run_json_section(
        skill_sprawl.main, ["audit", str(skills), "--json"])
    sections_text["skill-sprawl"] = _text(
        "skill-sprawl", skill_sprawl.main,
        ["audit", str(skills)])

    run = [name for name, s in sections_json.items()
           if s.get("status") != "skipped"]
    skipped = [name for name, s in sections_json.items()
               if s.get("status") == "skipped"]
    clean_modules = [n for n in run
                     if sections_json[n].get("clean") is True]
    finding_modules = [n for n in run
                       if sections_json[n].get("clean") is False]
    payload = {
        "tool": TOOL, "version": __version__,
        "command": "scan", "dir": str(d), "home": str(home),
        "modules": sections_json,
        "summary": {
            "modules_run": len(run),
            "modules_skipped": len(skipped),
            "clean_modules": len(clean_modules),
            "finding_modules": len(finding_modules),
            "skipped": skipped,
        },
        "clean": not finding_modules,
    }
    return payload, sections_text


def cmd_scan(a):
    d = Path(a.dir)
    if not d.exists():
        raise ToolError(f"no such folder: {a.dir}")
    if not d.is_dir():
        raise ToolError(f"not a folder: {a.dir}")
    home = Path(a.home) if a.home else d
    payload, sections_text = scan_setup(
        d, home, want_text=not a.json)
    if a.json:
        print(json.dumps(payload, indent=2))
        return 0 if payload["clean"] else 1
    s = payload["summary"]
    print(f"{TOOL} v{__version__} — scan · folder {d.name}")
    print(f"{s['modules_run']} module(s) run · "
          f"{s['clean_modules']} clean · "
          f"{s['finding_modules']} with findings · "
          f"{s['modules_skipped']} skipped")
    for name in MODULES:
        section = payload["modules"][name]
        print()
        print(f"===== {name} — {_MODULE_BLURBS[name]} =====")
        if section.get("status") == "skipped":
            print(f"SKIPPED — {section['reason']}")
            continue
        _code, text = sections_text[name]
        print(text.rstrip())
    print()
    if payload["clean"]:
        print("VERDICT: CLEAN — every module that ran "
              "reported clean. CLEAN covers only what the "
              "five modules check; it is not a guarantee the "
              "setup is safe. Each module's limits are in "
              "its own section above.")
    else:
        names = [n for n, sec in payload["modules"].items()
                 if sec.get("clean") is False]
        print("VERDICT: FINDINGS in "
              + ", ".join(names)
              + " — read each module's section above before "
                "this setup runs.")
    return 0 if payload["clean"] else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _scan_parser():
    ap = _JsonAwareParser(
        prog=f"{TOOL} scan",
        description="Run all five modules over one "
                    "agent-setup folder and print a single "
                    "combined receipt. Read-only: the "
                    "combined scan never calls secret-guard "
                    "fix, never executes or installs "
                    "anything.")
    ap.add_argument("dir", help="the agent-setup folder to "
                                "scan (e.g. a project root "
                                "holding .claude/)")
    ap.add_argument("--home", default=None,
                    help="the home folder hook-doctor "
                         "resolves ~ and $HOME paths against "
                         "(default: the scanned folder "
                         "itself, so a self-contained setup "
                         "is audited as its own home)")
    ap.add_argument("--json", action="store_true",
                    help="print one JSON object (with a "
                         "per-module section) instead of "
                         "text")
    return ap


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(HELP)
        return 0
    if argv[0] in ("-v", "--version"):
        print(f"{TOOL} v{__version__}")
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd in MODULES:
        return MODULES[cmd].main(rest)
    if cmd == "scan":
        _JsonAwareParser.json_mode = "--json" in rest
        ap = _scan_parser()
        a = ap.parse_args(rest)

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
    if "--json" in argv:
        print(json.dumps(
            {"error": f"unknown command: {cmd} "
                      f"(expected one of: "
                      f"{', '.join(MODULES)}, scan)"}))
    else:
        print(f"ERROR: unknown command: {cmd}")
        print()
        print(HELP)
    return 2


if __name__ == "__main__":
    sys.exit(main())
