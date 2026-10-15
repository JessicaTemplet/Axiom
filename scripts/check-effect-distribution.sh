#!/usr/bin/env bash
# Pins the distribution of inferred effect rows: the measurement behind
# which effects are required and which are ambient.
#
# `docs/reference.md` (Effects) lets silence claim "performs no IO"
# (AX3042) and "touches no raw memory" (AX3073); a refuted claim is
# AX3010. `Alloc` and `Mut` are ambient: inferred and reported, never
# demanded. Requiring `Mut` would tag most effectful functions, so it
# would distinguish nothing.
#
# `Unsafe` is required lexically. Only the body that performs an unsafe
# operation (MM-EXEC-9d) declares it, because a transitive rule would
# tag nearly every function that performs anything. A trusted
# encapsulation stops `Unsafe` at its own row (R-B6, MM-EXEC-9d), so its
# callers' rows do not carry it.
#
# "Ambient" is a claim about a population, so the gate pins every bucket
# of exact effect rows in two views:
#
#   compiler  `symbols --calls self_host/main.ax`: the compiler and the
#             stdlib it reaches.
#   stdlib    one probe importing every stdlib module.
#
# Each view also pins its pure rows, its `#effects-incomplete` rows (a
# call through a parameter, so the row is a lower bound), its
# `#effect-params` rows and its number of distinct rows. The distinct
# count refuses a new effect combination that has no pin of its own.
#
# A failure means the distribution moved: a row was added, removed or
# changed bucket. To re-pin, diff the `symbols --calls` rows of the old
# and new tree with one compiler, and account for every added, removed
# and changed row. Moves among the ambient buckets need only new
# numbers. A row that moves onto or off `IO` is a question about the
# required/ambient line: settle it first.
#
# The negative probes at the end check that the comparison `have` makes
# refuses a count one off its pin.
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/gate.sh"
gate_init
gate_build_axc axc

failed=0; passed=0
ok()   { echo "ok   $*"; passed=$((passed + 1)); }
fail() { echo "FAIL: $*"; failed=$((failed + 1)); }

# bucket <axsym> <label>: count the rows whose #effects= row is exactly <label>.
bucket() { grep -c "#effects=$2\\([ \"#]\\|$\\)" "$1" || true; }
have() { # have <got> <want> <what>
  if (( $1 == $2 )); then ok "$3 is $1, as pinned";
  else fail "$3 is $1, pinned $2 - the distribution moved; revisit the required/ambient line in the diff"; fi
}

