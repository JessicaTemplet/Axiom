#!/usr/bin/env bash
# Measure independent lexing and parsing with bounded process workers.
# The parent receives summaries, never pointers into a child's arena.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/gate.sh"
gate_init
export AXIOM_PATH="$repo_root/self_host${AXIOM_PATH:+:$AXIOM_PATH}"
"$axiom" --diagnostic-format=ai build \
  --input tests/frontend/parallel/parse.ax --output "$work/frontend-probe" >"$work/build.log" 2>&1 \
  || { cat "$work/build.log" >&2; exit 1; }
python3 scripts/lib/bench-frontend.py --probe "$work/frontend-probe" "$@"
