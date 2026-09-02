#!/usr/bin/env bash
#
# Stop hook. Refuses to end a session that leaves the suite red, so an agent
# cannot declare a task done while its own changes are failing.
#
# Only the side that actually changed pays for a run: backend/ -> pytest,
# mobile/ -> flutter analyze + flutter test. Docs and config work run nothing.
#
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

red() {
  echo "$1" >&2
  printf '%s\n' "$2" | tail -25 >&2
  echo "
Finish the work or explain the failure to the user before stopping." >&2
  exit 2
}

if [ -n "$(git status --porcelain -- backend 2>/dev/null)" ]; then
  py="backend/.venv/bin/python"; [ -x "$py" ] || py="python3"
  if "$py" -c 'import pytest' 2>/dev/null; then
    output=$(cd backend && "../$py" -m pytest -q 2>&1) \
      || red "Backend tests are red and backend/ files were changed:" "$output"
  else
    # Can't verify without pytest; say so rather than passing silently.
    echo "Cannot verify backend tests: pytest not installed (create backend/.venv)." >&2
  fi
fi

if [ -n "$(git status --porcelain -- mobile/lib mobile/test 2>/dev/null)" ] && command -v flutter >/dev/null 2>&1; then
  output=$(cd mobile && flutter analyze 2>&1) \
    || red "flutter analyze reports problems and mobile/ files were changed:" "$output"
  output=$(cd mobile && flutter test 2>&1) \
    || red "Flutter tests are red and mobile/ files were changed:" "$output"
fi

exit 0
