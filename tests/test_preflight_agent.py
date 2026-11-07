#!/usr/bin/env python3
"""Tests for the preflight-agent dispatcher and the
combined scan. Stdlib unittest only, temp dirs only.

Run from the pack root:
    python3 -m unittest discover -s tests -v

The five module engines carry their own ported suites
(test_clone_guard.py, test_secret_guard.py,
test_hook_doctor.py, test_fleet_audit.py,
test_skill_sprawl.py — the source tools' suites, unchanged
except each suite's EXAMPLES path). This file tests only
what is new: routing, the JSON/error contract at the
suite level, the combined scan's discovery and verdict
math, read-only behaviour, and determinism.

The planted fixture is built by examples/make_fixture.py
(one planted problem per module); every expectation below
is stated from that fixture's construction, not copied
from the tool's output.
"""

import hashlib
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parent.parent
SRC = PACK_ROOT / "src"
EXAMPLES = PACK_ROOT / "examples"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(EXAMPLES))

import preflight_agent as pa  # noqa: E402
import make_fixture  # noqa: E402


def run_cli(argv):
    """Run the dispatcher in-process; return
    (exit_code, stdout_text)."""
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            code = pa.main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    return code, buf.getvalue()


def tree_hashes(root):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(
                p.read_bytes()).hexdigest()
    return out


