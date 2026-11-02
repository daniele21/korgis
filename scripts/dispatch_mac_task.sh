#!/usr/bin/env bash
# Manual source-owned Mac task. No infra commits or hosted CI dispatcher.
set -euo pipefail
usage() { echo "Usage: bash scripts/dispatch_mac_task.sh TASK_ID [SOURCE_SHA] [--dry-run]" >&2; exit 2; }
[[ $# -ge 1 && $# -le 3 ]] || usage
TASK_ID="$1"; shift
SHA=""
DRY_RUN=false
for arg in "$@"; do
  if [[ "$arg" == "--dry-run" ]]; then DRY_RUN=true
  elif [[ -z "$SHA" ]]; then SHA="$arg"
  else usage
  fi
done
ROOT=$(git rev-parse --show-toplevel)
[[ -n "$SHA" ]] || SHA=$(git -C "$ROOT" rev-parse HEAD)
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "Full SHA required" >&2; exit 2; }
[[ "$TASK_ID" =~ ^[a-z][a-z0-9-]{0,47}$ ]] || { echo "Invalid task ID" >&2; exit 2; }
repo="daniele21/korgis"
origin=$(git -C "$ROOT" remote get-url origin)
case "$origin" in
  *github.com[:/]daniele21/korgis|*github.com[:/]daniele21/korgis.git) ;;
  *) echo "Unexpected source origin" >&2; exit 2 ;;
esac
script=$(jq -er --arg task "$TASK_ID" '
  select(.schema == "source-owned-mac-tasks/v1")
  | .tasks[$task] | select(.cost_class == "free" and .manual_only == true)
  | .script | select(type == "string")
' "$ROOT/.github/ci/mac-tasks.json") || {
  echo "Task missing or not free/manual" >&2; exit 2;
}
[[ "$script" =~ ^\.github/ci/(local-test\.sh|tasks/[a-z0-9-]+\.sh)$ ]] || {
  echo "Unsafe script path" >&2; exit 2;
}
echo "Source: $repo@$SHA"
echo "Task:   $TASK_ID -> $script"
echo "Target: daniele21/local-runner-infra, event=source-owned-task-v1"
if $DRY_RUN; then
  echo "DRY RUN: no GitHub API call or remote job."
  exit 0
fi
git -C "$ROOT" show-ref --verify --quiet refs/remotes/origin/main || {
  echo "Fetch origin/main before dispatch" >&2; exit 2;
}
git -C "$ROOT" merge-base --is-ancestor "$SHA" refs/remotes/origin/main || {
  echo "Only reviewed main commits are allowed" >&2; exit 2;
}
gh auth status >/dev/null || { echo "GitHub CLI login required" >&2; exit 2; }
gh api --method POST repos/daniele21/local-runner-infra/dispatches \
  -f event_type=source-owned-task-v1 \
  -f "client_payload[repository]=$repo" \
  -f "client_payload[sha]=$SHA" \
  -f "client_payload[task_id]=$TASK_ID" >/dev/null
echo "Dispatch accepted. Check central Mac job and source SHA status local-ci/source-owned/$TASK_ID."
echo "Accepted != passed."
