#!/usr/bin/env bash
# Refresh the bedrock_dev AWS profile via HumanityRules opsh.
# Updates ~/.aws/credentials on the host; Hermes compose bind-mounts that file.
set -euo pipefail

OPSH_BIN="${OPSH_BIN:-$(command -v opsh || true)}"
OPSH_COMMAND="${OPSH_COMMAND:-bedrock}"
AWS_PROFILE="${AWS_PROFILE:-bedrock_dev}"

if [[ -z "$OPSH_BIN" ]]; then
    echo "opsh not found on PATH; install HumanityRules ops-console or set OPSH_BIN" >&2
    exit 1
fi

"$OPSH_BIN" -c "$OPSH_COMMAND" -q

if ! AWS_PROFILE="$AWS_PROFILE" aws sts get-caller-identity >/dev/null 2>&1; then
    echo "opsh finished but AWS_PROFILE=$AWS_PROFILE is not usable" >&2
    exit 1
fi

identity="$(AWS_PROFILE="$AWS_PROFILE" aws sts get-caller-identity --output text --query Arn)"
echo "bedrock creds refreshed for $AWS_PROFILE ($identity)"
