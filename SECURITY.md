# Security Policy

## Reporting a vulnerability

Please report security issues privately through
GitHub's private vulnerability reporting on this
repository (Security → Report a vulnerability) rather
than a public issue.

## Scope notes

preflight-agent is a static, local-only auditor:

- It reads files; its scans never execute, import,
  compile, or install code from a scanned target.
- It makes no network calls and collects no telemetry.
- secret-guard reports key paths and fingerprints
  only; it never prints a secret's value. Its `fix`
  command rewrites only the files it is explicitly
  handed, keeps a timestamped backup of each changed
  file, and re-scans its own rewrite before reporting
  success.
- A CLEAN verdict from any module means "none of the
  problems this module checks were found". It is not a
  guarantee that a setup is safe — each report prints
  its own limits.
