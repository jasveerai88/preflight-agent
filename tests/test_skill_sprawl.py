#!/usr/bin/env python3
"""Tests for skill-sprawl. Stdlib unittest only.

Run from the pack root:
    python3 -m unittest discover -s tests -v

The bundled fixtures are CONSTRUCTED skill trees (fictional
skills) whose expected findings were worked out by hand
from the rule table in SKILL.md and the similarity metric
— token sets enumerated by hand below — never copied from
the tool's own output:

sprawling-skills (13 .md files seen: 11 candidates,
2 supporting docs ignored — meeting-notes-summarizer/
templates.md and reading-notes.md, neither named SKILL.md
nor opening with frontmatter; 10 skills parsed, 1
unrecognized):
    pdf-form-filler    desc tokens (20): fill pdf forms
      from csv data files automatically use when a batch
      of insurance claim needs values mapped into fields
      (124 chars)
    pdf-form-completer the same 20 tokens + {overnight}
      = 21 (134 chars)
      -> pair similarity 20/21 = 0.952 >= 0.90
         DESCRIPTION_IDENTICAL (ERROR)
    meeting-notes-summarizer desc tokens (17): summarize
      meeting notes into decisions action items and owners
      use when a team ends the need summary (123 chars)
    meeting-minutes-writer desc tokens (17): turn meeting
      notes into decisions action items and owners use
      when a workshop ends the need minutes (112 chars)
      shared 14 -> 14/20 = 0.700
         DESCRIPTION_COLLISION (WARN)
    invoice-chaser x2 files (billing/invoice-chaser.md is
      a loose file in a subfolder; ops/invoice-chaser/ is
      the second scope's SKILL.md), descriptions share
      only {use, when, polite} = 3/32 = 0.094 (no pair
      finding), bodies differ too
         DUPLICATE_NAME (ERROR, drift on description, body)
    quiet-archiver     no description key at all
         DESCRIPTION_MISSING (ERROR)
    kitchen-sink-cookbook body of exactly 540 lines
         BODY_OVERSIZED (WARN)
    tiny-helper        description "Sorts files. Use when
      tidying." = 30 chars
         DESCRIPTION_SHORT (WARN)
    weather-briefing   description (104 chars) with none
      of the trigger phrases
         NO_TRIGGER_PHRASE (INFO)
    broken-skill       SKILL.md whose frontmatter nests a
      config map
         UNRECOGNIZED_FILE (ERROR)
    every other pair shares < 0.50 of its tokens (the
    largest is meeting-notes-summarizer vs kitchen-sink-
    cookbook, 6/34 = 0.176), so no further pair findings
    exist; the discovery listing totals ~1.2k chars, far
    under the 16,000 budget, so no forecast finding.
    => 8 findings · 4 errors · 3 warnings · 1 info · exit 1

tidy-skills (3 files, all candidates): distinct
descriptions (largest pair overlap 5/32 = 0.156), every
description carries a "Use when" trigger, small bodies
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
SRC = PACK_ROOT / "src" / "skill_sprawl.py"
EXAMPLES = PACK_ROOT / "examples" / "skill-sprawl"
sys.path.insert(0, str(PACK_ROOT / "src"))

import skill_sprawl as ss  # noqa: E402

FAULTY = EXAMPLES / "sprawling-skills"
CLEAN = EXAMPLES / "tidy-skills"


def run_cli(*args):
    proc = subprocess.run(
        [sys.executable, str(SRC), *args],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def audit_dict(folder):
    return ss.audit_folder(Path(folder))


def rules(audit, rule=None, severity=None):
    out = audit["findings"]
    if rule is not None:
        out = [f for f in out if f["rule"] == rule]
    if severity is not None:
        out = [f for f in out if f["severity"] == severity]
    return out


def write_tree(testcase, files):
    """files: {relative path: text}. Returns the folder."""
    tmp = tempfile.TemporaryDirectory()
    testcase.addCleanup(tmp.cleanup)
    root = Path(tmp.name)
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def skill_file(name, description=None, body="Do the thing.",
               extra_lines=()):
    lines = ["---", f"name: {name}"]
    if description is not None:
        lines.append(f"description: {description}")
    lines += list(extra_lines)
    lines += ["---", ""]
    if body:
        lines.append(body)
    lines.append("")
    return "\n".join(lines)


LONG_A = ("Sorts the photo library into albums by date and "
          "place. Use when a trip ends and photos pile up.")
LONG_B = ("Drafts the weekly grocery list from the meal "
          "plan and pantry stock. Use when the plan changes.")


class TestFixtures(unittest.TestCase):
    def test_faulty_tree_matches_hand_counted_table(self):
        audit = audit_dict(FAULTY)
        self.assertEqual(audit["files_seen"], 13)
        self.assertEqual(audit["candidates"], 11)
        self.assertEqual(audit["skills_parsed"], 10)
        self.assertEqual(audit["unrecognized"], 1)
        self.assertEqual(audit["ignored"], 2)
        self.assertEqual(audit["summary"],
                         {"findings": 8, "errors": 4,
                          "warnings": 3, "info": 1})
        self.assertFalse(audit["clean"])
        counts = {}
        for f in audit["findings"]:
            counts[f["rule"]] = counts.get(f["rule"], 0) + 1
        self.assertEqual(counts, {
            "DESCRIPTION_IDENTICAL": 1,
            "DESCRIPTION_MISSING": 1,
            "DUPLICATE_NAME": 1,
            "UNRECOGNIZED_FILE": 1,
            "DESCRIPTION_COLLISION": 1,
            "DESCRIPTION_SHORT": 1,
            "BODY_OVERSIZED": 1,
            "NO_TRIGGER_PHRASE": 1,
        })

    def test_faulty_tree_similarities_are_the_hand_math(self):
        audit = audit_dict(FAULTY)
        pairs = {}
        for f in audit["findings"]:
            if f["similarity"] is not None:
                pairs[tuple(f["skills"])] = f["similarity"]
        self.assertEqual(pairs, {
            ("pdf-form-completer", "pdf-form-filler"): 0.952,
            ("meeting-minutes-writer",
             "meeting-notes-summarizer"): 0.700,
        })

    def test_faulty_tree_cli_exit_1(self):
        code, out, _ = run_cli("audit", str(FAULTY))
        self.assertEqual(code, 1)
        self.assertIn("Summary: 8 findings · 4 errors · "
                      "3 warnings · 1 info", out)

    def test_clean_tree_zero_findings_exit_0(self):
        audit = audit_dict(CLEAN)
        self.assertEqual(audit["files_seen"], 3)
        self.assertEqual(audit["candidates"], 3)
        self.assertEqual(audit["ignored"], 0)
        self.assertEqual(audit["summary"]["findings"], 0)
        self.assertTrue(audit["clean"])
        code, out, _ = run_cli("audit", str(CLEAN))
        self.assertEqual(code, 0)
        self.assertIn("Verdict: CLEAN", out)

    def test_duplicate_finding_names_the_drift(self):
        audit = audit_dict(FAULTY)
        dup = rules(audit, "DUPLICATE_NAME")
        self.assertEqual(len(dup), 1)
        self.assertIn("disagree on description, body",
                      dup[0]["message"])
        self.assertEqual(dup[0]["files"],
                         ["billing/invoice-chaser.md",
                          "ops/invoice-chaser/SKILL.md"])

    def test_inventory_fields(self):
        audit = audit_dict(FAULTY)
        by_key = {(s["name"], s["file"]): s
                  for s in audit["skills"]}
        filler = by_key[("pdf-form-filler",
                         "pdf-form-filler/SKILL.md")]
        self.assertEqual(filler["description_chars"], 124)
        completer = by_key[("pdf-form-completer",
                            "pdf-form-completer/SKILL.md")]
        self.assertEqual(completer["description_chars"], 134)
        quiet = by_key[("quiet-archiver",
                        "quiet-archiver/SKILL.md")]
        self.assertIsNone(quiet["description"])
        self.assertEqual(quiet["description_chars"], 0)
        cook = by_key[("kitchen-sink-cookbook",
                       "kitchen-sink-cookbook/SKILL.md")]
        self.assertEqual(cook["body_lines"], 540)
        # the loose file in a subfolder is walked, with its
        # path shown
        loose = by_key[("tiny-helper", "loose/tiny-helper.md")]
        self.assertEqual(loose["description_chars"], 30)

    def test_totals_recount_against_inventory(self):
        audit = audit_dict(FAULTY)
        skills = audit["skills"]
        self.assertEqual(
            audit["totals"]["body_lines"],
            sum(s["body_lines"] for s in skills))
        self.assertEqual(
            audit["totals"]["est_tokens"],
            sum(s["est_tokens"] for s in skills))
        self.assertEqual(
            audit["totals"]["listing_chars"],
            sum(s["listing_chars"] for s in skills))
        self.assertEqual(
            audit["discovery"]["total_listing_chars"],
            audit["totals"]["listing_chars"])

    def test_discovery_forecast_quiet_on_fixtures(self):
        for tree in (FAULTY, CLEAN):
            audit = audit_dict(tree)
            self.assertEqual(audit["discovery"]["at_risk"], [])
            self.assertEqual(
                rules(audit, "DISCOVERY_BUDGET_EXCEEDED"), [])


class TestSimilarityMetric(unittest.TestCase):
    """The metric is Jaccard over lowercase alphanumeric
    token sets. Every expectation below is counted by hand
    in the comment above it."""

    def test_known_pair(self):
        # {sorts, photos, by, date} vs {sorts, photos, by,
        # place}: shared 3, union 5 -> 0.6
        self.assertAlmostEqual(
            ss.similarity("sorts photos by date",
                          "sorts photos by place"), 0.6)

    def test_identical_descriptions(self):
        self.assertEqual(
            ss.similarity("sorts photos carefully",
                          "sorts photos carefully"), 1.0)

    def test_disjoint_descriptions(self):
        self.assertEqual(
            ss.similarity("alpha beta", "gamma delta"), 0.0)

    def test_case_and_punctuation_do_not_matter(self):
        # both tokenize to {sorts, photos}
        self.assertEqual(
            ss.similarity("Sorts, PHOTOS!", "sorts photos"),
            1.0)

    def test_repeated_words_count_once(self):
        # both tokenize to {photos}
        self.assertEqual(
            ss.similarity("photos photos photos", "photos"),
            1.0)

    def test_hyphenated_words_split(self):
        # read-only tokenizes to {read, only}; both sides
        # become {read, only, sorting}
        self.assertEqual(
            ss.similarity("read-only sorting",
                          "read only sorting"), 1.0)

    def test_empty_side_is_zero(self):
        self.assertEqual(ss.similarity("", "alpha"), 0.0)


class TestCollisionThresholds(unittest.TestCase):
    def test_exactly_at_collision_line_warns(self):
        # {red, blue, green} vs {red, blue, gold}: shared 2,
        # union 4 -> 0.5, which is >= the 0.50 line (the
        # repeat-padding takes both descriptions past the
        # 40-character short floor without changing the sets)
        folder = write_tree(self, {
            "one/SKILL.md": skill_file(
                "skill-one",
                "red blue green red blue green red blue "
                "green red blue"),
            "two/SKILL.md": skill_file(
                "skill-two",
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
        folder = write_tree(self, {
            "one/SKILL.md": skill_file(
                "skill-one",
                "one two three four five six seven eight "
                "nine"),
            "two/SKILL.md": skill_file(
                "skill-two",
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
        folder = write_tree(self, {
            "one/SKILL.md": skill_file(
                "skill-one",
                "one two three four five six seven eight "
                "eight"),
            "two/SKILL.md": skill_file(
                "skill-two",
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
        folder = write_tree(self, {
            "one/SKILL.md": skill_file("skill-one", LONG_A),
            "two/SKILL.md": skill_file("skill-two", LONG_B),
        })
        audit = audit_dict(folder)
        self.assertEqual(
            rules(audit, "DESCRIPTION_COLLISION"), [])
        self.assertEqual(
            rules(audit, "DESCRIPTION_IDENTICAL"), [])


class TestFrontmatterParser(unittest.TestCase):
    def parse(self, text, filename="SKILL.md"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / filename
            path.write_text(text, encoding="utf-8")
            return ss._read_skill(path)

    def test_quoted_description_with_colon(self):
        skill, reason = self.parse(
            '---\nname: a\n'
            'description: "Sorts: photos and videos"\n---\n'
            'body\n')
        self.assertIsNone(reason)
        self.assertEqual(skill["description"],
                         "Sorts: photos and videos")

    def test_single_quoted_scalar(self):
        skill, reason = self.parse(
            "---\nname: 'it''s mine'\ndescription: d\n---\n"
            "body\n")
        self.assertIsNone(reason)
        self.assertEqual(skill["name"], "it's mine")

    def test_unknown_keys_are_ignored(self):
        skill, reason = self.parse(
            "---\nname: a\ndescription: d\n"
            "allowed-tools: Read, Grep\nlicense: MIT\n---\n"
            "body\n")
        self.assertIsNone(reason)
        self.assertEqual(skill["name"], "a")

    def test_crlf_is_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_bytes(
                b"---\r\nname: a\r\ndescription: d\r\n"
                b"---\r\nbody\r\n")
            skill, reason = ss._read_skill(path)
        self.assertIsNone(reason)
        self.assertEqual(skill["name"], "a")
        self.assertEqual(skill["body_lines"], 1)

    def test_missing_frontmatter_is_unrecognized(self):
        skill, reason = self.parse("# Just a doc\n")
        self.assertIsNone(skill)
        self.assertIn("no frontmatter", reason)

    def test_unclosed_frontmatter_is_unrecognized(self):
        skill, reason = self.parse(
            "---\nname: a\ndescription: d\n")
        self.assertIsNone(skill)
        self.assertIn("never closed", reason)

    def test_nested_map_is_unrecognized(self):
        skill, reason = self.parse(
            "---\nname: a\ndescription: d\nconfig:\n"
            "  depth: 2\n---\nbody\n")
        self.assertIsNone(skill)
        self.assertIn("outside the supported subset", reason)

    def test_block_scalar_is_unrecognized(self):
        skill, reason = self.parse(
            "---\nname: a\ndescription: |\n  long text\n---\n")
        self.assertIsNone(skill)
        self.assertIn("block scalars", reason)

    def test_duplicate_key_is_unrecognized(self):
        skill, reason = self.parse(
            "---\nname: a\nname: b\ndescription: d\n---\n")
        self.assertIsNone(skill)
        self.assertIn("duplicate key", reason)

    def test_missing_name_is_unrecognized(self):
        skill, reason = self.parse(
            "---\ndescription: d\n---\nbody\n")
        self.assertIsNone(skill)
        self.assertIn("no `name`", reason)

    def test_body_metrics_and_token_estimate(self):
        # body "abcde" is 5 chars, 1 line -> ceil(5/4) = 2
        skill, reason = self.parse(
            "---\nname: a\ndescription: d\n---\n\nabcde\n")
        self.assertIsNone(reason)
        self.assertEqual(skill["body_lines"], 1)
        self.assertEqual(skill["body_chars"], 5)
        self.assertEqual(skill["est_tokens"], 2)
        # body "abcd" is 4 chars -> ceil(4/4) = 1
        skill, _ = self.parse(
            "---\nname: a\ndescription: d\n---\n\nabcd\n")
        self.assertEqual(skill["est_tokens"], 1)
        # interior blank lines count in lines and chars:
        # "one\n\ntwo" = 3 lines, 8 chars -> 2 tokens
        skill, _ = self.parse(
            "---\nname: a\ndescription: d\n---\n\n"
            "one\n\ntwo\n")
        self.assertEqual(skill["body_lines"], 3)
        self.assertEqual(skill["body_chars"], 8)
        self.assertEqual(skill["est_tokens"], 2)


class TestCandidateDiscovery(unittest.TestCase):
    def test_loose_file_without_frontmatter_is_ignored(self):
        folder = write_tree(self, {
            "real/SKILL.md": skill_file("real-skill", LONG_A),
            "notes.md": "# Loose notes, no frontmatter\n",
        })
        audit = audit_dict(folder)
        self.assertEqual(audit["files_seen"], 2)
        self.assertEqual(audit["candidates"], 1)
        self.assertEqual(audit["ignored"], 1)
        self.assertEqual(audit["skills_parsed"], 1)

    def test_loose_file_with_frontmatter_is_a_candidate(self):
        folder = write_tree(self, {
            "loose.md": skill_file("loose-skill", LONG_A),
        })
        audit = audit_dict(folder)
        self.assertEqual(audit["candidates"], 1)
        self.assertEqual(audit["skills_parsed"], 1)
        self.assertEqual(audit["skills"][0]["file"],
                         "loose.md")

    def test_skill_md_without_frontmatter_is_unrecognized(self):
        folder = write_tree(self, {
            "bad/SKILL.md": "# No frontmatter here\n",
        })
        audit = audit_dict(folder)
        self.assertEqual(audit["candidates"], 1)
        self.assertEqual(audit["skills_parsed"], 0)
        hits = rules(audit, "UNRECOGNIZED_FILE")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["files"], ["bad/SKILL.md"])

    def test_tree_with_only_supporting_docs_is_exit_2(self):
        folder = write_tree(self, {
            "notes.md": "# Just notes\n",
            "more.md": "More notes, no frontmatter.\n",
        })
        code, out, err = run_cli("audit", str(folder))
        self.assertEqual(code, 2)
        self.assertIn("no skill definitions found",
                      out + err)


class TestPerSkillRules(unittest.TestCase):
    def test_missing_description_errors(self):
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("skill-a", None)})
        audit = audit_dict(folder)
        hits = rules(audit, "DESCRIPTION_MISSING")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], "ERROR")

    def test_empty_description_also_missing(self):
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("skill-a", '""')})
        # description: "" parses to an empty string
        audit = audit_dict(folder)
        self.assertEqual(
            len(rules(audit, "DESCRIPTION_MISSING")), 1)

    def test_description_present_is_quiet(self):
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("skill-a", LONG_A)})
        audit = audit_dict(folder)
        self.assertEqual(audit["findings"], [])

    def test_missing_description_skips_trigger_check(self):
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("skill-a", None)})
        audit = audit_dict(folder)
        self.assertEqual(rules(audit, "NO_TRIGGER_PHRASE"), [])

    def test_short_description_warns(self):
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("skill-a", "x" * 39)})
        audit = audit_dict(folder)
        hits = rules(audit, "DESCRIPTION_SHORT")
        self.assertEqual(len(hits), 1)
        self.assertIn("39-character", hits[0]["message"])

    def test_exactly_at_floor_is_quiet(self):
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("skill-a", "x" * 40)})
        audit = audit_dict(folder)
        self.assertEqual(rules(audit, "DESCRIPTION_SHORT"), [])

    def test_no_trigger_phrase_is_info(self):
        folder = write_tree(self, {
            "a/SKILL.md": skill_file(
                "skill-a",
                "Produces a tidy summary of the day's "
                "numbers for the kitchen wall chart.")})
        audit = audit_dict(folder)
        hits = rules(audit, "NO_TRIGGER_PHRASE")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], "INFO")
        self.assertIn("heuristic", hits[0]["message"])

    def test_trigger_phrase_present_is_quiet(self):
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("skill-a", LONG_A)})
        audit = audit_dict(folder)
        self.assertEqual(rules(audit, "NO_TRIGGER_PHRASE"), [])

    def test_empty_body_warns(self):
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("skill-a", LONG_A,
                                     body="")})
        audit = audit_dict(folder)
        hits = rules(audit, "BODY_EMPTY")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], "WARN")

    def test_body_present_is_quiet(self):
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("skill-a", LONG_A)})
        audit = audit_dict(folder)
        self.assertEqual(rules(audit, "BODY_EMPTY"), [])

    def test_oversized_body_warns(self):
        body = "\n".join(f"line {i}" for i in range(501))
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("skill-a", LONG_A,
                                     body=body)})
        audit = audit_dict(folder)
        hits = rules(audit, "BODY_OVERSIZED")
        self.assertEqual(len(hits), 1)
        self.assertIn("501-line", hits[0]["message"])

    def test_exactly_at_body_line_is_quiet(self):
        body = "\n".join(f"line {i}" for i in range(500))
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("skill-a", LONG_A,
                                     body=body)})
        audit = audit_dict(folder)
        self.assertEqual(rules(audit, "BODY_OVERSIZED"), [])


class TestDuplicateNames(unittest.TestCase):
    def test_identical_definitions_error_without_drift(self):
        text = skill_file("same-name", LONG_A)
        folder = write_tree(self, {
            "scope-one/SKILL.md": text,
            "scope-two/SKILL.md": text})
        audit = audit_dict(folder)
        hits = rules(audit, "DUPLICATE_NAME")
        self.assertEqual(len(hits), 1)
        self.assertNotIn("disagree", hits[0]["message"])

    def test_drifted_description_only(self):
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("same-name", LONG_A),
            "b/SKILL.md": skill_file("same-name", LONG_B)})
        audit = audit_dict(folder)
        hits = rules(audit, "DUPLICATE_NAME")
        self.assertEqual(len(hits), 1)
        self.assertIn("disagree on description",
                      hits[0]["message"])
        self.assertNotIn("body", hits[0]["message"]
                         .split("disagree on")[1])

    def test_drifted_body_only(self):
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("same-name", LONG_A,
                                     body="First body."),
            "b/SKILL.md": skill_file("same-name", LONG_A,
                                     body="Second body.")})
        audit = audit_dict(folder)
        hits = rules(audit, "DUPLICATE_NAME")
        self.assertEqual(len(hits), 1)
        self.assertIn("disagree on body",
                      hits[0]["message"])

    def test_unique_names_are_quiet(self):
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("skill-a", LONG_A),
            "b/SKILL.md": skill_file("skill-b", LONG_B)})
        audit = audit_dict(folder)
        self.assertEqual(rules(audit, "DUPLICATE_NAME"), [])


class TestDiscoveryBudget(unittest.TestCase):
    def test_skills_starting_past_the_budget_are_named(self):
        # listing chars = len(name) + len(description):
        # alpha: 5 + 15995 = 16000 (fills the budget
        # exactly, starting at offset 0 — shown); bravo,
        # charlie and delta each start at or past offset
        # 16000 -> at risk. Descriptions are single repeated
        # letters, so no pair shares a token and no pair
        # finding interferes.
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("alpha", "a" * 15995),
            "b/SKILL.md": skill_file("bravo", "b" * 100),
            "c/SKILL.md": skill_file("charlie", "c" * 100),
            "d/SKILL.md": skill_file("delta", "d" * 100),
        })
        audit = audit_dict(folder)
        self.assertEqual(audit["discovery"]["budget_chars"],
                         16000)
        self.assertEqual(
            audit["discovery"]["total_listing_chars"],
            16000 + 105 + 107 + 105)
        self.assertEqual(audit["discovery"]["at_risk"],
                         ["bravo", "charlie", "delta"])
        hits = rules(audit, "DISCOVERY_BUDGET_EXCEEDED")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], "WARN")
        self.assertIn("forecast", hits[0]["message"])
        for name in ("bravo", "charlie", "delta"):
            self.assertIn(name, hits[0]["message"])

    def test_straddling_entry_is_not_at_risk(self):
        # alpha's entry is 5 + 15990 = 15995 chars; bravo's
        # starts at offset 15995 — before the line — so it
        # straddles and is NOT at risk, even though the
        # total (16001) passes the budget. The forecast
        # flags entries that START past the line.
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("alpha", "a" * 15990),
            "b/SKILL.md": skill_file("bravo", "b"),
        })
        audit = audit_dict(folder)
        self.assertEqual(
            audit["discovery"]["total_listing_chars"], 16001)
        self.assertEqual(audit["discovery"]["at_risk"], [])
        self.assertEqual(
            rules(audit, "DISCOVERY_BUDGET_EXCEEDED"), [])

    def test_under_budget_is_quiet(self):
        folder = write_tree(self, {
            "a/SKILL.md": skill_file("skill-a", LONG_A),
            "b/SKILL.md": skill_file("skill-b", LONG_B)})
        audit = audit_dict(folder)
        self.assertEqual(audit["discovery"]["at_risk"], [])
        self.assertEqual(
            rules(audit, "DISCOVERY_BUDGET_EXCEEDED"), [])


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
        self.assertEqual(audit["tool"], "skill-sprawl")
        self.assertEqual(audit["files_seen"], 3)
        self.assertEqual(audit["skills_parsed"], 3)
        self.assertEqual(audit["thresholds"], {
            "collision": 0.5, "identical": 0.9,
            "short_description_chars": 40,
            "body_oversized_lines": 500,
            "discovery_budget_chars": 16000})
        self.assertEqual(audit["discovery"]["budget_chars"],
                         16000)


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
