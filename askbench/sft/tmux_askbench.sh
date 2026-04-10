#!/bin/bash
set -euo pipefail

SESSION_NAME="${1:-askbench}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux is not installed on this machine."
    exit 1
fi

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    exec tmux attach -t "$SESSION_NAME"
fi

tmux new-session -d -s "$SESSION_NAME" -n shell -c "$SCRIPT_DIR"
tmux set-option -t "$SESSION_NAME" mouse on
tmux set-option -t "$SESSION_NAME" history-limit 50000
tmux set-window-option -t "$SESSION_NAME" remain-on-exit on

tmux new-window -t "$SESSION_NAME" -n monitor -c "$SCRIPT_DIR"
tmux send-keys -t "$SESSION_NAME:monitor" "cd \"$SCRIPT_DIR\"" C-m
tmux send-keys -t "$SESSION_NAME:monitor" "echo 'monitor window: use squeue, scontrol, tail -f logs here'" C-m

tmux new-window -t "$SESSION_NAME" -n notes -c "$SCRIPT_DIR"
tmux send-keys -t "$SESSION_NAME:notes" "cd \"$SCRIPT_DIR\"" C-m
tmux send-keys -t "$SESSION_NAME:notes" "printf '%s\n' 'detach: Ctrl+b d' 'list sessions: tmux ls' 'reattach: tmux attach -t $SESSION_NAME' 'kill session: tmux kill-session -t $SESSION_NAME'" C-m

tmux select-window -t "$SESSION_NAME:shell"
exec tmux attach -t "$SESSION_NAME"
