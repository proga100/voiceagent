---
name: tdd
description: Test-driven workflow for this repo — red-green-refactor across backend pytest and Flutter tests, wired to the agent team. Use when implementing a feature test-first, fixing a bug (regression test first), or when the user says TDD, "write the test first", or reports a reproducible bug.
---

# TDD loop for the voice agent

Every feature and every bug fix goes red → green → refactor. The Stop hook
(`block-finish-on-red.sh`) refuses to finish a session with a red suite, and
the PostToolUse hook re-runs related tests after each edit — work with them,
not around them.

## The loop

1. **Red — delegate to `tdd-tester`.** It writes the smallest failing test
   and proves it fails for the right reason. For cross-layer features, get a
   plan from `architect` first; the plan's test list is the input here.
2. **Green — smallest change that passes.** Backend work goes to
   `backend-python`, mobile to `flutter-mobile`; the failing test is the spec.
   No behaviour beyond what the test demands.
3. **Refactor — suite green, then clean up.** Re-run the full suite after.
4. **Review — delegate to `code-reviewer`** on the diff before committing;
   run `security-reviewer` too if auth, photos, or external URLs changed.

## Commands

```bash
cd backend && pytest                          # full backend suite, no network
cd backend && pytest app/voice/tests/test_chunker.py -q   # one module
cd mobile && flutter analyze && flutter test  # mobile suite
```

## House rules the tests must obey

- No network — mock the Protocols in `voice/providers/base.py`.
- Backend tests in `backend/app/voice/tests/`; mobile tests mirror `lib/`
  names under `mobile/test/`.
- Audio framing asserted exactly: 3200-byte PCM16 16 kHz in, 24 kHz out.
- Protocol changes test both sides: pydantic event in `schemas.py` and the
  sealed Dart `ServerEvent` class.
- A bug fix without a regression test is not done.