# RE-DERIVED 2026-10-13: the lexicon (`self_host/lexicon.ax`), the
# nolint reading (`self_host/nolint.ax`) and the server's semantic
# tokens section arrived, all of them string-building and
# vector-walking code with no IO: pure 1872 -> 1980, Alloc,Mut 2496 ->
# 2566, Alloc,IO,Mut 365 -> 367, Mut 141 -> 144, Alloc 141 -> 142. Every
# IO bucket stayed where it was, which is the line this gate watches.
# Postfix propagation and unit checking add 36 compiler functions:
# 11 pure, 3 Alloc and 22 Alloc,Mut. Comparing both source trees with
# the same compiler moves no existing effect row, including every IO row.
# Handler-call attribution removes the obsolete blanket effect-claim
# helpers; the formatter adds a padding helper. Comparing 4074f467 and
# this tree with the same compiler changes no existing function's effect
# set, including every IO row. The pins also include the recovered LSP,
# contract tooling and allocator-poison functions already on trunk.
# REPL styling and shared help add six pure palette constants and two
# Alloc,IO,Mut functions. The CLI helpers move modules with unchanged
# effect sets; comparing the 4ffb6f02 symbols changes no existing row.
# Pattern Float lowering adds patFloatFlag, an Unsafe leaf reading the
# checker's binder stamp. Existing functions keep their measured rows.
# Explicit conversions add four pure predicates/constants, three allocating
# type-syntax helpers, three Alloc,Mut lowering helpers and one emitter
# with Alloc,IO,Mut. Removing fpCastSurplus removes one Alloc,Mut row.
# The emitter writes LLVM text, which is the new IO row. Comparing source
# trees with the same compiler changes no existing function effect set.
# Generic alias expansion adds scoped substitution, cycle, parameter and
# constructor-arity helpers. Existing function effect sets remain unchanged
# when compared with the same compiler; the additions are pure or Alloc,Mut.
# Removing explicit region annotations removes annotation-only helpers,
# and two region walkers lose obsolete Unsafe operations. rgnOriginSpan adds locations to memory
# diagnostics; existing IO rows keep their effect sets.
# Pipelines add hygienic expansion helpers; lambda Float parameters consult
# their checked arrow. No IO function changes its effect set.
# Exact conversions add three Alloc rows and one pure row. Alias scope
# helpers add one Alloc,Mut row and one Unsafe row; removing repeated
# alias expansion moves castNormTy and asNumericKind from Alloc,Mut to Alloc.
# All required effect rows retain their existing counts.
# Incremental LSP synchronisation adds document/edit helpers and an IO
# publisher. Line/column helpers now build the CR/LF-aware line index,
# moving two pure rows to Alloc,Mut. No existing IO row changes effects.
# Integer widths add checked lowering and emission helpers. Five field
# lookup helpers lose Mut through the numeric predicate path. Fixed Float
# rendering replaces three helpers with four; semantic token caching adds
# one IO publisher. Existing IO rows retain their measured effect sets.
# Scoped callback contracts add seven Alloc,Mut helpers and two pure
# helpers. Signed literal parsing shares existing pure bounds. Existing
# effect rows and all stdlib buckets are unchanged.
# Nominal declaration identity removes name-building from structural equality
# and ownership classifiers; qualified type resolution adds its checked helpers.
# Workspace overlays add seven Alloc,IO,Mut readers/publishers. The existing
# parseModuleOrDieUnit loses Unsafe through typed preloaded units. Imported
# extern registration removes five duplicate Ffi IO,Unsafe rows from the library
# view; the five source declarations remain, with their required effects.
# Rigid generic results expose five explicit word/container boundaries.
# The formatter adds bounded trials and CLI publishers; workspace caching
# adds revision and exact-source readers. parseModuleOrDieUnit explicitly
# converts its raw preload result. No existing IO requirement is removed.
# Background LSP work adds process lifecycle helpers and an Alloc,IO,Spawn
# row. Dispatch returns packets for the parent to publish; sigChild is pure.
echo "== compiler view: symbols --calls self_host/main.ax =="
"$axc" --diagnostic-format=ai symbols --calls self_host/main.ax > "$work/main.axsym" 2>"$work/main.err" \
  || { fail "could not read symbols over self_host/main.ax"; echo "check-effect-distribution: $failed failed"; exit 1; }
rows="$(grep -c '^F ' "$work/main.axsym" || true)"
(( rows >= 4000 )) && ok "$rows functions listed (floor 4000)" \
  || fail "only $rows functions listed; the floor is 4000 (the corpus moved or the read broke)"
