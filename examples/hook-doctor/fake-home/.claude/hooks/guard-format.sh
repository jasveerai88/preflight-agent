#!/bin/sh
# Fictional example hook for hook-doctor's fixtures.
# Pretend guard: it wants to block badly formatted code —
# but the fixtures wire it to PostToolUse, an event that
# fires after the tool call already ran and cannot block
# anything. hook-doctor flags that as EVENT_NOT_BLOCKING.
echo "guard-format: checked" >&2
exit 0
