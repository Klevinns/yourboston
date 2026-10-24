#!/usr/bin/env bash
# Run the test suite(s) relevant to a saved file.
# Invoked by .kiro/hooks/run-tests-on-save.json (PostFileSave / PostTaskExec).
# Kiro passes hook event JSON on STDIN. The exact field carrying the file path is
# not pinned here: we scan every string in the event for repo paths, so this works
# regardless of field name. With no path found (e.g. PostTaskExec) we run both suites.
set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

EVENT="$(cat 2>/dev/null || true)"
PATHS="$(printf '%s' "$EVENT" | python3 -c '
import json, sys
def walk(o):
    if isinstance(o, dict):
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)
    elif isinstance(o, str):
        yield o
try:
    for s in walk(json.load(sys.stdin)):
        if s.endswith((".py", ".js")): print(s)
except Exception:
    pass
' 2>/dev/null)"

run_py=0; run_js=0
if [ -z "$PATHS" ]; then
  run_py=1; run_js=1
else
  printf '%s\n' "$PATHS" | grep -q 'pipeline/' && run_py=1
  printf '%s\n' "$PATHS" | grep -q 'web/'      && run_js=1
fi

status=0
if [ "$run_py" = 1 ] && [ -f pipeline/pyproject.toml ]; then
  if ! out="$(cd pipeline && uv run pytest -q 2>&1)"; then
    printf 'pipeline tests FAILED:\n%s\n' "$out" | tail -n 60 >&2; status=1
  else
    printf 'pipeline tests passed: %s\n' "$(printf '%s' "$out" | tail -n 1)"
  fi
fi
if [ "$run_js" = 1 ] && [ -d web/tests ]; then
  if ! out="$(node --test web/tests/ 2>&1)"; then
    printf 'web tests FAILED:\n%s\n' "$out" | tail -n 60 >&2; status=1
  else
    printf 'web tests passed\n'
  fi
fi
exit "$status"
