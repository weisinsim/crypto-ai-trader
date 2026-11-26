#!/usr/bin/env bash
# Run only in a trusted isolated cloud shell, not production.
set +x
set -euo pipefail
cd "$(dirname "$0")/.."
unset PIONEX_READ_API_KEY PIONEX_READ_API_SECRET PIONEX_READ_ONLY_ENABLED PIONEX_LIVE_PROBE_APPROVED
trap 'unset PIONEX_READ_API_KEY PIONEX_READ_API_SECRET PIONEX_READ_ONLY_ENABLED PIONEX_LIVE_PROBE_APPROVED' EXIT
python -c 'import httpx' >/dev/null
printf 'Confirm isolated GET-only positions probe (type YES): '
read -r approval
[[ "$approval" == YES ]] || exit 2
read -rs -p 'Read-only API key: ' PIONEX_READ_API_KEY
printf '\n'
read -rs -p 'Read-only API secret: ' PIONEX_READ_API_SECRET
printf '\n'
[[ -n "$PIONEX_READ_API_KEY" && -n "$PIONEX_READ_API_SECRET" ]] || exit 2
export PIONEX_READ_API_KEY PIONEX_READ_API_SECRET
export PIONEX_READ_ONLY_ENABLED=true PIONEX_LIVE_PROBE_APPROVED=true
python -m notifications.pionex_connection_probe
