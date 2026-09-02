---
name: preflight
description: Pre-release audit before deploying the backend to voi.flance.info — tests, env-var delta, protocol/APK compatibility, what ships. Use when the user says deploy, ship, release, update the server, or asks whether it is safe to deploy.
---

# Release pre-flight

Every production deploy is: **preflight → user reviews → user OKs
`./deploy/update.sh` → post-deploy verify**. This skill is the first and last
step; the `deploy` skill documents the middle (and rollback).

## Run it

Delegate to the `release-manager` agent for the full audit. It must come back
with a GO / NO-GO verdict covering:

1. `cd backend && pytest` green (red = stop).
2. Commit list since the deployed state + any uncommitted working-tree files
   (rsync ships the tree, not HEAD — dirty files go out too).
3. New/renamed `config.py` fields since the deployed state, each marked
   legacy-safe or **needs server `.env` edit before deploy**.
4. `schemas.py` protocol changes vs the APK in the field — breaking changes
   require a coordinated APK rebuild (`build-apk` skill).
5. `requirements.txt` / Dockerfile delta (build time, new system deps).

## Present to the user

A short table: what ships, what needs a server-side `.env` edit first, APK
impact, verdict. Then wait — the user runs `./deploy/update.sh` themselves.

## After the user deploys

`release-manager` verifies: `/health` fields, tokenless WS → 403, container
logs clean, one real session observed. Report PASS/FAIL with the rollback
command ready (`git revert <sha>` + user-run redeploy).
