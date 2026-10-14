#!/usr/bin/env bash
# Tree-sitter colours Axiom the way VS Code does, token by token.
#
# VS Code's colour is the language server's semantic token where the
# server sends one, mapped through the extension's `semanticTokenScopes`,
# and the TextMate grammar's scope everywhere else. Tree-sitter's is what
# its own highlighter answers with `tree-sitter-axiom/queries/`. The
# comparison (`scripts/lib/highlight-parity/compare.mjs`) reduces both to
# one role per character and compares every character of the corpus:
# the standard library, the examples, the language server fixtures and
# the grammar's highlighting regressions.
#
# It holds three things:
#
#   1. Agreement. Every disagreement is a FAIL except the pairs below,
#      each a use of a name that only resolution can colour. The server
#      resolves names across the file and across modules; tree-sitter's
#      highlighter sees one file and only the bindings written above a
#      use. Each allowed pair has a ceiling, the count measured when it
#      was set, so it can only shrink.
#   2. One capture per node. Neovim takes the last of two patterns on a
#      node and Helix the first, so the queries never capture a node twice.
#   3. That the comparison can fail. A copy of the grammar with the
#      operator rule or `in` keyword taken out must disagree outside the
#      allowed pairs, and one with a second catch-all must capture nodes
#      twice.
#
# Needs the compiler (for the server), `node`, the tree-sitter CLI that
# `npm ci --prefix tree-sitter-axiom` installs, and the TextMate engine
# that `npm ci --prefix scripts/lib/highlight-parity --ignore-scripts`
# installs, pinned by the lockfile there.
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/gate.sh"
gate_init
gate_build_axc axc

failed=0
checks=0
ok()   { echo "ok   $*"; checks=$((checks + 1)); }
fail() { echo "FAIL: $*"; failed=$((failed + 1)); }

lib="$repo_root/scripts/lib/highlight-parity"
ts="$repo_root/tree-sitter-axiom/node_modules/.bin/tree-sitter"
command -v node > /dev/null || { echo "FAIL: node is not on PATH"; exit 1; }
[[ -x "$ts" ]] || { echo "FAIL: no tree-sitter CLI at $ts; run npm ci --prefix tree-sitter-axiom"; exit 1; }
[[ -d "$lib/node_modules/vscode-textmate" && -d "$lib/node_modules/vscode-oniguruma" ]] \
  || { echo "FAIL: the TextMate engine is not installed; run npm ci --prefix scripts/lib/highlight-parity --ignore-scripts"; exit 1; }

# The pairs only resolution can settle: tree-sitter's role, VS Code's,
# the ceiling, and why.
allowed=(
  "variable function 1142 a function named as a value, which the server resolves across the file"
  "function macro 477 a call to a macro, which the server resolves to the macro"
  "constructor function 79 a capitalised function named as a value"
  "type.builtin type 25 a library type the file declares again"
  "variable parameter 20 a parameter shadowed in a binding's own value"
  "parameter variable 24 a parameter's use inside a macro template, where the server resolves no names"
  "function variable 1 a syntax/for binder in an expression, which the grammar reads as a call"
)

