#!/usr/bin/env python3
"""Tests for fleet-audit. Stdlib unittest only.

Run from the pack root:
    python3 -m unittest discover -s tests -v

The bundled fixtures are CONSTRUCTED agent fleets (fictional
agents) whose expected findings were worked out by hand from
the rule table in SKILL.md and the similarity metric —
token sets enumerated by hand below — never copied from the
tool's own output:

faulty-fleet (10 files, recursive: one agent lives in
extra/):
    code-reviewer    desc tokens (26): reviews code changes
      for bugs regressions and style problems before merge
      use when a pull request or diff needs careful read
      only review of implementation quality
    pr-reviewer      the same 26 tokens + {tests} = 27
      -> pair similarity 26/27 = 0.963 >= 0.90
         DESCRIPTION_IDENTICAL (ERROR)
    docs-reviewer    desc tokens (25): reviews documentation
      changes for errors regressions and style problems
      before merge use when a guide or page needs careful
      read only review of writing quality
      shared with code-reviewer: 20 -> 20/31 = 0.645
      shared with pr-reviewer:   20 -> 20/32 = 0.625
         DESCRIPTION_COLLISION x2 (WARN)
    security-reviewer read intent (audits / checks / audit),
      tools Read, Grep, Edit
         ROLE_TOOL_MISMATCH (WARN) — Edit left over
    deploy-helper x2 files (extra/ is the second), same
      name, descriptions / tools / model all differ
         DUPLICATE_NAME (ERROR, drift noted)
      their descriptions share 8 of 31 tokens = 0.258:
      no collision finding between them
    silent-helper    no description
         DESCRIPTION_MISSING (ERROR)
    general-researcher no tools field
         TOOLS_UNSPECIFIED (INFO)
    quick-fixer      description "Fixes typos." = 12 chars
         DESCRIPTION_SHORT (WARN)
    onboarding-notes.md no frontmatter at all
         UNRECOGNIZED_FILE (ERROR)
    every other pair shares < 0.50 of its tokens (the
    largest is security-reviewer vs code-reviewer, 9/41 =
    0.220), so no further pair findings exist.
    => 9 findings · 4 errors · 4 warnings · 1 info · exit 1

clean-fleet (3 files): distinct descriptions (largest pair
overlap 6/36 = 0.167), tools declared everywhere, grants
matching intent — and the three tools forms (flow list,
block list, comma scalar) all exercised
    => 0 findings · exit 0

Both directions are covered per rule: the fault present
earns the finding with the right rule and severity, the
fault absent earns none. And the auditor is proven
read-only: every fixture file's bytes are unchanged after
a run.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]
SRC = PACK_ROOT / "src" / "fleet_audit.py"
EXAMPLES = PACK_ROOT / "examples" / "fleet-audit"
sys.path.insert(0, str(PACK_ROOT / "src"))

import fleet_audit as fa  # noqa: E402

FAULTY = EXAMPLES / "faulty-fleet"
CLEAN = EXAMPLES / "clean-fleet"


def run_cli(*args):
    proc = subprocess.run(
        [sys.executable, str(SRC), *args],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def audit_dict(folder):
    return fa.audit_folder(Path(folder))


def rules(audit, rule=None, severity=None):
    out = audit["findings"]
    if rule is not None:
        out = [f for f in out if f["rule"] == rule]
    if severity is not None:
        out = [f for f in out if f["severity"] == severity]
    return out


def write_fleet(testcase, files):
    """files: {relative path: text}. Returns the folder."""
    tmp = tempfile.TemporaryDirectory()
    testcase.addCleanup(tmp.cleanup)
    root = Path(tmp.name)
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def agent_file(name, description=None, tools="Read",
               model=None, body="Body."):
    lines = ["---", f"name: {name}"]
    if description is not None:
        lines.append(f"description: {description}")
    if tools is not None:
        lines.append(f"tools: {tools}")
    if model is not None:
        lines.append(f"model: {model}")
    lines += ["---", "", body, ""]
    return "\n".join(lines)


LONG_A = ("Reviews submitted code for correctness problems "
          "and reports them to the team.")
LONG_B = ("Builds the public API endpoints that the mobile "
          "clients call every day.")


class TestFixtures(unittest.TestCase):
    def test_faulty_fleet_matches_hand_counted_table(self):
        audit = audit_dict(FAULTY)
        self.assertEqual(audit["files"], 10)
        self.assertEqual(audit["agents_parsed"], 9)
        self.assertEqual(audit["unrecognized"], 1)
        self.assertEqual(audit["summary"],
                         {"findings": 9, "errors": 4,
                          "warnings": 4, "info": 1})
        self.assertFalse(audit["clean"])
        counts = {}
        for f in audit["findings"]:
            counts[f["rule"]] = counts.get(f["rule"], 0) + 1
        self.assertEqual(counts, {
            "DESCRIPTION_IDENTICAL": 1,
            "DESCRIPTION_MISSING": 1,
            "DUPLICATE_NAME": 1,
            "UNRECOGNIZED_FILE": 1,
            "DESCRIPTION_COLLISION": 2,
            "DESCRIPTION_SHORT": 1,
            "ROLE_TOOL_MISMATCH": 1,
            "TOOLS_UNSPECIFIED": 1,
        })

    def test_faulty_fleet_similarities_are_the_hand_math(self):
        audit = audit_dict(FAULTY)
        pairs = {}
        for f in audit["findings"]:
            if f["similarity"] is not None:
                pairs[tuple(f["agents"])] = f["similarity"]
        self.assertEqual(pairs, {
            ("code-reviewer", "pr-reviewer"): 0.963,
            ("code-reviewer", "docs-reviewer"): 0.645,
            ("docs-reviewer", "pr-reviewer"): 0.625,
        })

    def test_faulty_fleet_cli_exit_1(self):
        code, out, _ = run_cli("audit", str(FAULTY))
        self.assertEqual(code, 1)
        self.assertIn("Summary: 9 findings · 4 errors · "
                      "4 warnings · 1 info", out)

    def test_clean_fleet_zero_findings_exit_0(self):
        audit = audit_dict(CLEAN)
        self.assertEqual(audit["summary"]["findings"], 0)
        self.assertTrue(audit["clean"])
        code, out, _ = run_cli("audit", str(CLEAN))
        self.assertEqual(code, 0)
        self.assertIn("Verdict: CLEAN", out)

    def test_duplicate_finding_names_the_drift(self):
        audit = audit_dict(FAULTY)
        dup = rules(audit, "DUPLICATE_NAME")
        self.assertEqual(len(dup), 1)
        self.assertIn("disagree on description, tools, "
                      "model", dup[0]["message"])
        self.assertEqual(dup[0]["files"],
                         ["deploy-helper.md",
                          "extra/deploy-helper.md"])

    def test_inventory_fields(self):
        audit = audit_dict(FAULTY)
        by_name = {}
        for a in audit["agents"]:
            by_name.setdefault(a["name"], []).append(a)
        sec = by_name["security-reviewer"][0]
        self.assertEqual(sec["tools"], ["Read", "Grep", "Edit"])
        self.assertEqual(sec["model"], "opus")
        self.assertEqual(sec["description_chars"], 171)
        gen = by_name["general-researcher"][0]
        self.assertIsNone(gen["tools"])  # field absent
        sil = by_name["silent-helper"][0]
        self.assertIsNone(sil["description"])
        self.assertEqual(sil["description_chars"], 0)
        # the subfolder agent is walked, with its path shown
        extra = [a for a in by_name["deploy-helper"]
                 if a["file"] == "extra/deploy-helper.md"]
        self.assertEqual(len(extra), 1)


class TestSimilarityMetric(unittest.TestCase):
    """The metric is Jaccard over lowercase alphanumeric
    token sets. Every expectation below is counted by hand
    in the comment above it."""

    def test_known_pair(self):
        # {reviews, code, for, bugs} vs {reviews, code, for,
        # style}: shared 3, union 5 -> 0.6
        self.assertAlmostEqual(
            fa.similarity("reviews code for bugs",
                          "reviews code for style"), 0.6)

    def test_identical_descriptions(self):
        self.assertEqual(
            fa.similarity("reviews code carefully",
                          "reviews code carefully"), 1.0)

    def test_disjoint_descriptions(self):
        self.assertEqual(
            fa.similarity("alpha beta", "gamma delta"), 0.0)

    def test_case_and_punctuation_do_not_matter(self):
        # both tokenize to {reviews, code}
        self.assertEqual(
            fa.similarity("Reviews, CODE!", "reviews code"),
            1.0)

    def test_repeated_words_count_once(self):
        # both tokenize to {code}
        self.assertEqual(
            fa.similarity("code code code", "code"), 1.0)

    def test_hyphenated_words_split(self):
        # read-only tokenizes to {read, only}; both sides
        # become {read, only, review}
        self.assertEqual(
            fa.similarity("read-only review",
                          "read only review"), 1.0)

    def test_empty_side_is_zero(self):
        self.assertEqual(fa.similarity("", "alpha"), 0.0)


class TestCollisionThresholds(unittest.TestCase):
    def test_exactly_at_collision_line_warns(self):
        # {red, blue, green} vs {red, blue, gold}: shared 2,
        # union 4 -> 0.5, which is >= the 0.50 line (and the
        # repeat-padding takes both descriptions past the
        # 40-character short floor without changing the sets)
        folder = write_fleet(self, {
            "one.md": agent_file(
                "agent-one",
                "red blue green red blue green red blue "
                "green red blue"),
            "two.md": agent_file(
                "agent-two",
                "red blue gold red blue gold red blue gold "
                "red blue gold"),
        })
        audit = audit_dict(folder)
        hits = rules(audit, "DESCRIPTION_COLLISION")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["similarity"], 0.5)
        self.assertEqual(rules(audit, "DESCRIPTION_IDENTICAL"),
                         [])

    def test_exactly_at_identical_line_errors(self):
        # {one..nine} (9) vs {one..ten} (10): shared 9,
        # union 10 -> 0.9, which is >= the 0.90 line
        folder = write_fleet(self, {
            "one.md": agent_file(
                "agent-one",
                "one two three four five six seven eight "
                "nine"),
            "two.md": agent_file(
                "agent-two",
                "one two three four five six seven eight "
                "nine ten"),
        })
        audit = audit_dict(folder)
        hits = rules(audit, "DESCRIPTION_IDENTICAL")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["similarity"], 0.9)
        self.assertEqual(rules(audit, "DESCRIPTION_COLLISION"),
                         [])

    def test_just_below_identical_line_only_warns(self):
        # {one..eight} (8) vs {one..ten} (10): shared 8,
        # union 10 -> 0.8
        folder = write_fleet(self, {
            "one.md": agent_file(
                "agent-one",
                "one two three four five six seven eight "
                "eight"),
            "two.md": agent_file(
                "agent-two",
                "one two three four five six seven eight "
                "nine ten"),
        })
        audit = audit_dict(folder)
        self.assertEqual(rules(audit, "DESCRIPTION_IDENTICAL"),
                         [])
        hits = rules(audit, "DESCRIPTION_COLLISION")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["similarity"], 0.8)

    def test_below_collision_line_is_quiet(self):
        # agent-two's description is a builder's, so it gets
        # a write-capable tool; the pair itself shares too
        # few tokens to collide
        folder = write_fleet(self, {
            "one.md": agent_file("agent-one", LONG_A),
            "two.md": agent_file("agent-two", LONG_B,
                                 tools="Bash, Read"),
        })
        audit = audit_dict(folder)
        self.assertEqual(audit["findings"], [])


class TestFrontmatterParser(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.md"
            path.write_text(text, encoding="utf-8")
            return fa._read_agent(path)

    def test_comma_separated_tools(self):
        agent, reason = self.parse(
            "---\nname: a\ndescription: d\n"
            "tools: Read, Grep, Edit\n---\nbody\n")
        self.assertIsNone(reason)
        self.assertEqual(agent["tools"],
                         ["Read", "Grep", "Edit"])

    def test_flow_list_tools(self):
        agent, reason = self.parse(
            "---\nname: a\ndescription: d\n"
            "tools: [Read, Grep]\n---\n")
        self.assertIsNone(reason)
        self.assertEqual(agent["tools"], ["Read", "Grep"])

    def test_block_list_tools(self):
        agent, reason = self.parse(
            "---\nname: a\ndescription: d\ntools:\n"
            "  - Read\n  - Grep\nmodel: sonnet\n---\n")
        self.assertIsNone(reason)
        self.assertEqual(agent["tools"], ["Read", "Grep"])
        self.assertEqual(agent["model"], "sonnet")

    def test_quoted_description_with_colon(self):
        agent, reason = self.parse(
            '---\nname: a\n'
            'description: "Reviews: code and docs"\n---\n')
        self.assertIsNone(reason)
        self.assertEqual(agent["description"],
                         "Reviews: code and docs")

    def test_single_quoted_scalar(self):
        agent, reason = self.parse(
            "---\nname: 'it''s me'\ndescription: d\n---\n")
        self.assertIsNone(reason)
        self.assertEqual(agent["name"], "it's me")

    def test_unknown_keys_are_ignored(self):
        agent, reason = self.parse(
            "---\nname: a\ndescription: d\ncolor: blue\n"
            "memory: project\n---\n")
        self.assertIsNone(reason)
        self.assertEqual(agent["name"], "a")

    def test_crlf_is_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.md"
            path.write_bytes(
                b"---\r\nname: a\r\ndescription: d\r\n"
                b"tools: Read\r\n---\r\nbody\r\n")
            agent, reason = fa._read_agent(path)
        self.assertIsNone(reason)
        self.assertEqual(agent["tools"], ["Read"])

    def test_missing_frontmatter_is_unrecognized(self):
        agent, reason = self.parse("# Just notes\n")
        self.assertIsNone(agent)
        self.assertIn("no frontmatter", reason)

    def test_unclosed_frontmatter_is_unrecognized(self):
        agent, reason = self.parse(
            "---\nname: a\ndescription: d\n")
        self.assertIsNone(agent)
        self.assertIn("never closed", reason)

    def test_nested_map_is_unrecognized(self):
        agent, reason = self.parse(
            "---\nname: a\ndescription: d\nconfig:\n"
            "  depth: 2\n---\n")
        self.assertIsNone(agent)
        self.assertIn("outside the supported subset", reason)

    def test_block_scalar_is_unrecognized(self):
        agent, reason = self.parse(
            "---\nname: a\ndescription: |\n  long text\n---\n")
        self.assertIsNone(agent)
        self.assertIn("block scalars", reason)

    def test_duplicate_key_is_unrecognized(self):
        agent, reason = self.parse(
            "---\nname: a\nname: b\ndescription: d\n---\n")
        self.assertIsNone(agent)
        self.assertIn("duplicate key", reason)

    def test_missing_name_is_unrecognized(self):
        agent, reason = self.parse(
            "---\ndescription: d\ntools: Read\n---\n")
        self.assertIsNone(agent)
        self.assertIn("no `name`", reason)

    def test_empty_tools_value_means_field_absent(self):
        agent, reason = self.parse(
            "---\nname: a\ndescription: d\ntools:\n---\n")
        self.assertIsNone(reason)
        self.assertIsNone(agent["tools"])
        self.assertFalse(agent["tools_empty"])

    def test_explicit_empty_tools_list(self):
        agent, reason = self.parse(
            "---\nname: a\ndescription: d\ntools: []\n---\n")
        self.assertIsNone(reason)
        self.assertEqual(agent["tools"], [])
        self.assertTrue(agent["tools_empty"])


class TestPerAgentRules(unittest.TestCase):
    def test_missing_description_errors(self):
        folder = write_fleet(self, {
            "a.md": agent_file("agent-a", None)})
        audit = audit_dict(folder)
        hits = rules(audit, "DESCRIPTION_MISSING")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], "ERROR")

    def test_empty_description_also_missing(self):
        folder = write_fleet(self, {
            "a.md": agent_file("agent-a", '""')})
        # description: "" parses to an empty string
        audit = audit_dict(folder)
        self.assertEqual(
            len(rules(audit, "DESCRIPTION_MISSING")), 1)

    def test_description_present_is_quiet(self):
        folder = write_fleet(self, {
            "a.md": agent_file("agent-a", LONG_A)})
        audit = audit_dict(folder)
        self.assertEqual(audit["findings"], [])

    def test_short_description_warns(self):
        folder = write_fleet(self, {
            "a.md": agent_file("agent-a", "x" * 39)})
        audit = audit_dict(folder)
        hits = rules(audit, "DESCRIPTION_SHORT")
        self.assertEqual(len(hits), 1)
        self.assertIn("39-character", hits[0]["message"])

    def test_exactly_at_floor_is_quiet(self):
        folder = write_fleet(self, {
            "a.md": agent_file("agent-a", "x" * 40)})
        audit = audit_dict(folder)
        self.assertEqual(rules(audit, "DESCRIPTION_SHORT"), [])

    def test_tools_unspecified_is_info(self):
        folder = write_fleet(self, {
            "a.md": agent_file("agent-a", LONG_A, tools=None)})
        audit = audit_dict(folder)
        hits = rules(audit, "TOOLS_UNSPECIFIED")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], "INFO")

    def test_tools_declared_is_quiet(self):
        folder = write_fleet(self, {
            "a.md": agent_file("agent-a", LONG_A)})
        audit = audit_dict(folder)
        self.assertEqual(rules(audit, "TOOLS_UNSPECIFIED"), [])

    def test_explicit_empty_tools_warns_not_info(self):
        folder = write_fleet(self, {
            "a.md": agent_file("agent-a", LONG_A, tools="[]")})
        audit = audit_dict(folder)
        hits = rules(audit, "TOOLS_EMPTY")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], "WARN")
        self.assertEqual(rules(audit, "TOOLS_UNSPECIFIED"), [])


class TestRoleToolMismatch(unittest.TestCase):
    REVIEWER = ("Reviews submitted code for correctness "
                "problems and reports them to the team.")

    def test_reviewer_with_edit_warns(self):
        folder = write_fleet(self, {
            "a.md": agent_file("code-reviewer", self.REVIEWER,
                               tools="Read, Edit")})
        audit = audit_dict(folder)
        hits = rules(audit, "ROLE_TOOL_MISMATCH")
        self.assertEqual(len(hits), 1)
        self.assertIn("Edit", hits[0]["message"])
        self.assertIn("heuristic", hits[0]["message"])

    def test_reviewer_with_only_bash_warns(self):
        folder = write_fleet(self, {
            "a.md": agent_file("code-reviewer", self.REVIEWER,
                               tools="Read, Bash")})
        audit = audit_dict(folder)
        hits = rules(audit, "ROLE_TOOL_MISMATCH")
        self.assertEqual(len(hits), 1)
        self.assertIn("Bash", hits[0]["message"])

    def test_reviewer_with_read_tools_is_quiet(self):
        folder = write_fleet(self, {
            "a.md": agent_file("code-reviewer", self.REVIEWER,
                               tools="Read, Grep, Glob")})
        audit = audit_dict(folder)
        self.assertEqual(audit["findings"], [])

    def test_declared_write_intent_suppresses_the_flag(self):
        # the description itself says it edits: the grant
        # matches the promise, so no mismatch
        folder = write_fleet(self, {
            "a.md": agent_file(
                "code-reviewer",
                "Reviews submitted code and edits the "
                "changelog whenever the review lands.",
                tools="Read, Edit")})
        audit = audit_dict(folder)
        self.assertEqual(rules(audit, "ROLE_TOOL_MISMATCH"), [])

    def test_builder_without_write_tools_warns(self):
        folder = write_fleet(self, {
            "a.md": agent_file("api-builder", LONG_B,
                               tools="Read, Grep")})
        audit = audit_dict(folder)
        hits = rules(audit, "ROLE_TOOL_MISMATCH")
        self.assertEqual(len(hits), 1)
        self.assertIn("cannot do the job", hits[0]["message"])

    def test_builder_with_bash_is_quiet(self):
        folder = write_fleet(self, {
            "a.md": agent_file("api-builder", LONG_B,
                               tools="Bash, Read")})
        audit = audit_dict(folder)
        self.assertEqual(audit["findings"], [])

    def test_no_intent_words_no_flag_either_way(self):
        folder = write_fleet(self, {
            "a.md": agent_file(
                "helper",
                "Handles miscellaneous chores around the "
                "repository whenever someone asks.",
                tools="Edit, Read")})
        audit = audit_dict(folder)
        self.assertEqual(audit["findings"], [])


class TestDuplicateNames(unittest.TestCase):
    def test_identical_definitions_error_without_drift(self):
        text = agent_file("same-name", LONG_A, tools="Read")
        folder = write_fleet(self, {
            "a.md": text, "sub/b.md": text})
        audit = audit_dict(folder)
        hits = rules(audit, "DUPLICATE_NAME")
        self.assertEqual(len(hits), 1)
        self.assertNotIn("disagree", hits[0]["message"])
        self.assertEqual(hits[0]["files"],
                         ["a.md", "sub/b.md"])

    def test_different_descriptions_note_the_drift(self):
        folder = write_fleet(self, {
            "a.md": agent_file("same-name", LONG_A,
                               tools="Read"),
            "b.md": agent_file("same-name", LONG_B,
                               tools="Read")})
        audit = audit_dict(folder)
        hits = rules(audit, "DUPLICATE_NAME")
        self.assertEqual(len(hits), 1)
        self.assertIn("disagree on description",
                      hits[0]["message"])

    def test_unique_names_are_quiet(self):
        folder = write_fleet(self, {
            "a.md": agent_file("agent-a", LONG_A),
            "b.md": agent_file("agent-b", LONG_B,
                               tools="Bash, Read")})
        audit = audit_dict(folder)
        self.assertEqual(audit["findings"], [])


class TestCliAndJson(unittest.TestCase):
    def test_missing_folder_is_exit_2(self):
        code, out, err = run_cli("audit", "/no/such/folder-xyz")
        self.assertEqual(code, 2)
        self.assertIn("folder not found", out + err)

    def test_missing_folder_json_error(self):
        code, out, _ = run_cli("audit", "/no/such/folder-xyz",
                               "--json")
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(out))

    def test_empty_folder_is_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, _ = run_cli("audit", tmp)
        self.assertEqual(code, 2)

    def test_folder_without_markdown_is_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "notes.txt").write_text("hi")
            code, _, _ = run_cli("audit", tmp)
        self.assertEqual(code, 2)

    def test_argparse_error_is_json_in_json_mode(self):
        code, out, _ = run_cli("audit", "--json")
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(out))

    def test_json_summary_recounts_against_findings(self):
        code, out, _ = run_cli("audit", str(FAULTY), "--json")
        self.assertEqual(code, 1)
        audit = json.loads(out)
        findings = audit["findings"]
        self.assertEqual(audit["summary"]["findings"],
                         len(findings))
        for key, severity in (("errors", "ERROR"),
                              ("warnings", "WARN"),
                              ("info", "INFO")):
            self.assertEqual(
                audit["summary"][key],
                sum(1 for f in findings
                    if f["severity"] == severity))
        self.assertEqual(audit["clean"],
                         audit["summary"]["errors"] == 0)

    def test_json_shape(self):
        code, out, _ = run_cli("audit", str(CLEAN), "--json")
        self.assertEqual(code, 0)
        audit = json.loads(out)
        self.assertEqual(audit["tool"], "fleet-audit")
        self.assertEqual(audit["files"], 3)
        self.assertEqual(audit["agents_parsed"], 3)
        self.assertEqual(audit["thresholds"],
                         {"collision": 0.5, "identical": 0.9,
                          "short_description_chars": 40})


class TestReadOnly(unittest.TestCase):
    def test_fixture_bytes_unchanged_after_a_run(self):
        def snapshot(root):
            return {p.relative_to(root).as_posix():
                    hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(root.rglob("*"))
                    if p.is_file()}
        before = snapshot(FAULTY)
        code, _, _ = run_cli("audit", str(FAULTY))
        self.assertEqual(code, 1)
        self.assertEqual(snapshot(FAULTY), before)


if __name__ == "__main__":
    unittest.main()
