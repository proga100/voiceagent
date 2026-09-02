#!/usr/bin/env bash
#
# PreToolUse guard for Bash. Blocks commands that would leak secrets, wipe
# farmer data, or ship something the user has not reviewed. Exit code 2 rejects
# the call and returns stderr to the agent.
#
# Matching runs against the command with quoted strings stripped, so a commit
# message that merely mentions "git push" is not itself treated as one.
#
set -uo pipefail

payload=$(cat)

read -r -d '' EXTRACT <<'PY' || true
import json, re, sys
try:
    cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
except Exception:
    cmd = ""
bare = re.sub(r"'[^']*'|\"[^\"]*\"", " ", cmd)
print(bare)
PY

command=$(printf '%s' "$payload" | python3 -c "$EXTRACT" 2>/dev/null)
[ -z "${command// /}" ] && exit 0

deny() {
  echo "BLOCKED: $1" >&2
  exit 2
}

# Force pushes rewrite reviewed history.
case "$command" in
  *"push"*"--force"*|*"push"*" -f "*|*"push"*"--force-with-lease"*)
    deny "Force pushing is not allowed from an agent session. If history must be
rewritten, the human does it deliberately." ;;
esac

# Secrets and runtime data must never be staged. The old repo had a service
# account key and the server password committed once; this one starts clean.
if printf '%s' "$command" | grep -qE 'git (add|commit -a|commit --all)'; then
  if printf '%s' "$command" | grep -qE '(^|[ /])\.env([ /]|$)|gcloud-sa|\.json[ ]*$|data/|\.apk|settings\.local\.json|git add (-A|--all|\.)( |$)'; then
    deny "Refusing to stage secrets or runtime data (.env, gcloud-sa.json, data/,
*.apk, settings.local.json), and refusing blanket 'git add -A / .'. Stage the
specific source files you changed."
  fi
fi

# data/ holds every farmer's memory, chats and photos (bind-mounted in prod).
case "$command" in
  *"rm -rf"*"data"*|*"rm -r"*"data/"*)
    deny "data/ is farmer memory, chats and photos — never delete it from an
agent session. Move it aside by hand if it really has to go." ;;
esac

# The server .env is the only copy of the production secrets.
case "$command" in
  *"ssh"*"flance.info"*"rm"*".env"*|*"ssh"*"flance.info"*"> "*".env"*|*"scp"*".env"*"flance.info"*)
    deny "Never delete, overwrite or upload the server .env. Production keys live
only there; deploy/update.sh excludes it from rsync on purpose." ;;
esac

# Never call the other repos' deploy scripts from here (one of them still
# targets THIS prototype's production directory).
case "$command" in
  *"growz"*"update.sh"*|*"growz_ai"*"deploy"*)
    deny "That deploy script belongs to another repo and its defaults point at
/opt/voiceagent-google — it would overwrite this project's production." ;;
esac

exit 0