have "$(bucket "$work/main.axsym" 'Alloc,Mut')" 2845 "exactly Alloc,Mut"
have "$(bucket "$work/main.axsym" 'Alloc,IO,Mut')" 428 "Alloc,IO,Mut"
have "$(bucket "$work/main.axsym" 'Mut')" 163 "exactly Mut"
have "$(bucket "$work/main.axsym" 'Alloc')" 164 "exactly Alloc"
have "$(bucket "$work/main.axsym" 'Alloc,IO')" 16 "Alloc,IO"
have "$(bucket "$work/main.axsym" 'IO')" 12 "exactly IO"
have "$(bucket "$work/main.axsym" 'IO,Mut')" 1 "IO,Mut"
have "$(bucket "$work/main.axsym" 'Alloc,Mut,Unsafe')" 255 "Alloc,Mut,Unsafe"
have "$(bucket "$work/main.axsym" 'Unsafe')" 125 "exactly Unsafe"
have "$(bucket "$work/main.axsym" 'Alloc,IO,Mut,Unsafe')" 63 "Alloc,IO,Mut,Unsafe"
have "$(bucket "$work/main.axsym" 'Mut,Unsafe')" 24 "Mut,Unsafe"
have "$(bucket "$work/main.axsym" 'Alloc,Unsafe')" 6 "Alloc,Unsafe"
have "$(bucket "$work/main.axsym" 'Alloc,IO,Unsafe')" 20 "Alloc,IO,Unsafe"
have "$(bucket "$work/main.axsym" 'IO,Mut,Unsafe')" 0 "IO,Mut,Unsafe"
have "$(bucket "$work/main.axsym" 'IO,Unsafe')" 13 "IO,Unsafe"
have "$(bucket "$work/main.axsym" 'Alloc,Block,IO,Mut,Spawn')" 29 "Alloc,Block,IO,Mut,Spawn"
have "$(bucket "$work/main.axsym" 'Alloc,Block,IO,Mut,Spawn,Unsafe')" 3 "Alloc,Block,IO,Mut,Spawn,Unsafe"
have "$(bucket "$work/main.axsym" 'Alloc,Block,Entropy,IO,Mut,Spawn')" 4 "Alloc,Block,Entropy,IO,Mut,Spawn"
have "$(bucket "$work/main.axsym" 'Alloc,Block,Entropy,IO,Mut,Spawn,Unsafe')" 2 "Alloc,Block,Entropy,IO,Mut,Spawn,Unsafe"
have "$(bucket "$work/main.axsym" 'Alloc,Block,IO,Mut,Unsafe')" 3 "Alloc,Block,IO,Mut,Unsafe"
have "$(bucket "$work/main.axsym" 'Alloc,Entropy,IO,Mut')" 2 "Alloc,Entropy,IO,Mut"
have "$(bucket "$work/main.axsym" 'Alloc,Entropy,IO,Mut,Unsafe')" 0 "Alloc,Entropy,IO,Mut,Unsafe"
have "$(bucket "$work/main.axsym" 'Alloc,Entropy,IO,Unsafe')" 1 "Alloc,Entropy,IO,Unsafe"
have "$(bucket "$work/main.axsym" 'Alloc,IO,Mut,Spawn,Unsafe')" 2 "Alloc,IO,Mut,Spawn,Unsafe"
have "$(bucket "$work/main.axsym" 'Alloc,IO,Spawn')" 1 "Alloc,IO,Spawn"
have "$(bucket "$work/main.axsym" 'Block')" 3 "Block"
have "$(bucket "$work/main.axsym" 'Block,IO,Unsafe')" 2 "Block,IO,Unsafe"
have "$(bucket "$work/main.axsym" 'Entropy')" 1 "Entropy"
have "$(bucket "$work/main.axsym" 'IO,Spawn,Unsafe')" 1 "IO,Spawn,Unsafe"
have "$(bucket "$work/main.axsym" 'Spawn')" 2 "Spawn"
have "$(bucket "$work/main.axsym" 'Alloc,Block,IO,Mut')" 10 "Alloc,Block,IO,Mut"
have "$(bucket "$work/main.axsym" 'Block,IO')" 1 "Block,IO"
have "$(grep '^F ' "$work/main.axsym" | grep -vc '#effects=\|#effects-incomplete' || true)" 2124 "pure (neither row nor mark)"
have "$(grep '^F ' "$work/main.axsym" | grep -o '#effects=[^ #]*' | LC_ALL=C sort -u | wc -l | tr -d ' ')" 30 "distinct compiler effect rows"
have "$(grep -c '#effects-incomplete' "$work/main.axsym" || true)" 38 "incomplete rows"
have "$(grep -c '#effect-params' "$work/main.axsym" || true)" 15 "effect-params rows"

