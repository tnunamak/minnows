#!/usr/bin/env bash
# Executable lane → op → evidence_refs → catalog file → SOURCES.json → URL chain.
# Exit nonzero if any link breaks.
set -euo pipefail

LANE="${1:-}"
MINNOWS_ROOT="${MINNOWS_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
CATALOG="$MINNOWS_ROOT/data/model-catalog"
POLICY_DEFAULT="$MINNOWS_ROOT/data/model-choice-policy/operating-points.json"

if [[ -z "$LANE" ]]; then
  echo "usage: demo_lane_trace.sh <lane>" >&2
  exit 2
fi

if ! command -v jq >/dev/null || ! command -v waspflow >/dev/null; then
  echo "need jq and waspflow" >&2
  exit 2
fi

STATUS="$(waspflow status "$LANE" 2>/dev/null)" || {
  echo "lane not found: $LANE" >&2
  exit 1
}

echo "=== 1. Lane ==="
echo "$STATUS" | jq '{
  lane: $l, op, model, effort, policy_version, catalog_ref, policy_file,
  op_expands_to, explicit_overrides, provider, status
}' --arg l "$LANE"

OP="$(echo "$STATUS" | jq -r '.op // empty')"
POLICY_FILE="$(echo "$STATUS" | jq -r '.policy_file // empty')"
CATALOG_REF="$(echo "$STATUS" | jq -r '.catalog_ref // empty')"
[[ -n "$OP" ]] || { echo "lane has no op (raw spawn?)" >&2; exit 1; }

if [[ -z "$POLICY_FILE" || ! -f "$POLICY_FILE" ]]; then
  POLICY_FILE="$POLICY_DEFAULT"
fi
[[ -f "$POLICY_FILE" ]] || { echo "policy file missing: $POLICY_FILE" >&2; exit 1; }

echo
echo "=== 2. Policy op ($OP) ==="
ROW="$(jq -c --arg id "$OP" '.operating_points[] | select(.id==$id)' "$POLICY_FILE")"
[[ -n "$ROW" ]] || { echo "op $OP not in $POLICY_FILE" >&2; exit 1; }
echo "$ROW" | jq '{id, expands_to, evidence_refs, frontier_assumption, known_gaps}'

POLICY_CATALOG="$(jq -r '.catalog_ref // empty' "$POLICY_FILE")"
echo "policy catalog_ref: $POLICY_CATALOG"
if [[ -n "$CATALOG_REF" && -n "$POLICY_CATALOG" && "$CATALOG_REF" != "$POLICY_CATALOG" ]]; then
  echo "WARN: lane catalog_ref ($CATALOG_REF) != policy file catalog_ref ($POLICY_CATALOG)" >&2
fi

echo
echo "=== 3. Evidence refs → catalog files / sources ==="
mapfile -t REFS < <(echo "$ROW" | jq -r '.evidence_refs[]?')
if [[ ${#REFS[@]} -eq 0 ]]; then
  echo "no evidence_refs" >&2
  exit 1
fi

RESOLVED=0
for ref in "${REFS[@]}"; do
  echo "-- $ref"
  if [[ "$ref" == catalog://* ]]; then
    rest="${ref#catalog://}"
    # try performance/pricing/capabilities
    found=""
    for sub in performance pricing capabilities; do
      cand="$CATALOG/$sub/${rest##*/}.json"
      # rest may already include subdir
      cand2="$CATALOG/$rest.json"
      if [[ -f "$cand2" ]]; then found="$cand2"; break; fi
      if [[ -f "$CATALOG/$rest.json" ]]; then found="$CATALOG/$rest.json"; break; fi
      if [[ "$rest" == */* ]]; then
        if [[ -f "$CATALOG/$rest.json" ]]; then found="$CATALOG/$rest.json"; break; fi
      fi
      if [[ -f "$cand" ]]; then found="$cand"; break; fi
    done
    # direct
    if [[ -z "$found" && -f "$CATALOG/${rest}.json" ]]; then found="$CATALOG/${rest}.json"; fi
    if [[ -z "$found" ]]; then
      # path form pricing/foo
      if [[ -f "$CATALOG/${rest}.json" ]]; then found="$CATALOG/${rest}.json"; fi
    fi
    if [[ -z "$found" ]]; then
      echo "  UNRESOLVED catalog path" >&2
      exit 1
    fi
    echo "  file: $found"
    sids="$(jq -r '.source_ids[]? // empty' "$found" 2>/dev/null | head -3)"
    if [[ -n "$sids" ]]; then
      while IFS= read -r sid; do
        [[ -z "$sid" ]] && continue
        url="$(jq -r --arg id "$sid" '.sources[] | select(.id==$id) | .url' "$CATALOG/SOURCES.json")"
        echo "  source_id: $sid"
        echo "  url: $url"
        [[ -n "$url" && "$url" != "null" ]] || { echo "  UNRESOLVED source" >&2; exit 1; }
        RESOLVED=$((RESOLVED + 1))
      done <<<"$sids"
    else
      # row-level source_id sample
      sid="$(jq -r '.scores[0].source_id // .claims[0].source_id // empty' "$found")"
      if [[ -n "$sid" ]]; then
        url="$(jq -r --arg id "$sid" '.sources[] | select(.id==$id) | .url' "$CATALOG/SOURCES.json")"
        echo "  source_id: $sid"
        echo "  url: $url"
        [[ -n "$url" && "$url" != "null" ]] || { echo "  UNRESOLVED source" >&2; exit 1; }
        RESOLVED=$((RESOLVED + 1))
      fi
    fi
  elif [[ "$ref" == source://* ]]; then
    sid="${ref#source://}"
    url="$(jq -r --arg id "$sid" '.sources[] | select(.id==$id) | .url' "$CATALOG/SOURCES.json")"
    echo "  source_id: $sid"
    echo "  url: $url"
    [[ -n "$url" && "$url" != "null" ]] || { echo "  UNRESOLVED source" >&2; exit 1; }
    RESOLVED=$((RESOLVED + 1))
  else
    echo "  bad scheme" >&2
    exit 1
  fi
done

echo
echo "=== OK: resolved $RESOLVED source link(s) for op $OP ==="
exit 0
