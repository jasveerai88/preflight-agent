#!/usr/bin/env python3
"""Build the preflight-agent demo fixture: one fake
agent-setup folder with exactly one planted problem per
module, so `preflight-agent scan` (and each module command)
has something real to find.

    python3 examples/make_fixture.py TARGET_DIR

Planted problems, one per module:

  clone-guard   .envrc — direnv runs it the moment the
                folder is entered (AUTO-RUN); the
                SessionStart hook in .claude/settings.json
                is a second open-time surface.
  secret-guard  .mcp.json holds a literal service key in
                its env block (ENV-LITERAL).
  hook-doctor   .claude/settings.json points a PreToolUse
                hook at $HOME/.claude/hooks/guard-rm.sh,
                which does not exist in the fixture — a
                guard that can never run.
  fleet-audit   .claude/agents holds the name "reviewer"
                twice (reviewer.md and extra/reviewer.md)
                — a duplicate-name collision.
  skill-sprawl  .claude/skills holds the skill name
                "notes-writer" in two scopes
                (notes-writer/ and archive/notes-writer/)
                — a cross-scope duplicate.

Everything else in the fixture is deliberately clean, so
each module's findings are attributable to its plant.
All secrets are fake.
"""

import json
import sys
from pathlib import Path

SETTINGS = {
    "hooks": {
        "SessionStart": [
            {"hooks": [{"type": "command",
                        "command": "echo session started",
                        "timeout": 5}]}
        ],
        "PreToolUse": [
            {"matcher": "Bash",
             "hooks": [{"type": "command",
                        "command": 'python3 '
                                   '"$HOME/.claude/hooks/'
                                   'guard-rm.sh"',
                        "timeout": 10}]}
        ],
    }
}

MCP = {
    "mcpServers": {
        "notes": {
            "command": "node",
            "args": ["server.js"],
            "env": {"SERVICE_KEY": "Q7f9Kd2Lm8Zx4Vb6N1pRs3Tg"},
        }
    }
}

AGENT = """---
name: reviewer
description: Reviews code changes for bugs and style problems before merge. Use when a diff needs a careful read-only review.
tools: Read, Grep, Glob
---

You review diffs and report problems. You do not edit code.
"""

SKILL = """---
name: notes-writer
description: Writes meeting notes into decisions and action items. Use when a meeting ends and notes need writing up.
---

You write up meeting notes: decisions first, then action
items with owners.
"""


def build(root):
    root = Path(root)
    claude = root / ".claude"
    (claude / "agents" / "extra").mkdir(parents=True,
                                        exist_ok=True)
    (claude / "skills" / "notes-writer").mkdir(parents=True,
                                               exist_ok=True)
    (claude / "skills" / "archive" / "notes-writer").mkdir(
        parents=True, exist_ok=True)
    (claude / "settings.json").write_text(
        json.dumps(SETTINGS, indent=2) + "\n")
    (claude / "agents" / "reviewer.md").write_text(AGENT)
    (claude / "agents" / "extra" / "reviewer.md").write_text(
        AGENT)
    (claude / "skills" / "notes-writer"
     / "SKILL.md").write_text(SKILL)
    (claude / "skills" / "archive" / "notes-writer"
     / "SKILL.md").write_text(SKILL)
    (root / ".mcp.json").write_text(
        json.dumps(MCP, indent=2) + "\n")
    (root / ".envrc").write_text("export PROJECT_ENV=dev\n")
    return root


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: make_fixture.py TARGET_DIR")
        sys.exit(2)
    target = build(sys.argv[1])
    print(f"fixture built at {target}")