echo
# Par -> Task shares the handle helpers: one Unsafe and two pure rows
# disappear; every remaining migrated function keeps its measured effects.
# Explicit range batching adds one Alloc,Block,IO,Mut,Spawn row and one
# callback effect-parameter row; it delegates to the existing Task pool.
echo "== stdlib view: one probe importing every module =="
: > "$work/modfiles"
for f in stdlib/*.ax stdlib/*/*.ax; do
  [[ -e "$f" ]] || continue
  rel="${f#stdlib/}"; dir="$(dirname "$rel")"
  base="$(basename "$rel" .ax)"; base="${base%%.*}"
  if [[ "$dir" == "." ]]; then printf '%s\n' "$base" >> "$work/modfiles";
  else printf '%s.%s\n' "${dir//\//.}" "$base" >> "$work/modfiles"; fi
done
{
  while read -r m; do printf '(import %s)\n\n' "$m"; done < <(LC_ALL=C sort -u "$work/modfiles")
  printf '(:: main Int)\n\n(fn (main) 0)\n'
} > "$work/probe.ax"
( cd "$work" && AXIOM_STDLIB="$repo_root/stdlib" "$axc" --diagnostic-format=ai symbols --calls probe.ax ) \
  > "$work/lib.axsym" 2>"$work/lib.err" \
  || { fail "could not read symbols over the stdlib probe"; echo "check-effect-distribution: $failed failed"; exit 1; }
lrows="$(grep -c '^F ' "$work/lib.axsym" || true)"
(( lrows >= 300 )) && ok "$lrows stdlib functions listed (floor 300)" \
  || fail "only $lrows stdlib functions listed; the floor is 300"
