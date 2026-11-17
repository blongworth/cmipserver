#!/usr/bin/env bash
# Attach to (or create) the "cmip" tmux session with work, slurm, and logs windows.
# Run from the repo root on the HPC login node: scripts/tmux-cmip.sh
set -euo pipefail

S=cmip
REPO=$(cd "$(dirname "$0")/.." && pwd)

if tmux has-session -t "$S" 2>/dev/null; then
  exec tmux attach -t "$S"
fi

tmux new-session -d -s "$S" -n work -c "$REPO"
tmux new-window -t "$S" -n slurm "watch -n 30 'squeue -u $USER'"
tmux new-window -t "$S" -n logs -c "$REPO"
tmux select-window -t "$S:work"
exec tmux attach -t "$S"
