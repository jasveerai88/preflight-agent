#!/usr/bin/env python3
"""Tests for hook-doctor. Stdlib unittest only.

Run from the pack root:
    python3 -m unittest discover -s tests -v

The bundled fixtures are CONSTRUCTED settings files (fictional
business, a fake home folder) whose expected findings were
worked out by hand from the rule table in SKILL.md — never
copied from the tool's own output:

broken-settings.json, audited with --home examples/fake-home
(fake-home/.claude/hooks holds block-secrets.py,
deny-danger.py, guard-format.sh, format.sh, session-note.sh;
guard-rm.sh and scripts/notify.sh are DELIBERATELY absent):
    ERROR  MATCHER_CASE          PreToolUse "bash" (Bash)
    ERROR  MATCHER_CASE          PreToolUse "Write|edit" (Edit)
    ERROR  COMMAND_PATH          PreToolUse guard-rm.sh missing
    ERROR  COMMAND_PATH          Notification ./scripts/notify.sh
    ERROR  COMMAND_PATH          SessionStart /opt/hooks/start.sh
    WARN   EXIT_CODE_TRAP        PreToolUse guard-rm (guard word,
                                 no exit 2, no JSON decision)
    WARN   EXIT_CODE_TRAP        PreToolUse block-secrets.py
                                 (guard word, script exits 1)
    WARN   EVENT_NOT_BLOCKING    PostToolUse guard-format.sh x2
    WARN   DUPLICATE_HOOK        PostToolUse guard-format.sh (2nd)
    WARN   MATCHER_NEVER_MATCHES Stop matcher "Bash" (Stop takes
                                 no matcher)
    INFO   TIMEOUT_MISSING       PreToolUse block-secrets.py
    => 12 findings · 5 errors · 6 warnings · 1 info · exit 1

clean-settings.json (same fake home): a guard that exits 2,
existing scripts, exact matchers, timeouts everywhere
    => 0 findings · exit 0

invalid-settings.json: a trailing comma
    => 1 INVALID_JSON ERROR with a line/column · exit 1

Both directions are covered per rule: the fault present earns
the finding with the right rule and severity, the fault absent
earns none. And the auditor is proven read-only: the settings
file's bytes are unchanged after a run, and a hook command
that WOULD create a marker file if executed creates nothing.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]
SRC = PACK_ROOT / "src" / "hook_doctor.py"
EXAMPLES = PACK_ROOT / "examples" / "hook-doctor"
FAKE_HOME = EXAMPLES / "fake-home"
sys.path.insert(0, str(PACK_ROOT / "src"))

import hook_doctor as hd  # noqa: E402

BROKEN = EXAMPLES / "broken-settings.json"
CLEAN = EXAMPLES / "clean-settings.json"
INVALID = EXAMPLES / "invalid-settings.json"


def run_cli(*args):
    proc = subprocess.run(
        [sys.executable, str(SRC), *args],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def audit_dict(settings, home=FAKE_HOME):
    return hd.audit_settings(Path(settings), Path(home))


def rules(audit, rule=None, severity=None):
    out = audit["findings"]
    if rule is not None:
        out = [f for f in out if f["rule"] == rule]
    if severity is not None:
        out = [f for f in out if f["severity"] == severity]
    return out


def write_settings(tmp, data, name="settings.json"):
    path = Path(tmp) / name
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data), encoding="utf-8")
    return path


def one_hook(event, command, matcher=None, timeout=5,
             htype="command"):
    handler = {"type": htype}
    if command is not None:
        handler["command"] = command
    if timeout is not None:
        handler["timeout"] = timeout
    group = {"hooks": [handler]}
    if matcher is not None:
        group["matcher"] = matcher
    return {"hooks": {event: [group]}}


class TestFixtures(unittest.TestCase):
    def test_broken_fixture_matches_hand_counted_table(self):
        audit = audit_dict(BROKEN)
        self.assertEqual(audit["hooks"], 8)
        self.assertEqual(len(audit["events"]), 5)
        expected = [
            ("COMMAND_PATH", "ERROR", 3),
            ("MATCHER_CASE", "ERROR", 2),
            ("EVENT_NOT_BLOCKING", "WARN", 2),
            ("EXIT_CODE_TRAP", "WARN", 2),
            ("DUPLICATE_HOOK", "WARN", 1),
            ("MATCHER_NEVER_MATCHES", "WARN", 1),
            ("TIMEOUT_MISSING", "INFO", 1),
        ]
        for rule, severity, count in expected:
            self.assertEqual(
                len(rules(audit, rule, severity)), count,
                f"{rule}/{severity}")
        self.assertEqual(audit["summary"], {
            "findings": 12, "errors": 5, "warnings": 6,
            "info": 1})
        self.assertFalse(audit["clean"])

    def test_broken_fixture_cli_exit_1(self):
        code, out, _ = run_cli("audit", str(BROKEN), "--home",
                               str(FAKE_HOME))
        self.assertEqual(code, 1)
        self.assertIn("Summary: 12 findings · 5 errors · "
                      "6 warnings · 1 info", out)

    def test_clean_fixture_zero_findings_exit_0(self):
        audit = audit_dict(CLEAN)
        self.assertEqual(audit["findings"], [])
        code, out, _ = run_cli("audit", str(CLEAN), "--home",
                               str(FAKE_HOME))
        self.assertEqual(code, 0)
        self.assertIn("Verdict: CLEAN", out)

    def test_invalid_fixture_one_error_with_position(self):
        audit = audit_dict(INVALID)
        found = rules(audit, "INVALID_JSON", "ERROR")
        self.assertEqual(len(found), 1)
        self.assertIn("line ", found[0]["message"])
        self.assertIn("column ", found[0]["message"])
        self.assertTrue(audit["parse_error"])
        code, _, _ = run_cli("audit", str(INVALID), "--home",
                             str(FAKE_HOME))
        self.assertEqual(code, 1)


class TestInvalidJson(unittest.TestCase):
    def test_parse_error_position_names_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_settings(
                tmp, '{\n  "hooks": {\n    bad\n  }\n}\n')
            audit = audit_dict(path, home=tmp)
            found = rules(audit, "INVALID_JSON")
            self.assertEqual(len(found), 1)
            self.assertIn("line 3", found[0]["message"])

    def test_top_level_not_an_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_settings(tmp, '["hooks"]')
            audit = audit_dict(path, home=tmp)
            found = rules(audit, "INVALID_JSON", "ERROR")
            self.assertEqual(len(found), 1)
            self.assertIn("not an object", found[0]["message"])

    def test_missing_file_is_exit_2_not_a_finding(self):
        code, out, _ = run_cli("audit", "/no/such/settings.json")
        self.assertEqual(code, 2)
        self.assertIn("ERROR:", out)
        code, out, _ = run_cli("audit", "/no/such/settings.json",
                               "--json")
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(out))


class TestMatcherCase(unittest.TestCase):
    def audit_matcher(self, tmp, matcher, event="PreToolUse"):
        path = write_settings(
            tmp, one_hook(event, "echo ok", matcher=matcher))
        return audit_dict(path, home=tmp)

    def test_lowercase_tool_flagged_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = self.audit_matcher(tmp, "bash")
            found = rules(audit, "MATCHER_CASE", "ERROR")
            self.assertEqual(len(found), 1)
            self.assertIn('"bash"', found[0]["message"])
            self.assertIn('"Bash"', found[0]["fix"])

    def test_exact_case_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = self.audit_matcher(tmp, "Bash")
            self.assertEqual(rules(audit, "MATCHER_CASE"), [])

    def test_uppercase_variant_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = self.audit_matcher(tmp, "EDIT")
            self.assertEqual(
                len(rules(audit, "MATCHER_CASE")), 1)

    def test_only_the_wrong_alternative_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = self.audit_matcher(tmp, "Write|edit")
            found = rules(audit, "MATCHER_CASE")
            self.assertEqual(len(found), 1)
            self.assertIn('"edit"', found[0]["message"])

    def test_unknown_and_mcp_names_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = self.audit_matcher(
                tmp, "mcp__github__create_issue|SomeFutureTool")
            self.assertEqual(rules(audit, "MATCHER_CASE"), [])
            self.assertEqual(
                rules(audit, "MATCHER_NEVER_MATCHES"), [])


class TestMatcherNeverMatches(unittest.TestCase):
    def test_matcher_on_event_that_takes_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_settings(
                tmp, one_hook("Stop", "echo ok", matcher="Bash"))
            audit = audit_dict(path, home=tmp)
            found = rules(audit, "MATCHER_NEVER_MATCHES",
                          "WARN")
            self.assertEqual(len(found), 1)
            self.assertIn("do not take a matcher",
                          found[0]["message"])

    def test_no_matcher_event_without_matcher_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_settings(
                tmp, one_hook("Stop", "echo ok"))
            audit = audit_dict(path, home=tmp)
            self.assertEqual(
                rules(audit, "MATCHER_NEVER_MATCHES"), [])

    def test_tool_name_on_session_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_settings(
                tmp, one_hook("SessionStart", "echo ok",
                              matcher="Bash"))
            audit = audit_dict(path, home=tmp)
            found = rules(audit, "MATCHER_NEVER_MATCHES")
            self.assertEqual(len(found), 1)
            self.assertIn("not tool", found[0]["message"])

    def test_session_start_source_value_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_settings(
                tmp, one_hook("SessionStart", "echo ok",
                              matcher="startup"))
            audit = audit_dict(path, home=tmp)
            self.assertEqual(
                rules(audit, "MATCHER_NEVER_MATCHES"), [])

    def test_event_name_used_as_tool_matcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_settings(
                tmp, one_hook("PreToolUse", "echo ok",
                              matcher="Stop"))
            audit = audit_dict(path, home=tmp)
            found = rules(audit, "MATCHER_NEVER_MATCHES")
            self.assertEqual(len(found), 1)
            self.assertIn("names an event", found[0]["message"])


class TestGuardTraps(unittest.TestCase):
    def make_script(self, tmp, name, body):
        script = Path(tmp) / name
        script.write_text(body, encoding="utf-8")
        return script

    def test_guard_script_that_exits_1_is_trapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_script(tmp, "guard.py",
                             "import sys\nsys.exit(1)\n")
            path = write_settings(tmp, one_hook(
                "PreToolUse", f'python3 "{tmp}/guard.py"',
                matcher="Bash"))
            audit = audit_dict(path, home=tmp)
            found = rules(audit, "EXIT_CODE_TRAP", "WARN")
            self.assertEqual(len(found), 1)
            self.assertIn("Heuristic", found[0]["message"])
            self.assertEqual(
                rules(audit, "EVENT_NOT_BLOCKING"), [])

    def test_guard_script_that_exits_2_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_script(tmp, "guard.py",
                             "import sys\nsys.exit(2)\n")
            path = write_settings(tmp, one_hook(
                "PreToolUse", f'python3 "{tmp}/guard.py"',
                matcher="Bash"))
            audit = audit_dict(path, home=tmp)
            self.assertEqual(rules(audit, "EXIT_CODE_TRAP"),
                             [])

    def test_guard_with_json_decision_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_script(
                tmp, "deny.py",
                'print({"permissionDecision": "deny"})\n')
            path = write_settings(tmp, one_hook(
                "PreToolUse", f'python3 "{tmp}/deny.py"',
                matcher="Bash"))
            audit = audit_dict(path, home=tmp)
            self.assertEqual(rules(audit, "EXIT_CODE_TRAP"),
                             [])

    def test_permission_request_needs_json_not_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_script(tmp, "guard.py",
                             "import sys\nsys.exit(2)\n")
            path = write_settings(tmp, one_hook(
                "PermissionRequest",
                f'python3 "{tmp}/guard.py"', matcher="Bash"))
            audit = audit_dict(path, home=tmp)
            self.assertEqual(
                len(rules(audit, "EXIT_CODE_TRAP")), 1)

    def test_non_guard_hook_is_not_trapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_settings(tmp, one_hook(
                "PreToolUse", "echo checking", matcher="Bash"))
            audit = audit_dict(path, home=tmp)
            self.assertEqual(rules(audit, "EXIT_CODE_TRAP"),
                             [])
            self.assertEqual(
                rules(audit, "EVENT_NOT_BLOCKING"), [])

    def test_guard_on_post_tool_use_cannot_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_script(tmp, "guard.py",
                             "import sys\nsys.exit(2)\n")
            path = write_settings(tmp, one_hook(
                "PostToolUse", f'python3 "{tmp}/guard.py"',
                matcher="Bash"))
            audit = audit_dict(path, home=tmp)
            found = rules(audit, "EVENT_NOT_BLOCKING", "WARN")
            self.assertEqual(len(found), 1)
            self.assertEqual(rules(audit, "EXIT_CODE_TRAP"),
                             [])

    def test_guard_on_pre_tool_use_is_not_event_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_script(tmp, "guard.py",
                             "import sys\nsys.exit(1)\n")
            path = write_settings(tmp, one_hook(
                "PreToolUse", f'python3 "{tmp}/guard.py"',
                matcher="Bash"))
            audit = audit_dict(path, home=tmp)
            self.assertEqual(
                rules(audit, "EVENT_NOT_BLOCKING"), [])


class TestCommandPath(unittest.TestCase):
    def test_missing_absolute_path_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_settings(tmp, one_hook(
                "PreToolUse", "/definitely/not/here.sh",
                matcher="Bash"))
            audit = audit_dict(path, home=tmp)
            found = rules(audit, "COMMAND_PATH", "ERROR")
            self.assertEqual(len(found), 1)
            self.assertIn("/definitely/not/here.sh",
                          found[0]["message"])

    def test_missing_relative_path_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_settings(tmp, one_hook(
                "Notification", "./scripts/nope.sh"))
            audit = audit_dict(path, home=tmp)
            self.assertEqual(
                len(rules(audit, "COMMAND_PATH", "ERROR")), 1)

    def test_home_relative_path_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            script = home / ".claude" / "hooks" / "ok.sh"
            script.parent.mkdir(parents=True)
            script.write_text("#!/bin/sh\nexit 0\n")
            script.chmod(0o755)
            path = write_settings(tmp, one_hook(
                "SessionStart", '"$HOME/.claude/hooks/ok.sh"'))
            audit = audit_dict(path, home=home)
            self.assertEqual(rules(audit, "COMMAND_PATH"), [])

    def test_existing_script_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "run.sh"
            script.write_text("#!/bin/sh\nexit 0\n")
            script.chmod(0o755)
            path = write_settings(tmp, one_hook(
                "SessionStart", f'"{script}"'))
            audit = audit_dict(path, home=tmp)
            self.assertEqual(rules(audit, "COMMAND_PATH"), [])

    def test_non_executable_direct_script_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "run.sh"
            script.write_text("#!/bin/sh\nexit 0\n")
            script.chmod(0o644)
            path = write_settings(tmp, one_hook(
                "SessionStart", f'"{script}"'))
            audit = audit_dict(path, home=tmp)
            found = rules(audit, "COMMAND_PATH", "WARN")
            self.assertEqual(len(found), 1)
            self.assertIn("not executable",
                          found[0]["message"])

    def test_non_executable_via_interpreter_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "job.py"
            script.write_text("print('hi')\n")
            script.chmod(0o644)
            path = write_settings(tmp, one_hook(
                "SessionStart", f'python3 "{script}"'))
            audit = audit_dict(path, home=tmp)
            self.assertEqual(rules(audit, "COMMAND_PATH"), [])

    def test_project_dir_paths_are_skipped_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_settings(tmp, one_hook(
                "PreToolUse",
                '"$CLAUDE_PROJECT_DIR"/hooks/whatever.sh',
                matcher="Bash"))
            audit = audit_dict(path, home=tmp)
            self.assertEqual(rules(audit, "COMMAND_PATH"), [])


class TestTimeoutMissing(unittest.TestCase):
    def test_missing_timeout_is_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_settings(tmp, one_hook(
                "PreToolUse", "echo ok", matcher="Bash",
                timeout=None))
            audit = audit_dict(path, home=tmp)
            found = rules(audit, "TIMEOUT_MISSING", "INFO")
            self.assertEqual(len(found), 1)
            # INFO alone never fails the audit
            self.assertTrue(audit["clean"])

    def test_present_timeout_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_settings(tmp, one_hook(
                "PreToolUse", "echo ok", matcher="Bash",
                timeout=30))
            audit = audit_dict(path, home=tmp)
            self.assertEqual(rules(audit, "TIMEOUT_MISSING"),
                             [])


class TestHooksShape(unittest.TestCase):
    def audit_raw(self, tmp, data):
        path = write_settings(tmp, data)
        return audit_dict(path, home=tmp)

    def test_hooks_not_an_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = self.audit_raw(tmp, {"hooks": ["x"]})
            found = rules(audit, "HOOKS_SHAPE", "ERROR")
            self.assertEqual(len(found), 1)

    def test_group_without_hooks_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = self.audit_raw(
                tmp, {"hooks": {"PreToolUse": [{"matcher":
                                                 "Bash"}]}})
            self.assertEqual(
                len(rules(audit, "HOOKS_SHAPE", "ERROR")), 1)

    def test_command_handler_without_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = self.audit_raw(tmp, {"hooks": {
                "PreToolUse": [{"matcher": "Bash", "hooks": [
                    {"type": "command", "timeout": 5}]}]}})
            self.assertEqual(
                len(rules(audit, "HOOKS_SHAPE", "ERROR")), 1)

    def test_unknown_event_is_a_warning_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = self.audit_raw(tmp, {"hooks": {
                "PreTooluse": [{"hooks": [
                    {"type": "command", "command": "echo ok",
                     "timeout": 5}]}]}})
            found = rules(audit, "HOOKS_SHAPE", "WARN")
            self.assertEqual(len(found), 1)
            self.assertIn("typo", found[0]["message"])
            self.assertTrue(audit["clean"])

    def test_no_hooks_key_is_clean_and_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = self.audit_raw(tmp, {"permissions": {}})
            self.assertEqual(audit["findings"], [])
            self.assertEqual(audit["hooks"], 0)


class TestDuplicateHook(unittest.TestCase):
    def test_identical_handler_twice_warns_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            group = {"matcher": "Bash", "hooks": [
                {"type": "command", "command": "echo ok",
                 "timeout": 5}]}
            path = write_settings(
                tmp, {"hooks": {"PostToolUse": [group, group]}})
            audit = audit_dict(path, home=tmp)
            self.assertEqual(
                len(rules(audit, "DUPLICATE_HOOK", "WARN")), 1)

    def test_different_handlers_do_not_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = {"hooks": {"PostToolUse": [
                {"matcher": "Bash", "hooks": [
                    {"type": "command", "command": "echo a",
                     "timeout": 5}]},
                {"matcher": "Bash", "hooks": [
                    {"type": "command", "command": "echo b",
                     "timeout": 5}]},
            ]}}
            path = write_settings(tmp, data)
            audit = audit_dict(path, home=tmp)
            self.assertEqual(rules(audit, "DUPLICATE_HOOK"),
                             [])


class TestReadOnly(unittest.TestCase):
    def test_settings_bytes_unchanged_and_nothing_executed(
            self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "PWNED"
            data = {"hooks": {"PreToolUse": [
                {"matcher": "Bash", "hooks": [
                    {"type": "command",
                     "command": f"touch {marker}",
                     "timeout": 5}]}]}}
            path = write_settings(tmp, data)
            before = path.read_bytes()
            code, _, _ = run_cli("audit", str(path), "--home",
                                 tmp)
            # exit 1: the not-yet-existing marker path is
            # itself a COMMAND_PATH finding — expected. The
            # proof is that the file was never created.
            self.assertEqual(code, 1)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(
                marker.exists(),
                "the auditor executed a hook command")


class TestJsonOutput(unittest.TestCase):
    def test_summary_recounts_against_findings(self):
        code, out, _ = run_cli("audit", str(BROKEN), "--home",
                               str(FAKE_HOME), "--json")
        self.assertEqual(code, 1)
        data = json.loads(out)
        findings = data["findings"]
        self.assertEqual(data["summary"]["findings"],
                         len(findings))
        for key, severity in (("errors", "ERROR"),
                              ("warnings", "WARN"),
                              ("info", "INFO")):
            self.assertEqual(
                data["summary"][key],
                sum(1 for f in findings
                    if f["severity"] == severity))
        self.assertFalse(data["clean"])

    def test_argparse_error_is_json_in_json_mode(self):
        code, out, _ = run_cli("--json")
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(out))

    def test_clean_fixture_json(self):
        code, out, _ = run_cli("audit", str(CLEAN), "--home",
                               str(FAKE_HOME), "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["findings"], [])
        self.assertTrue(data["clean"])


if __name__ == "__main__":
    unittest.main()
