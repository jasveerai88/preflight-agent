# Contributing

Thanks for looking. preflight-agent is a merged suite:
five auditors with deliberately separate scopes, one
dispatcher. Contributions that keep each module's scope
sharp — and the merge honest — are welcome.

## Ground rules

- **Standard library only.** No third-party
  dependencies, no network calls, ever — in any module
  or in the dispatcher.
- **The engines are ported, not rewritten.** Each
  module's checks, verdict words, thresholds, and
  honesty limits are its source tool's contract. A
  behaviour change to a module belongs upstream in that
  module's own test suite first: its ported suite here
  must keep passing, and any intentional divergence
  must be named in CHANGELOG.md, never silent.
- **Deterministic, always.** No LLM, no randomness, no
  timestamps in any receipt: the same target must
  produce a byte-identical report on every run.
- **Read-only scanning, always.** No scan (module or
  combined) may write to, execute, import, or install
  anything in the target. The one exception is
  secret-guard's `fix --apply`, which writes only the
  files it is handed, with backups — and `scan` must
  never call it. Tests prove fixture trees are
  byte-identical after scans; keep them passing.
- **Skipped is not clean.** A module with nothing to
  audit reports itself skipped with a reason. Never
  let the combined verdict count a skipped module as
  evidence of safety.
- **Secrets are never printed.** Findings cite key
  paths and fingerprints (first characters + length)
  only — in every module, in the combined receipt, and
  in test fixtures (which stay fictional and fake).
- **No tracebacks, ever.** Bad input is a clean
  `{"error": ...}` with exit 2 — including argument
  errors in `--json` mode, at dispatcher and module
  level alike.

## Running the tests

```bash
python3 -m unittest discover -s tests
```

All 300 tests must pass on a clean checkout with
nothing but Python 3.10+. The five module suites live
in `tests/test_<module>.py`; dispatcher and combined
scan tests in `tests/test_preflight_agent.py`.
