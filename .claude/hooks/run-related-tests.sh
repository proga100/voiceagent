#!/usr/bin/env bash
#
# PostToolUse hook for Edit/Write. After a backend Python file or a Dart file
# changes, run the test that covers it and hand any failure straight back.
#
# Backend:  backend/app/**/foo.py      -> backend/app/voice/tests/test_foo.py
#           backend/app/voice/tests/*  -> that file
# Mobile:   mobile/lib/**/foo.dart     -> mobile/test/foo_test.dart
#           mobile/test/*_test.dart    -> that file
#
# No matching test is not a failure — plenty of files have no direct cover.
#
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 0

payload=$(cat)
file=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)
[ -z "$file" ] && exit 0

fail() {
  echo "Tests for $(basename "$file") are failing after your edit:" >&2
  printf '%s\n' "$1" | tail -30 >&2
  echo "
Fix the code or the test before moving on. Do not edit the assertion to match
broken behaviour." >&2
  exit 2
}

case "$file" in
  *"/backend/app/"*.py)
    base=$(basename "$file" .py)
    case "$file" in
      *"/voice/tests/"*) test="backend/app/voice/tests/${base}.py" ;;
      *)                 test="backend/app/voice/tests/test_${base}.py" ;;
    esac
    [ -f "$test" ] || exit 0
    py="backend/.venv/bin/python"; [ -x "$py" ] || py="python3"
    # No pytest (fresh clone, no venv yet) is not a red suite — say so and move on.
    "$py" -c 'import pytest' 2>/dev/null || { echo "pytest not installed for $py — create backend/.venv to enable auto-tests" >&2; exit 0; }
    output=$(cd backend && "../$py" -m pytest "${test#backend/}" -q 2>&1) || fail "$output"
    ;;

  *"/mobile/lib/"*.dart|*"/mobile/test/"*.dart)
    command -v flutter >/dev/null 2>&1 || exit 0
    base=$(basename "$file" .dart)
    case "$file" in
      *"/mobile/test/"*) test="mobile/test/${base}.dart" ;;
      *)                 test="mobile/test/${base}_test.dart" ;;
    esac
    [ -f "$test" ] || exit 0
    output=$(cd mobile && flutter test "${test#mobile/}" 2>&1) || fail "$output"
    ;;
esac

exit 0
