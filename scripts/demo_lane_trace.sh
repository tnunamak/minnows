#!/usr/bin/env bash
# Demonstrate lane → op → policy → catalog_ref chain from a recent --op spawn.
set -euo pipefail
LANE="${1:-}"
if [[ -z "$LANE" ]]; then
  # pick most recent lane with catalog_ref set
  LANE=$(waspflow list 2>/dev/null | awk 'NR>1 {print $1; exit}')
fi
echo "lane: $LANE"
waspflow status "$LANE" 2>/dev/null | jq '{
  lane: $lane,
  op, model, effort, policy_version, catalog_ref, policy_file, provider, status
}' --arg lane "$LANE"
echo "policy file resolves catalog_ref → sources via minnows pack tags"
