#!/usr/bin/env bash
# Attach to (or create) the "cmip" tmux session.
# Wide/tall terminal: one window, main shell on the left, slurm queue (top)
# and logs shell (bottom) stacked on the right.
# Smaller terminal: separate main, slurm, and logs windows.
# The layout is chosen when the session is created, not on later attaches.
# Run from the repo root on the HPC login node: scripts/tmux-cmip.sh
set -euo pipefail

S=cmip
REPO=$(cd "$(dirname "$0")/.." && pwd)
MIN_COLS=${MIN_COLS:-160}
MIN_LINES=${MIN_LINES:-40}
WATCH="watch -n 30 'squeue -u \$USER'"

if tmux has-session -t "$S" 2>/dev/null; then
  exec tmux attach -t "$S"
fi

cols=$(tput cols)
lines=$(tput lines)

tmux new-session -d -s "$S" -n main -c "$REPO" -x "$cols" -y "$lines"
main=$(tmux display-message -p -t "$S" '#{pane_id}')

if ((cols >= MIN_COLS && lines >= MIN_LINES)); then
  slurm=$(tmux split-window -h -l 40% -t "$main" -c "$REPO" -P -F '#{pane_id}')
  tmux split-window -v -t "$slurm" -c "$REPO"
  tmux send-keys -t "$slurm" "$WATCH" Enter
  tmux select-pane -t "$main"
else
  slurm=$(tmux new-window -t "$S" -n slurm -c "$REPO" -P -F '#{pane_id}')
  tmux send-keys -t "$slurm" "$WATCH" Enter
  tmux new-window -t "$S" -n logs -c "$REPO"
  tmux select-window -t "$S:main"
fi

exec tmux attach -t "$S"
