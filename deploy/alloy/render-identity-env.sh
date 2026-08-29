#!/usr/bin/env bash
# Renders SCREENKEEPER_PLAYER_ID / _LOCATION_ID / _ORGANIZATION_ID into an env
# file Alloy loads via `EnvironmentFile=-/etc/alloy/screenkeeper-identity.env`
# (the leading `-` makes a missing file non-fatal to Alloy).
#
# Read-only: never writes to device.json, never touches the device token.
# Intended to be invoked by the *Alloy* systemd unit (ExecStartPre=, or a
# timer that restarts Alloy after enrollment), not by any Screenkeeper unit
# -- Screenkeeper must never depend on Alloy, and Alloy's own startup must
# never block on this host being enrolled. See deploy/alloy/README.md.
#
# Usage: render-identity-env.sh [device.json path] [output env file path]
set -euo pipefail

DEVICE_JSON="${1:-/var/lib/signage-controller/device.json}"
OUTPUT_ENV="${2:-/etc/alloy/screenkeeper-identity.env}"

if ! command -v jq >/dev/null 2>&1; then
	echo "render-identity-env.sh: jq is required" >&2
	exit 1
fi

if [ ! -r "$DEVICE_JSON" ]; then
	# Not enrolled yet, or this host doesn't run the edge player at all.
	# Writing nothing leaves Alloy's identity labels empty, not broken.
	echo "render-identity-env.sh: $DEVICE_JSON not readable; leaving $OUTPUT_ENV unchanged" >&2
	exit 0
fi

player_id=$(jq -r '.player_id // empty' "$DEVICE_JSON")
location_id=$(jq -r '.location_id // empty' "$DEVICE_JSON")
organization_id=$(jq -r '.organization_id // empty' "$DEVICE_JSON")

umask 077
{
	printf 'SCREENKEEPER_PLAYER_ID=%s\n' "$player_id"
	printf 'SCREENKEEPER_LOCATION_ID=%s\n' "$location_id"
	printf 'SCREENKEEPER_ORGANIZATION_ID=%s\n' "$organization_id"
} > "$OUTPUT_ENV"

echo "render-identity-env.sh: wrote $OUTPUT_ENV"
