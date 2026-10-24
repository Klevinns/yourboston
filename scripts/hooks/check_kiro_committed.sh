#!/usr/bin/env bash
# Pre-commit guard (Hackachusetts DQ rules + secret hygiene).
#  1. .kiro/ exists at repo root and is NOT gitignored.
#  2. .kiro/ (and any staged file) contains no secrets.
# Exit 0 = OK, 1 = violation (stderr explains).
# Used by: .kiro/hooks/pre-commit-guard.json (via kiro_precommit_gate.sh) and
#          scripts/git-hooks/pre-commit (native git hook, core.hooksPath).
set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fail=0

if [ ! -d .kiro ]; then
  echo "pre-commit: .kiro/ folder missing at repo root (required for submission)" >&2; fail=1
else
  for p in .kiro .kiro/steering/product.md .kiro/specs/fitmap/tasks.md .kiro/hooks/run-tests-on-save.json; do
    if git check-ignore -q --no-index "$p" 2>/dev/null; then
      echo "pre-commit: '$p' is gitignored - .kiro must be committed (DQ risk). Fix .gitignore." >&2; fail=1
    fi
  done
fi

# Files to scan: everything under .kiro plus staged files (added/copied/modified).
FILES="$( { find .kiro -type f 2>/dev/null; git diff --cached --name-only --diff-filter=ACM 2>/dev/null; } | sort -u )"

# Generic secret patterns (AWS keys, GitHub/Slack tokens, private keys, key=value assignments).
PATTERN='(AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(api[_-]?key|secret|token|password)["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"'][A-Za-z0-9/+_-]{16,}["'"'"'])'

while IFS= read -r f; do
  [ -f "$f" ] || continue
  case "$f" in scripts/hooks/check_kiro_committed.sh) continue;; esac
  if grep -IEiq -- "$PATTERN" "$f" 2>/dev/null; then
    echo "pre-commit: possible secret in $f:" >&2
    grep -IEin -- "$PATTERN" "$f" | head -n 3 | sed 's/\(.\{12\}\).*/\1…[redacted]/' >&2
    fail=1
  fi
  # Exact match on the real Census key if it is set in the environment (never printed).
  if [ -n "${CENSUS_API_KEY:-}" ] && grep -Fq -- "$CENSUS_API_KEY" "$f" 2>/dev/null; then
    echo "pre-commit: CENSUS_API_KEY value found in $f" >&2; fail=1
  fi
done <<< "$FILES"

[ "$fail" = 0 ] && echo "pre-commit: .kiro committed-not-ignored and secret scan OK"
exit "$fail"