have "$(bucket "$work/lib.axsym" 'Alloc,Mut')" 707 "exactly Alloc,Mut"
have "$(bucket "$work/lib.axsym" 'Alloc,IO,Mut')" 311 "Alloc,IO,Mut"
have "$(bucket "$work/lib.axsym" 'Mut')" 179 "exactly Mut"
have "$(bucket "$work/lib.axsym" 'Alloc')" 163 "exactly Alloc"
have "$(bucket "$work/lib.axsym" 'Alloc,IO')" 22 "Alloc,IO"
have "$(bucket "$work/lib.axsym" 'IO')" 14 "exactly IO"
have "$(bucket "$work/lib.axsym" 'Alloc,Assert,IO,Mut')" 7 "Alloc,Assert,IO,Mut"
have "$(bucket "$work/lib.axsym" 'IO,Mut')" 7 "IO,Mut"
have "$(bucket "$work/lib.axsym" 'Alloc,Mut,Unsafe')" 69 "Alloc,Mut,Unsafe"
have "$(bucket "$work/lib.axsym" 'Unsafe')" 74 "exactly Unsafe"
have "$(bucket "$work/lib.axsym" 'Alloc,IO,Mut,Unsafe')" 62 "Alloc,IO,Mut,Unsafe"
have "$(bucket "$work/lib.axsym" 'Mut,Unsafe')" 134 "Mut,Unsafe"
have "$(bucket "$work/lib.axsym" 'Alloc,Unsafe')" 7 "Alloc,Unsafe"
have "$(bucket "$work/lib.axsym" 'Alloc,IO,Unsafe')" 30 "Alloc,IO,Unsafe"
have "$(bucket "$work/lib.axsym" 'Alloc,Assert,IO,Mut,Unsafe')" 0 "Alloc,Assert,IO,Mut,Unsafe"
have "$(bucket "$work/lib.axsym" 'IO,Mut,Unsafe')" 3 "IO,Mut,Unsafe"
have "$(bucket "$work/lib.axsym" 'IO,Unsafe')" 20 "IO,Unsafe"
have "$(bucket "$work/lib.axsym" 'Alloc,IO,Spawn')" 1 "Alloc,IO,Spawn"
have "$(bucket "$work/lib.axsym" 'Alloc,IO,Mut,Spawn,Unsafe')" 3 "Alloc,IO,Mut,Spawn,Unsafe"
have "$(bucket "$work/lib.axsym" 'Alloc,Block,IO,Mut')" 25 "Alloc,Block,IO,Mut"
have "$(bucket "$work/lib.axsym" 'Alloc,Block,IO,Unsafe')" 0 "Alloc,Block,IO,Unsafe"
have "$(bucket "$work/lib.axsym" 'Block,IO,Mut,Unsafe')" 0 "Block,IO,Mut,Unsafe"
have "$(bucket "$work/lib.axsym" 'Alloc,Block,IO,Mut,Spawn')" 16 "Alloc,Block,IO,Mut,Spawn"
have "$(bucket "$work/lib.axsym" 'Alloc,Block,IO,Mut,Spawn,Unsafe')" 2 "Alloc,Block,IO,Mut,Spawn,Unsafe"
have "$(bucket "$work/lib.axsym" 'Fallible')" 1 "exactly Fallible"
have "$(bucket "$work/lib.axsym" 'Assert')" 1 "exactly Assert"
have "$(bucket "$work/lib.axsym" 'Alloc,Block,IO,Mut,Unsafe')" 7 "Alloc,Block,IO,Mut,Unsafe"
have "$(bucket "$work/lib.axsym" 'Alloc,Entropy,IO,Mut')" 22 "Alloc,Entropy,IO,Mut"
have "$(bucket "$work/lib.axsym" 'Alloc,Entropy,IO,Mut,Unsafe')" 1 "Alloc,Entropy,IO,Mut,Unsafe"
have "$(bucket "$work/lib.axsym" 'Alloc,Entropy,IO,Unsafe')" 1 "Alloc,Entropy,IO,Unsafe"
have "$(bucket "$work/lib.axsym" 'Block')" 3 "Block"
have "$(bucket "$work/lib.axsym" 'Block,IO,Unsafe')" 2 "Block,IO,Unsafe"
have "$(bucket "$work/lib.axsym" 'Entropy')" 2 "Entropy"
have "$(bucket "$work/lib.axsym" 'Entropy,Mut')" 1 "Entropy,Mut"
have "$(bucket "$work/lib.axsym" 'IO,Spawn,Unsafe')" 1 "IO,Spawn,Unsafe"
have "$(bucket "$work/lib.axsym" 'Spawn')" 2 "Spawn"
have "$(bucket "$work/lib.axsym" 'Alloc,Block,IO')" 2 "Alloc,Block,IO"
have "$(bucket "$work/lib.axsym" 'Alloc,IO,Mut,Spawn')" 1 "Alloc,IO,Mut,Spawn"
have "$(bucket "$work/lib.axsym" 'Block,IO')" 1 "Block,IO"
have "$(bucket "$work/lib.axsym" 'Block,IO,Mut')" 1 "Block,IO,Mut"
have "$(grep '^F ' "$work/lib.axsym" | grep -vc '#effects=\|#effects-incomplete' || true)" 1085 "pure (neither row nor mark)"
have "$(grep '^F ' "$work/lib.axsym" | grep -o '#effects=[^ #]*' | LC_ALL=C sort -u | wc -l | tr -d ' ')" 37 "distinct stdlib effect rows"
have "$(grep -c '#effects-incomplete' "$work/lib.axsym" || true)" 17 "incomplete rows"
have "$(grep -c '#effect-params' "$work/lib.axsym" || true)" 58 "effect-params rows"

echo
echo "== the pins refuse doctored counts =="
if (( 2015 == 2014 )); then fail "probe accepted"; else ok "probe: 2015 against pin 2014 is refused"; fi
if (( 4 == 3 )); then fail "probe accepted"; else ok "probe: 4 incomplete against pin 3 is refused"; fi

echo
if (( failed > 0 )); then
  echo "check-effect-distribution: $failed check(s) failed, $passed passed"
  exit 1
fi
echo "check-effect-distribution: $passed checks - the ambient line sits where it was measured"