class FixtureCase(unittest.TestCase):
    """Base: a fresh planted fixture per test."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "setup"
        make_fixture.build(self.root)

    def tearDown(self):
        self._tmp.cleanup()


class DelegationTests(FixtureCase):

    def test_clone_guard_routes_and_exits_1(self):
        code, out = run_cli(["clone-guard", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("clone-guard v", out)
        self.assertIn("AUTO-RUN", out)

    def test_secret_guard_scan_routes(self):
        code, out = run_cli(
            ["secret-guard", "scan",
             str(self.root / ".mcp.json")])
        self.assertEqual(code, 1)
        self.assertIn("ENV-LITERAL", out)

    def test_hook_doctor_routes(self):
        code, out = run_cli(
            ["hook-doctor", "audit",
             str(self.root / ".claude" / "settings.json"),
             "--home", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("COMMAND_PATH", out)

    def test_fleet_audit_routes(self):
        code, _out = run_cli(
            ["fleet-audit", "audit",
             str(self.root / ".claude" / "agents")])
        self.assertEqual(code, 1)

    def test_skill_sprawl_routes(self):
        code, _out = run_cli(
            ["skill-sprawl", "audit",
             str(self.root / ".claude" / "skills")])
        self.assertEqual(code, 1)

    def test_module_json_is_one_object_with_tool_name(self):
        code, out = run_cli(
            ["clone-guard", str(self.root), "--json"])
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["tool"], "clone-guard")
        self.assertFalse(payload["clean"])

    def test_secret_guard_fix_dry_run_writes_nothing(self):
        target = self.root / ".mcp.json"
        before = target.read_bytes()
        code, out = run_cli(
            ["secret-guard", "fix", str(target)])
        self.assertEqual(code, 1)
        self.assertIn("DRY RUN", out)
        self.assertEqual(target.read_bytes(), before)


class ErrorContractTests(unittest.TestCase):

    def test_unknown_command_text_mode(self):
        code, out = run_cli(["nonsense"])
        self.assertEqual(code, 2)
        self.assertIn("unknown command", out)

    def test_unknown_command_json_mode(self):
        code, out = run_cli(["nonsense", "--json"])
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(out))

    def test_scan_missing_folder_json_error(self):
        code, out = run_cli(
            ["scan", "/nonexistent-pa-dir", "--json"])
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(out))

    def test_scan_missing_folder_text_error(self):
        code, out = run_cli(["scan", "/nonexistent-pa-dir"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR:", out)

    def test_scan_no_args_json_error_not_traceback(self):
        code, out = run_cli(["scan", "--json"])
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(out))

    def test_scan_on_a_file_is_not_a_folder_error(self):
        code, out = run_cli(
            ["scan", __file__, "--json"])
        self.assertEqual(code, 2)
        self.assertIn("not a folder",
                      json.loads(out)["error"])

    def test_module_help_delegates_and_exits_0(self):
        code, out = run_cli(["clone-guard", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("usage: clone-guard", out)

    def test_module_argparse_error_stays_json(self):
        code, out = run_cli(["secret-guard", "--json"])
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(out))

    def test_version_flag(self):
        code, out = run_cli(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("preflight-agent v", out)

    def test_no_args_prints_help_exit_0(self):
        code, out = run_cli([])
        self.assertEqual(code, 0)
        for name in ("clone-guard", "secret-guard",
                     "hook-doctor", "fleet-audit",
                     "skill-sprawl", "scan"):
            self.assertIn(name, out)


class CombinedScanTests(FixtureCase):

    def test_scan_json_shape_and_plants(self):
        code, out = run_cli(
            ["scan", str(self.root), "--json"])
        self.assertEqual(code, 1)
        d = json.loads(out)
        self.assertEqual(d["tool"], "preflight-agent")
        self.assertEqual(d["command"], "scan")
        self.assertFalse(d["clean"])
        self.assertEqual(d["summary"]["modules_run"], 5)
        self.assertEqual(d["summary"]["modules_skipped"], 0)
        self.assertEqual(d["summary"]["finding_modules"], 5)
        m = d["modules"]
        self.assertGreaterEqual(
            m["clone-guard"]["summary"]["auto_run"], 1)
        sg_files = m["secret-guard"]["files"]
        self.assertTrue(any(
            f["verdict"] == "ENV-LITERAL"
            for r in sg_files for f in r["findings"]))
        self.assertFalse(m["hook-doctor"]["clean"])
        fa_rules = {f["rule"]
                    for f in m["fleet-audit"]["findings"]}
        self.assertIn("DUPLICATE_NAME", fa_rules)
        ss_rules = {f["rule"]
                    for f in m["skill-sprawl"]["findings"]}
        self.assertIn("DUPLICATE_NAME", ss_rules)
        for name, section in m.items():
            self.assertEqual(section["exit_code"], 1, name)

    def test_scan_text_has_all_sections_and_verdict(self):
        code, out = run_cli(["scan", str(self.root)])
        self.assertEqual(code, 1)
        for name in ("clone-guard", "secret-guard",
                     "hook-doctor", "fleet-audit",
                     "skill-sprawl"):
            self.assertIn(f"===== {name}", out)
        self.assertIn("VERDICT: FINDINGS in", out)

    def test_scan_is_read_only_on_the_fixture(self):
        before = tree_hashes(self.root)
        run_cli(["scan", str(self.root), "--json"])
        run_cli(["scan", str(self.root)])
        self.assertEqual(tree_hashes(self.root), before)

    def test_scan_json_byte_identical_across_runs(self):
        _c1, out1 = run_cli(["scan", str(self.root), "--json"])
        _c2, out2 = run_cli(["scan", str(self.root), "--json"])
        self.assertEqual(out1, out2)


class CleanAndSkipTests(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "setup"
        agents = self.root / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "helper.md").write_text(
            "---\n"
            "name: helper\n"
            "description: Helps with exactly one small "
            "thing, described uniquely so nothing else "
            "collides with it at all.\n"
            "tools: Read\n"
            "---\n\nYou help with the one small thing.\n")
        (self.root / ".claude" / "skills").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_clean_setup_scans_clean_with_two_skips(self):
        code, out = run_cli(
            ["scan", str(self.root), "--json"])
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertTrue(d["clean"])
        self.assertEqual(
            sorted(d["summary"]["skipped"]),
            ["hook-doctor", "secret-guard"])
        self.assertEqual(
            d["modules"]["hook-doctor"]["status"], "skipped")
        self.assertIsNone(
            d["modules"]["secret-guard"]["clean"])

    def test_clean_setup_text_verdict(self):
        code, out = run_cli(["scan", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("SKIPPED", out)


class ImportDisciplineTests(unittest.TestCase):

    def test_dispatcher_imports_stdlib_plus_modules(self):
        import ast
        tree = ast.parse(
            (SRC / "preflight_agent.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(
                    a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) \
                    and node.module:
                imported.add(node.module.split(".")[0])
        allowed = set(sys.stdlib_module_names) | {
            "clone_guard", "secret_guard", "hook_doctor",
            "fleet_audit", "skill_sprawl"}
        self.assertLessEqual(imported, allowed)


if __name__ == "__main__":
    unittest.main()