cd "$repo_root"
LC_ALL=C find stdlib examples -name '*.ax' | LC_ALL=C sort > "$work/corpus"
LC_ALL=C ls tests/lsp/*.ax >> "$work/corpus"
LC_ALL=C find tree-sitter-axiom/test/highlight -name '*.ax' | LC_ALL=C sort >> "$work/corpus"
files=$(wc -l < "$work/corpus" | tr -d ' ')

# Tree-sitter's highlighter names a capture only if the theme does, so
# the theme names every capture the queries use.
grep -oh '@[a-z.]*' tree-sitter-axiom/queries/highlights.scm | sort -u | tr -d @ \
  | node -e 'const names = require("fs").readFileSync(0, "utf8").trim().split("\n");
             console.log(JSON.stringify({ "parser-directories": [], theme: Object.fromEntries(names.map((n) => [n, "#000000"])) }));' \
  > "$work/tsconfig.json"

# The extension's queries as the highlighter loads them: with
# `queries/locals.scm`, from the grammar's own directory.
compare() {
  local grammar="$1" out="$2"; shift 2
  node "$lib/compare.mjs" "$repo_root" "$grammar" "$work/tsconfig.json" "$work/sem.json" "$@" > "$out" 2> "$out.err"
}

echo "== the language server's semantic tokens over $files files =="
if ! AXIOM_STDLIB="$repo_root/stdlib" python3 "$lib/semantic-tokens.py" "$axc" "$work/sem.json" $(cat "$work/corpus") > "$work/sem.log" 2>&1; then
  fail "the server did not answer semanticTokens/full for the corpus"; tail -5 "$work/sem.log"
  echo "check-highlight-parity: $failed failed"; exit 1
fi
cat "$work/sem.log"

echo "== tree-sitter against VS Code =="
compare tree-sitter-axiom "$work/report" $(cat "$work/corpus") || { fail "the comparison did not run"; cat "$work/report.err"; }
runs=0; agree=0
read -r _ runs _ agree < <(grep '^RUNS ' "$work/report") || true
runs="${runs:-0}"; agree="${agree:-0}"
if (( runs >= 300000 )); then ok "$runs runs of like-coloured characters compared (floor 300000)"
else fail "only $runs runs compared; the floor is 300000, so the corpus or the comparison shrank"; fi
echo "     $agree agree ($(( runs > 0 ? agree * 10000 / runs : 0 )) per 10000)"

overlap="$(sed -n 's/^OVERLAP //p' "$work/report")"
overlap="${overlap:-unknown}"
if [[ "$overlap" == 0 ]]; then ok "no node is captured twice"
else fail "$overlap nodes are captured twice"; grep '^OVERLAP_AT' "$work/report" | head -5; fi

while read -r _ tsr vsr n ex; do
  hit=""
  for a in "${allowed[@]}"; do
    read -r ats avs ceil why <<< "$a"
    if [[ "$tsr" == "$ats" && "$vsr" == "$avs" ]]; then
      hit=1
      if (( n <= ceil )); then ok "$n x tree-sitter $tsr where VS Code says $vsr: $why (ceiling $ceil)"
      else fail "$n x tree-sitter $tsr where VS Code says $vsr, over the ceiling $ceil; e.g. $ex"; fi
    fi
  done
  [[ -n "$hit" ]] || fail "$n x tree-sitter $tsr where VS Code says $vsr; e.g. $ex"
done < <(grep '^PAIR ' "$work/report")

echo "== the comparison can fail =="
sample="stdlib/Vec.ax stdlib/Err.ax tests/lsp/060-outline.ax tree-sitter-axiom/test/highlight/loops.ax"
for kind in operator keyword overlap; do
  copy="$work/grammar-$kind"
  mkdir -p "$copy"
  cp -R tree-sitter-axiom/grammar.js tree-sitter-axiom/tree-sitter.json tree-sitter-axiom/package.json tree-sitter-axiom/src tree-sitter-axiom/queries "$copy/"
  if [[ "$kind" == operator ]]; then
    python3 - "$copy/queries/highlights.scm" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
old = '((identifier) @operator\n  (#match? @operator "^([-+*/%<>=!&|^~]+|[.][.][.])$"))'
assert s.count(old) == 1, "the operator rule moved; update this ablation"
open(p, "w").write(s.replace(old, "", 1))
PY
  elif [[ "$kind" == keyword ]]; then
    python3 - "$copy/queries/highlights.scm" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
old = '  "in"\n'
assert s.count(old) == 1, "the in keyword moved; update this ablation"
open(p, "w").write(s.replace(old, "", 1))
PY
  else
    printf '\n(identifier) @variable\n' >> "$copy/queries/highlights.scm"
  fi
  compare "$copy" "$work/ablate-$kind" $sample
  if [[ "$kind" == operator ]]; then
    if grep -q '^PAIR [a-z.]* operator ' "$work/ablate-$kind"; then ok "without the operator rule, operators disagree"
    else fail "a grammar without its operator rule agreed with VS Code, so the comparison cannot see a dropped rule"; fi
  elif [[ "$kind" == keyword ]]; then
    if grep -q '^PAIR plain keyword ' "$work/ablate-$kind"; then ok "without the in capture, loop keywords disagree"
    else fail "a grammar without its in capture agreed with VS Code, so the comparison cannot see an uncoloured loop keyword"; fi
  else
    if grep -q '^OVERLAP [1-9]' "$work/ablate-$kind"; then ok "a second catch-all is caught capturing nodes twice"
    else fail "a second catch-all went unnoticed, so the overlap check cannot fail"; fi
  fi
done

if (( failed == 0 )); then echo "check-highlight-parity: $checks checks passed"
else echo "check-highlight-parity: $failed failed"; exit 1; fi
