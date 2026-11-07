# Changelog

## v1.0.0 — 2026-11-07 (first publication: the merge ships)

- **The merge story, honestly.** This suite exists
  because the fleet audit (FLEET-AUDIT-2026-11-04)
  looked at 43 published tools and ordered 18 of the
  quiet ones merged into 4 flagships — five standalone
  auditors that each answered one preflight question
  became flagship B1, this suite. clone-guard,
  secret-guard, hook-doctor, fleet-audit and
  skill-sprawl were each shipped, tested tools; nobody
  was installing five of them to answer "is this setup
  safe to run?" One command now runs all five and
  prints one receipt.
- **Engines ported byte-identical.** No check, verdict,
  threshold, or honesty limit changed in the merge:
  each module's engine file is byte-identical to its
  source tool's, and each module still reports its own
  source version (v0.1.0) inside its sections — the
  suite's version, 1.0.0, is the dispatcher's.
- **Tests: 300.** The five source suites carried over
  (276 tests; the only edit is each suite's EXAMPLES
  path, since examples now live per-module under
  `examples/<module>/`) plus 24 dispatcher/scan tests
  written for the merge.
- **The one disclosed exception to read-only:**
  `secret-guard fix --apply` writes — that is its job.
  It rewrites only the config files you name, replacing
  inline secret literals with `${ENV_VAR}` references;
  it is a dry run unless `--apply` is given, it keeps a
  timestamped backup beside each changed file, and it
  re-scans the rewrite before reporting it. It was
  ported intact, and the combined `scan` **never**
  invokes it — scanning stays strictly read-only.
- **Publication prep:** launch-standard README with a
  demo rendered from a real fixture scan (nothing
  retyped), social preview, PyPI metadata (148-char
  description, inside the 200 limit; SPDX MIT). The
  PyPI name
  `preflight-agent` was free (checked 2026-11-07);
  `agent-preflight` remains taken by an unrelated
  project.

## v0.1.0 — 2026-11-07 (stage 1: the merge)

- **preflight-agent created** as the merged flagship
  ordered by FLEET-AUDIT-2026-11-04 (flagship B1): five
  shipped auditors become modules of one "scan your
  AI-agent setup before it runs" suite.
- **Engines ported verbatim** from the source packs —
  clone-guard, secret-guard, hook-doctor, fleet-audit,
  skill-sprawl, each at its source v0.1.0. No check,
  verdict, threshold, or honesty limit was changed;
  each module keeps its own CLI shape behind the suite
  command (`preflight-agent clone-guard DIR`,
  `preflight-agent secret-guard scan|fix FILE...`,
  `preflight-agent hook-doctor audit SETTINGS`,
  `preflight-agent fleet-audit audit FOLDER`,
  `preflight-agent skill-sprawl audit FOLDER`).
- **New: `scan` subcommand** — runs all five modules
  over one setup folder (documented per-module target
  discovery) and prints one combined receipt; `--json`
  emits one object whose per-module sections are the
  exact objects the modules print standalone. Modules
  with nothing to audit report as skipped with a reason,
  never as clean. The combined scan is read-only and
  never invokes secret-guard fix.
- **Tests:** the five source suites ported (276 tests;
  the only edit is each suite's EXAMPLES path — examples
  now live per-module under `examples/<module>/`) plus
  24 new dispatcher/scan tests: 300 total, all passing.
- **Name:** `agent-preflight` was taken on PyPI by an
  unrelated project (checked 2026-11-07), so the suite
  ships as **preflight-agent** (free on PyPI; GitHub
  name collisions are other users' repos, none in this
  account).
- Not yet done (later stages): publication (GitHub repo
  + PyPI), demo assets, launch kit, absorbing the
  unpublished skill-vet and mcp-footprint packs the
  audit also named for this flagship.
