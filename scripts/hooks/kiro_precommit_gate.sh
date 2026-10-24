#!/usr/bin/env bash
# Kiro PreToolUse gate for shell tools. Kiro has no "pre-commit" trigger, so this
# hook fires before every shell tool call, inspects the tool input from the STDIN
# event JSON, and only acts when the command contains `git commit`.
# Exit 2 = block the tool call and return stderr to the agent (hooks/types.md, PreToolUse).
set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

EVENT="$(cat 2>/dev/null || true)"
IS_COMMIT="$(printf '%s' "$EVENT" | python3 -c '
import json, re, sys
def walk(o):
    if isinstance(o, dict):
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)
    elif isinstance(o, str):
        yield o
try:
    ev = json.load(sys.stdin)
    src = ev.get("tool_input", ev)
    print("1" if any(re.search(r"\bgit\b[^\n;&|]*\bcommit\b", s) for s in walk(src)) else "0")
except Exception:
    print("0")
' 2>/dev/null)"

[ "$IS_COMMIT" = "1" ] || exit 0

if ! bash scripts/hooks/check_kiro_committed.sh; then
  echo "Commit blocked by .kiro/hooks/pre-commit-guard.json: fix the issues above, then retry." >&2
  exit 2
fi
exit 0
