#!/usr/bin/env bash
#
# Tear down all active AgentFactor sessions and associated terminals.

set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: uv CLI is required (https://docs.astral.sh/uv/)." >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "Error: jq is required to parse session JSON." >&2
  exit 1
fi

kill_tmux="${AC_KILL_TMUX:-true}"

sessions_json="$(uv run agentfactor sessions)"
session_names=($(echo "${sessions_json}" | jq -r '.[].name'))

if [[ ${#session_names[@]} -eq 0 ]]; then
  echo "No active sessions."
  exit 0
fi

echo "Found ${#session_names[@]} active session(s): ${session_names[*]}"

for session in "${session_names[@]}"; do
  echo "Closing terminals for session '${session}'..."
  terminal_ids=($(echo "${sessions_json}" | jq -r ".[] | select(.name == \"${session}\") | .terminals[].id"))
  for terminal_id in "${terminal_ids[@]}"; do
    echo "  Closing terminal ${terminal_id}"
    if ! uv run agentfactor close "${terminal_id}"; then
      echo "    Warning: Failed to close ${terminal_id}" >&2
    fi
  done

  if [[ "${kill_tmux}" == "true" ]] && command -v tmux >/dev/null 2>&1; then
    if tmux has-session -t "${session}" 2>/dev/null; then
      echo "  Killing tmux session '${session}'"
      tmux kill-session -t "${session}"
    fi
  fi
done

echo "Verifying cleanup..."
uv run agentfactor sessions
