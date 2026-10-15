#!/usr/bin/env bash
# `explain`, `symbols` and `doc`, pinned without a second compiler.
#
# This gate used to be a differential against the Rust compiler: run
# both binaries on the same input, require identical bytes. That was
# the right shape while the Rust compiler was the trusted reference and
# the Axiom one was being brought up to it. It stops being available
# the moment the reference is deleted, and - this is the part worth
# being careful about - a differential does not fail when its reference
# disappears. Point `$axiom` at a self-hosted binary and every
# comparison becomes a compiler against itself: 214 files swept, zero
# differences, exit 0, and nothing tested. That is the failure mode
# this repository names most often, and it is why the gate was rewritten
# rather than left to degrade.
#
# What replaces it, per surface:
#
#   explain   a checked-in golden of every code's text, materialized
#             from the Rust compiler at the last commit that had one
#             (tests/tools/explain.golden), PLUS a cross-check that
#             every code the diagnostics corpus actually emits has an
#             entry. The second half is derived from a different
#             artifact - tests/diagnostics/*.axdl - so re-blessing the
#             explain golden cannot satisfy it.
#
#   symbols   three checks, of which only the first is a golden:
#               1. AXSYM for tests/fmt/syntax-zoo.ax, byte for byte
#               2. `symbols` exits 0 for exactly the files `check`
#                  exits 0 for - two subcommands of one binary
#                  cross-checking each other, with nothing to bless
#               3. EVERY position claim AXSYM makes, verified against
#                  the source bytes: at the line and columns a symbol
#                  names, the file must literally spell that symbol
#
# Check 3 is strictly stronger than the differential it replaces.
# Agreeing with another implementation says two things agree; this says
# the answer is CORRECT, it cannot be satisfied by regenerating
# anything, and it does not churn when a span moves - the position is
# recomputed from the source on every run. 14,123 claims over the
# corpus, plus 503 symbols that carry no position by design (the
# builtin `Option` and its constructors, and the operations of an
# `effect`) and are counted rather than skipped.
#
# THE DRILL, run rather than assumed. A compiler was built with every
# AXSYM start column shifted by one, and BOTH goldens were regenerated
# from it:
#
#   the zoo golden        re-blessed clean - matched the wrong compiler
#   the status manifest   re-blessed clean - matched the wrong compiler
#   verify-axsym.py       14,100 of 14,100 claims wrong, plus 23 lines
#                         it could not even parse (one-character names,
#                         whose spans went zero-width and printed
#                         without the dash), exit 1
#
# That is the whole argument for check 3 existing, measured: the two
# goldens are exactly as strong as whoever last regenerated them, and
# the verifier is not.
#
# The provenance of both goldens, recorded at the last moment there
# were two compilers to compare: at commit 1041f11 the Rust compiler
# and the self-hosted one produced IDENTICAL AXSYM for all 216 files -
# all 14,626 lines - and identical exit statuses, once paths are
# normalised and the sweep runs from a neutral directory.
#
# Usage:  scripts/check-tools-selfhost.sh

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/gate.sh"
gate_init

failed=0

# AXSYM names the file each symbol came from, and for an imported
# stdlib module that path depends on where the BINARY lives - both
# compilers resolve their stdlib relative to themselves, so the same
# symbol is `.../target/release/../../stdlib/Mem.ax` from one install
# and `.../.axiom-bin/../stdlib/Mem.ax` from another. Neither is wrong
# and neither belongs in a golden. Normalising to the repo-relative
# spelling is what makes the golden a fact about AXSYM rather than
# about the checkout.
# Anchored on the `..` segment, which is what marks a
# binary-relative path. An earlier version matched any path ending in
# `stdlib/` and quietly rewrote `tests/stdlib/010-hello.ax` to
# `stdlib/010-hello.ax` - a file that does not exist, which the
# position verifier then reported as 191 unreadable files. The
# normaliser has to name the thing it is normalising.
norm() { sed -E -e "s|[^ ]*\.\./stdlib/|stdlib/|g" -e "s|[^ ]*\.\./self_host/|self_host/|g" -e "s|$repo_root/||g"; }

# Every invocation runs from `$neutral`, a directory containing
# nothing. Explicit relative roots resolve against that directory,
# so these checks cannot acquire modules from an incidental work tree.
neutral="$work/neutral"
mkdir -p "$neutral"

# The compiler under test is the one built FROM SOURCE by `$axiom`, not
# `$axiom` itself: this gate exists to test self_host/, and `$axiom` may
# be an older seed-descended binary that predates the change being
# tested.
#
# There are no stdlib/self_host symlinks in the work directory.
gate_build_axc axc

# ---------------------------------------------------------------
# explain
# ---------------------------------------------------------------
# THE COLOUR IN THE LIST SECTION IS RECONSTRUCTED, NOT CAPTURED, and the
# golden's header says so. stage0 printed `explain --list` through the
# `colored` crate, which does its own tty detection and has no
# `set_override` anywhere in that binary - so into a pipe, which is how
# this golden was materialised, stage0 emitted no escapes at all. Its
# diagnostics went through ariadne, which had the opposite behaviour and
# painted a pipe as readily as a terminal.
#
# stage1 is always-on everywhere, which EXCEEDS stage0 here rather than
# matching it. The palette is `colored`'s - bold bright cyan heading,
# bright yellow codes - so the intent is reproduced even though the
# bytes were never observed. Recorded because this file's whole job is
# provenance, and a captured byte and a reconstructed one are not the
# same claim.
echo "== explain: every code and spelling, against the golden =="
golden="tests/tools/explain.golden"
[[ -f "$golden" ]] || { echo "FAIL: $golden is missing"; exit 1; }

{
  # Regenerated in the same order and with the same section headers the
  # golden was written with; a mismatch in the ORDER is a real
  # difference and is meant to fail.
  head -4 "$golden"
  echo "### explain --list"
  "$work/axc" explain --list 2>&1
  for c in $("$work/axc" explain --list 2>/dev/null | grep -oE 'AX[0-9]{4}' | sort -u); do
    echo "### explain $c"
    "$work/axc" explain "$c" 2>&1
  done
  for v in ax3001 3001 AX-3001 AX9999 ax99; do
    echo "### explain $v (exit $("$work/axc" explain "$v" >/dev/null 2>&1; echo $?))"
    "$work/axc" explain "$v" 2>&1
  done
  echo "### explain (no argument) (exit $("$work/axc" explain >/dev/null 2>&1; echo $?))"
  "$work/axc" explain 2>&1
} > "$work/explain.out"

if ! cmp -s "$golden" "$work/explain.out"; then
  echo "FAIL explain: output differs from $golden"
  diff "$golden" "$work/explain.out" | head -12 | sed 's/^/     /'
  failed=$((failed + 1))
else
  echo "ok   explain matches the golden ($(grep -c '^### ' "$golden") sections)"
fi

# Ordering and format refusal are independent of a blessed golden.
python3 - "$work/explain.out" <<'PY_ORDER'
from pathlib import Path
import re
import sys
text = Path(sys.argv[1]).read_text()
listing = text.split('### explain --list\n', 1)[1].split('### explain AX', 1)[0]
codes = [int(c) for c in re.findall(r'AX([0-9]{4})', listing)]
assert codes == sorted(set(codes)), 'explain --list is not in numeric order'
PY_ORDER
for input in "$repo_root/tests/fmt/syntax-zoo.ax" "$work/absent.ax"; do
  for shape in plain axir; do
    flags=(--diagnostic-format=json)
    [[ "$shape" == axir ]] && flags+=(--axir)
    if "$work/axc" symbols "${flags[@]}" "$input" >"$work/refuse.out" 2>"$work/refuse.err"; then
      rc=0
    else
      rc=$?
    fi
    if [[ "$rc" != 2 || -s "$work/refuse.out" ]] || ! grep -q -- '--diagnostic-format=ai' "$work/refuse.err"; then
      echo "FAIL symbols: JSON must be refused with guidance before reading input ($shape, $input, rc=$rc)"
      failed=$((failed + 1))
    fi
  done
done
if "$work/axc" --diagnostic-format=json symbols "$work/absent.ax" >"$work/refuse.out" 2>"$work/refuse.err"; then
  rc=0
else
  rc=$?
fi
if [[ "$rc" != 2 || -s "$work/refuse.out" ]] || ! grep -q -- '--diagnostic-format=ai' "$work/refuse.err"; then
  echo 'FAIL symbols: JSON refusal must accept a global flag before the command'
  failed=$((failed + 1))
fi

# The half a re-bless of that golden cannot satisfy: the codes come from
# a DIFFERENT artifact. Every code the diagnostics corpus actually emits
# must have an explanation, so a new diagnostic that nobody documented
# fails here rather than shipping undocumented.
echo "== explain: every code the diagnostics corpus emits has an entry =="
listed="$("$work/axc" explain --list 2>/dev/null | grep -oE 'AX[0-9]{4}' | sort -u)"
emitted="$(grep -ohE 'AX[0-9]{4}' tests/diagnostics/*.axdl | sort -u)"
emitted_n="$(printf '%s\n' "$emitted" | grep -c .)"
if (( emitted_n < 40 )); then
  echo "FAIL: only $emitted_n codes found in tests/diagnostics/*.axdl; the floor is 40 (the corpus moved or the glob broke)"
  failed=$((failed + 1))
fi
missing="$(comm -23 <(printf '%s\n' "$emitted") <(printf '%s\n' "$listed") | tr '\n' ' ')"
if [[ -n "${missing// /}" ]]; then
  echo "FAIL explain: codes the corpus emits with no explanation: $missing"
  failed=$((failed + 1))
else
  echo "ok   all $emitted_n codes emitted by the corpus are explained"
fi

# ---------------------------------------------------------------
# symbols
# ---------------------------------------------------------------
# `--target` is PINNED for both golden comparisons below, and has to be.
#
# The zoo imports `Sys`, `Sys` imports `Sys.Platform`, and that module is
# selected by TARGET - `Platform.darwin.ax` here, `Platform.linux-aarch64.ax`
# on one CI runner, `Platform.linux-x86_64.ax` on another. Its symbols land
# in the table with the resolved file's name AND its line numbers, so 34 of
# the zoo's 99 rows are a statement about the host rather than about the
# fixture - 22 of 89 until the filesystem verbs the self-hosting record added
# ten constants to every platform module. Blessed on darwin, the golden
# could only ever match on darwin:
#
#     < F sysRead stdlib/Sys/Platform.darwin.ax:21:9-16 "Int" @d99009b49c22cd84
#     > F sysRead stdlib/Sys/Platform.linux-aarch64.ax:8:9-16 "Int" @d99009b49c22cd84
#
# which is what both Linux targets reported the first time this gate ever
# ran on them. Note the hash is EQUAL across the two - the symbol is the
# same symbol; only its address in the tree moved.
#
# Pinning a target rather than normalising the path away is the choice that
# keeps the other half honest: the cited file still exists on every host, so
# the position verifier can still open it and check the claim against real
# bytes. A placeholder would have to be exempted from that check, which is
# the half a re-bless cannot satisfy and the half worth keeping.
#
# darwin-aarch64 is simply the target the goldens were blessed with; any
# fixed one would do. What matters is that it is fixed, which also means a
# contributor on Linux can now run this gate at all - until this line they
# could not.
zoo_target="darwin-aarch64"

echo "== symbols: the syntax zoo, against the golden =="
zoo_golden="tests/tools/symbols-zoo.golden"
(cd "$neutral" && "$work/axc" --target="$zoo_target" --diagnostic-format=ai symbols \
   "$repo_root/tests/fmt/syntax-zoo.ax" 2>/dev/null) | norm >"$work/zoo.out"
# AXSYM names the file and span each symbol was DECLARED at, so this
# golden moves whenever a stdlib module is edited - and it had no bless
# path while its sibling table below did, so re-blessing it meant
# knowing the pipeline by hand.
if [[ "${AXIOM_BLESS:-0}" == 1 ]]; then
  cp "$work/zoo.out" "$repo_root/$zoo_golden"
  echo "blessed $zoo_golden"
fi
[[ -f "$zoo_golden" ]] || { echo "FAIL: $zoo_golden is missing; run with AXIOM_BLESS=1"; exit 1; }
if ! cmp -s "$zoo_golden" "$work/zoo.out"; then
  echo "FAIL symbols: the zoo's AXSYM differs from $zoo_golden"
  diff "$zoo_golden" "$work/zoo.out" | head -10 | sed 's/^/     /'
  failed=$((failed + 1))
else
  echo "ok   the zoo's AXSYM matches the golden ($(wc -l <"$zoo_golden" | tr -d ' ') symbols)"
fi

# ---------------------------------------------------------------
# The same zoo with `--calls`, which is the only place the edge lists
# are pinned as VALUES rather than as a property.
#
# `check-agent-calls.sh` asserts the graph's INVARIANTS - containment,
# totality, grounding - over the standard library, and an invariant is
# satisfied by a graph that is merely consistent. It would not notice
# an edge quietly disappearing from every row at once, because a graph
# with fewer edges still contains its callees' effects. This golden is
# the other half: it says which edges, for a fixed input.
#
# It also pins the SILENCE the feature promises. The AXSYM golden above
# is produced by the same binary on the same file without `--calls`,
# so if the key ever leaks into the default stream, that golden moves
# and this one stops being the only place it appears.
# ---------------------------------------------------------------
echo "== symbols: the zoo's call graph, against the golden =="
calls_golden="tests/tools/symbols-zoo-calls.golden"
(cd "$neutral" && "$work/axc" --target="$zoo_target" --diagnostic-format=ai symbols \
   --calls "$repo_root/tests/fmt/syntax-zoo.ax" 2>/dev/null) | norm >"$work/zoo.calls"
if [[ "${AXIOM_BLESS:-0}" == 1 ]]; then
  cp "$work/zoo.calls" "$repo_root/$calls_golden"
  echo "blessed $calls_golden"
fi
[[ -f "$calls_golden" ]] || { echo "FAIL: $calls_golden is missing; run with AXIOM_BLESS=1"; exit 1; }
if ! cmp -s "$calls_golden" "$work/zoo.calls"; then
  echo "FAIL symbols: the zoo's call graph differs from $calls_golden"
  diff "$calls_golden" "$work/zoo.calls" | head -10 | sed 's/^/     /'
  failed=$((failed + 1))
else
  echo "ok   the zoo's call graph matches the golden ($(grep -c '#calls=' "$calls_golden" || true) rows carry edges)"
fi

# The two streams differ ONLY by the key. Stripping every `#calls=`
# field from the `--calls` run must reproduce the plain run byte for
# byte - which is what "opt-in" has to mean for a golden to be worth
# pinning, and is stronger than grepping the plain stream for the key:
# it also catches the key displacing, reordering or truncating a field
# that was already there.
sed -E 's/ #calls=[^ ]*//' "$work/zoo.calls" > "$work/zoo.stripped"
if ! cmp -s "$work/zoo.stripped" "$work/zoo.out"; then
  echo "FAIL symbols: --calls changed the stream by more than the key it adds"
  diff "$work/zoo.out" "$work/zoo.stripped" | head -10 | sed 's/^/     /'
  failed=$((failed + 1))
else
  echo "ok   --calls adds the key and disturbs nothing else"
fi

# ---------------------------------------------------------------
# The human table, and the half a re-bless of ITS golden cannot satisfy.
#
# `symbols` printed AXSYM under every format until the table landed;
# stage0's bare `symbols` printed the table and only `ai` gave AXSYM.
# The golden below is the re-blessable half. The derivation after it
# reads `$work/zoo.out` - the AXSYM golden's own bytes, a different
# artifact - and reconstructs every row from them, so a table that
# invented, dropped, reordered or misspelled a symbol fails even with a
# freshly written golden beside it.
# ---------------------------------------------------------------
echo "== symbols: the human table, against the golden and against AXSYM =="
tbl_golden="tests/tools/symbols-zoo-human.golden"
(cd "$neutral" && "$work/axc" --target="$zoo_target" symbols \
   "$repo_root/tests/fmt/syntax-zoo.ax" 2>/dev/null) | norm >"$work/zoo.tbl"
if [[ "${AXIOM_BLESS:-0}" == 1 ]]; then
  cp "$work/zoo.tbl" "$repo_root/$tbl_golden"
  echo "blessed $tbl_golden"
fi
if [[ ! -f "$tbl_golden" ]]; then
  echo "FAIL symbols: $tbl_golden is missing; run with AXIOM_BLESS=1"
  failed=$((failed + 1))
elif ! cmp -s "$tbl_golden" "$work/zoo.tbl"; then
  echo "FAIL symbols: the human table differs from $tbl_golden"
  diff "$tbl_golden" "$work/zoo.tbl" | head -6 | sed 's/^/     /'
  failed=$((failed + 1))
else
  echo "ok   the human table matches the golden ($(wc -l <"$tbl_golden" | tr -d ' ') rows)"
fi

# Row for row against the AXSYM the same run produced.
tbl_bad="$(python3 - "$work/zoo.out" "$work/zoo.tbl" <<'PY'
import re, sys
# One entry per arm of `symKindWord` in self_host/symbols.ax, and no
# more: `X` (foreign binding) was retired with the FFI, and leaving it
# here would have this rule spell a kind the compiler prints raw.
# This table is the SECOND implementation of `symKindWord`, and it is
# deliberately a second one: the check reconstructs the human table
# from AXSYM and compares, so a letter the compiler emits and this
# does not know is a real failure rather than a re-bless. It caught
# `M` and `E` the day they were added.
KIND = {'F': 'Fn', 'D': 'Data', 'C': 'Ctor',
        'S': 'Struct', 'A': 'Alias', 'T': 'Trait',
        'M': 'Macro', 'E': 'Effect'}
axsym = [l for l in open(sys.argv[1], encoding='utf-8').read().split('\n') if l]
table = [l for l in open(sys.argv[2], encoding='utf-8').read().split('\n') if l]
bad = []
if len(axsym) != len(table):
    bad.append('%d AXSYM lines, %d table rows' % (len(axsym), len(table)))
for i, (a, t) in enumerate(zip(axsym, table)):
    k, name, loc, rest = a.split(' ', 3)
    ty = rest[1:rest.rindex('"')] if rest.startswith('"') else ''
    want_loc = 'builtin' if loc == '-' else loc
    # The rule, not the golden: 8, 20 and 40 columns, left justified,
    # single spaces between, location in brackets at the end.
    want = '%-8s %-20s %-40s [%s]' % (KIND.get(k, k), name, ty, want_loc)
    if t != want:
        bad.append('row %d\n      is   %r\n      want %r' % (i + 1, t, want))
    if len(bad) > 3:
        break
print('\n    '.join(bad))
PY
)"
if [[ -n "$tbl_bad" ]]; then
  echo "FAIL symbols: the table does not reconstruct from AXSYM"
  echo "    $tbl_bad"
  failed=$((failed + 1))
else
  echo "ok   every table row reconstructs from its AXSYM line ($(wc -l <"$work/zoo.out" | tr -d ' ') rows)"
fi

# ---------------------------------------------------------------
# `agent:*` tags survive `fmt`, AXSYM and import
# (docs/agent-harness.md §7). The compiler records the open `agent:*`
# namespace and re-emits it without checking it; what this section
# holds is that nothing on the way drops one. `fmt` runs over copies
# in $work, so a formatter that ate a tag comment could never touch
# the fixtures - and `--check` alone would only prove the input
# canonical, not that the tag made it through, which is why the
# section formats and compares bytes. (`fmt --check` takes one file;
# a second filename on the line is silently unchecked, so the loop.)
# ---------------------------------------------------------------
echo "== symbols: agent:* tags survive fmt, AXSYM and import =="
taglib="$repo_root/tests/tools/TagLib.ax"
tagimp="$repo_root/tests/tools/TagLibImport.ax"
cp "$taglib" "$tagimp" "$work/"
for f in TagLib.ax TagLibImport.ax; do
  if ! (cd "$work" && "$work/axc" fmt --check "$f" >/dev/null 2>&1); then
    echo "FAIL symbols: tests/tools/$f is not fmt-canonical"
    failed=$((failed + 1))
  fi
done
(cd "$work" && "$work/axc" fmt TagLib.ax TagLibImport.ax >/dev/null 2>&1)
if ! cmp -s "$taglib" "$work/TagLib.ax" || ! cmp -s "$tagimp" "$work/TagLibImport.ax"; then
  echo "FAIL symbols: fmt rewrote a tag comment it should have kept"
  failed=$((failed + 1))
else
  echo "ok   fmt keeps both fixtures byte-identical, tag comments included"
fi
(cd "$neutral" && "$work/axc" --diagnostic-format=ai symbols "$taglib" 2>/dev/null) | norm >"$work/tags.out"
for key in '#agent:readonly' '#agent:allowed=net,fs' '#agent:rewrite'; do
  if ! grep -qF "$key" "$work/tags.out"; then
    echo "FAIL symbols: $key missing from TagLib.ax's AXSYM"
    failed=$((failed + 1))
  fi
done
if ! grep -q '^F plain ' "$work/tags.out" || grep '^F plain ' "$work/tags.out" | grep -q '#agent:'; then
  echo "FAIL symbols: the untagged control row is missing or carries a tag"
  failed=$((failed + 1))
else
  echo "ok   AXSYM re-emits all three agent keys, and the untagged row carries none"
fi
(cd "$neutral" && "$work/axc" --diagnostic-format=ai symbols "$tagimp" 2>/dev/null) | norm >"$work/tags-imp.out"
if ! grep -qF 'F quiet tests/tools/TagLib.ax' "$work/tags-imp.out" \
  || ! grep -qF '#agent:readonly' "$work/tags-imp.out" \
  || ! grep -qF '#agent:allowed=net,fs' "$work/tags-imp.out"; then
  echo "FAIL symbols: the importer's AXSYM does not re-emit TagLib's tags against TagLib.ax"
  failed=$((failed + 1))
else
  echo "ok   the tags cross the import, attributed to the defining file"
fi
# Negative probe: the keys come from the comments, not from thin air.
# A copy with one tag comment deleted must lose exactly that key and
# keep the other two - and still check clean, since a comment is not
# code.
sed '/;@axiom:agent:readonly/d' "$taglib" >"$work/TagLib-stripped.ax"
(cd "$neutral" && "$work/axc" --diagnostic-format=ai symbols "$work/TagLib-stripped.ax" 2>/dev/null) | norm >"$work/tags-stripped.out"
if grep -qF '#agent:readonly' "$work/tags-stripped.out"; then
  echo "FAIL symbols: #agent:readonly survived deleting its comment"
  failed=$((failed + 1))
elif ! grep -qF '#agent:allowed=net,fs' "$work/tags-stripped.out" \
  || ! grep -qF '#agent:rewrite' "$work/tags-stripped.out"; then
  echo "FAIL symbols: deleting one tag comment lost a key it should have kept"
  failed=$((failed + 1))
else
  echo "ok   each key answers to its own comment and no other"
fi

echo '== symbols: exit status agrees with check, file by file =='
# This was a checked-in manifest of the per-file exit status, and the
# manifest was wrong for the corpus - it enumerated every `.ax` in the
# repository, so ADDING A TEST FIXTURE anywhere made it fail and need
# re-blessing. A golden that churns for reasons unrelated to what it
# pins is a golden nobody reads.
#
# The property behind it does not churn. `symbols` folds every failure
# into exit 1, where `check` distinguishes parse (2) from semantic (1),
# so for every file:
#
#     symbols exits 0  <=>  check exits 0
#
# That is two independent paths through the same binary - a file that
# type-checks but whose symbols cannot be listed, or vice versa, is a
# real defect and nothing has to be regenerated for this to notice.
# Measured over 224 files at the time it was written: zero mismatches.
: > "$work/all.axsym"
swept=0; accepted=0; refused=0
while read -r f; do
  swept=$((swept + 1))
  (cd "$neutral" && "$work/axc" check "$repo_root/$f" >/dev/null 2>&1); c=$?
  (cd "$neutral" && "$work/axc" --diagnostic-format=ai symbols "$repo_root/$f" \
     2>/dev/null) | norm > "$work/one.axsym"
  (cd "$neutral" && "$work/axc" --diagnostic-format=ai symbols "$repo_root/$f" \
     >/dev/null 2>&1); sy=$?
  cat "$work/one.axsym" >> "$work/all.axsym"
  if [[ "$c" == 0 ]]; then
    accepted=$((accepted + 1))
    if [[ "$sy" != 0 ]]; then
      echo "FAIL $f: check accepted it (0) but symbols refused it ($sy)"
      failed=$((failed + 1))
    fi
  else
    refused=$((refused + 1))
    if [[ "$sy" == 0 ]]; then
      echo "FAIL $f: check refused it ($c) but symbols answered (0)"
      failed=$((failed + 1))
    elif [[ "$sy" != 1 ]]; then
      echo "FAIL $f: symbols exited $sy; it folds every failure into 1"
      failed=$((failed + 1))
    fi
  fi
done < <(find stdlib self_host tests -name '*.ax' | sort)

# Re-derived 2026-08-16 against 390 files; it was 150, set when the
# tree held ~194.
if (( swept < 370 )); then
  echo "FAIL: the sweep read only $swept files; the floor is 370 - a tree is missing from the find"
  failed=$((failed + 1))
# Both outcomes have to occur, or the agreement is trivial: a `symbols`
# that always succeeded would satisfy a corpus that always type-checks.
elif (( accepted == 0 || refused == 0 )); then
  echo "FAIL: every file got the same verdict ($accepted accepted, $refused refused) - the check cannot distinguish anything"
  failed=$((failed + 1))
else
  echo "ok   $swept files, symbols agrees with check everywhere ($accepted accepted, $refused refused)"
fi

echo "== symbols: every position claim, against the source =="
if ! (cd "$repo_root" && python3 tests/tools/verify-axsym.py "$work/all.axsym"); then
  echo "FAIL symbols: AXSYM claims a symbol where the source does not have one"
  failed=$((failed + 1))
fi

# ---------------------------------------------------------------
# The documented programs compile.
#
# `check-tree-sitter.sh` runs the same verifier for delimiter BALANCE,
# in the grammar job that deliberately has no compiler. Balance cannot
# see an example that parses and means nothing, and three did - `(fn
# main ...)` with more than one body expression is a syntax error, and
# `printf` is not a function in this language. This is where the
# compiler exists, so this is where they are compiled.
# ---------------------------------------------------------------
echo "== docs: whole programs compile and marked outputs match =="
if ! python3 "$repo_root/tests/docs/test-doc-code.py"; then
  failed=$((failed + 1))
fi
gate_prose_docs_abs
if ! python3 "$repo_root/tests/docs/verify-doc-code.py" --compile "$work/axc" \
     "${prose_docs[@]}"; then
  failed=$((failed + 1))
fi

echo "== symbols: flags, and a file that is not there =="
zoocase="$repo_root/tests/stdlib/010-hello.ax"
(cd "$neutral" && "$work/axc" --diagnostic-format=ai symbols --builtins "$zoocase" \
   >"$work/b1.out" 2>/dev/null); b1=$?
(cd "$neutral" && "$work/axc" --diagnostic-format=ai symbols "$zoocase" --builtins \
   >"$work/b2.out" 2>/dev/null); b2=$?
if [[ "$b1" != 0 || "$b2" != 0 ]] || ! cmp -s "$work/b1.out" "$work/b2.out"; then
  echo "FAIL symbols: --builtins is order-sensitive (exits $b1/$b2)"
  failed=$((failed + 1))
elif ! grep -q '^C Some ' "$work/b1.out"; then
  echo "FAIL symbols: --builtins printed no builtin constructor - the flag did nothing"
  failed=$((failed + 1))
else
  echo "ok   --builtins works in either position and lists the builtins"
fi

"$work/axc" symbols "$work/does-not-exist.ax" >"$work/m.out" 2>/dev/null; m=$?
if [[ "$m" == 0 || -s "$work/m.out" ]]; then
  echo "FAIL symbols: a missing file exited $m with $(wc -c <"$work/m.out" | tr -d ' ') bytes on stdout"
  failed=$((failed + 1))
else
  echo "ok   a missing file is refused with empty stdout"
fi

# --------------------------------------------------------------------
# explain: the lexicon
#
# `axiom explain WORD` answers a keyword, a builtin type, an effect, an
# operator or a literal from `self_host/lexicon.ax`, and `explain @KEY`
# an AXTAG key. The lexicon is a hand-written table of another
# module's vocabulary, which is the shape that drifts, so it is held
# to the sources it mirrors:
#
#   * every head keyword the parser dispatches on (`kwEq name "..."` in
#     self_host/parser.ax) and every word the server's completion menu
#     offers (`lspKeywords` in self_host/lsp.ax) has an entry;
#   * every AXTAG key the checker knows (`axtagKnownKey` in
#     self_host/typecheck.ax) has an entry;
#   * every word the lexicon's own index lists answers, and the
#     `Reference:` line each answer ends with names a heading or an
#     `<a id>` that docs/reference.md has;
#   * a word nothing knows is refused: exit 1, nothing on stdout.
# --------------------------------------------------------------------
echo "== explain: the lexicon, against the parser, the checker and the reference =="
if python3 - "$work/axc" <<'PY_LEX'
import re, subprocess, sys
from pathlib import Path
axc = sys.argv[1]
parser = Path("self_host/parser.ax").read_text()
lsp = Path("self_host/lsp.ax").read_text()
tc = Path("self_host/typecheck.ax").read_text()
lexicon = Path("self_host/lexicon.ax").read_text()
ref = Path("docs/reference.md").read_text()

def block(src, head):
    i = src.index(head)
    j = src.index("\n(pub ::", i + 1)
    return src[i:j]

keywords = set(re.findall(r'kwEq\s+\w+\s+"([^"]+)"', parser))
keywords |= set(re.findall(r'\(vecPush v "([^"]+)"\)', block(lsp, "(pub fn (lspKeywords)")))
tagkeys = set(re.findall(r'\(strEq key "([^"]+)"\)', block(tc, "(pub fn (axtagKnownKey key)")))
words = re.findall(r'\(vecPush v "([^"]+)"\)', block(lexicon, "(pub fn (lexWords)"))
lexkeys = re.findall(r'\(vecPush v "([^"]+)"\)', block(lexicon, "(pub fn (lexTagKeys)"))
if len(keywords) < 15 or len(tagkeys) < 5 or len(words) < 40 or len(lexkeys) < 5:
    sys.exit(f"FAIL: an extraction stopped matching: {len(keywords)} keywords, "
             f"{len(tagkeys)} tag keys, {len(words)} lexicon words, {len(lexkeys)} lexicon keys")

def slug(h):
    h = h.strip().lower()
    h = re.sub(r"[^\w\s-]", "", h)
    return re.sub(r"\s+", "-", h)
anchors = {slug(h) for h in re.findall(r"^#{1,6}\s+(.*)$", ref, re.M)}
anchors |= set(re.findall(r'<a id="([^"]+)"></a>', ref))

failed = []
for w in sorted(keywords - set(words)):
    failed.append(f"the parser's keyword `{w}` has no lexicon entry")
for k in sorted(tagkeys - set(lexkeys)):
    failed.append(f"the checker's AXTAG key `{k}` has no lexicon entry")
for w in words:
    # `--` ends option parsing, which `-` and `->` would otherwise start.
    r = subprocess.run([axc, "explain", "--", w], capture_output=True)
    out = r.stdout.decode()
    if r.returncode != 0 or "```axiom" not in out:
        failed.append(f"`explain {w}` exited {r.returncode} with {out[:60]!r}")
        continue
    m = re.search(r"Reference: `docs/reference\.md#([^`]+)`", out)
    if not m:
        failed.append(f"`explain {w}` ends with no Reference line")
    elif m.group(1) not in anchors:
        failed.append(f"`explain {w}` points at reference.md#{m.group(1)}, which no heading has")
for k in lexkeys:
    r = subprocess.run([axc, "explain", "@" + k], capture_output=True)
    if r.returncode != 0 or ";@axiom:" + k not in r.stdout.decode():
        failed.append(f"`explain @{k}` exited {r.returncode}")
for w in ("nosuchword", "@nosuchkey"):
    r = subprocess.run([axc, "explain", w], capture_output=True)
    if r.returncode != 1 or r.stdout:
        failed.append(f"`explain {w}` exited {r.returncode} with stdout {r.stdout[:40]!r}, want 1 and nothing")
for f in failed:
    print("     " + f)
if failed:
    sys.exit(1)
print(f"     {len(words)} words and {len(lexkeys)} AXTAG keys answer; {len(keywords)} parser keywords "
      f"and {len(tagkeys)} checker keys covered; every Reference line resolves")
PY_LEX
then
  echo "ok   the lexicon covers the parser's keywords and the checker's AXTAG keys, and points into the reference"
else
  echo "FAIL the lexicon has drifted from the parser, the checker or the reference"
  failed=$((failed + 1))
fi

# --------------------------------------------------------------------
# doc
#
# `axiom doc` renders the documentation set, one page per library
# module and the language index. Five properties, of which only the
# fixture's body is a golden:
#
#   1. every source page produced a file, every stdlib module has a
#      page, every `(pub :: name` has an anchor on its module page, and
#      the language index carries every lexicon word and AXTAG key;
#   2. every internal link - an `href` to a local file, with or without
#      `#anchor` - resolves to a file that exists and, with an anchor,
#      to an `id` that file has. This is the gap-finder: a heading
#      renamed in one page and linked from another fails here. Floor
#      200 links, so a sweep that found nothing cannot pass;
#   3. `--missing` names exactly the declarations whose Summary cell in
#      docs/stdlib-api.md is blank, and exits 1 iff there are any. The
#      two are different readers of the same doc comments
#      (`self_host/doccomment.ax` and `examples/axdoc/axdoc.ax`), so a
#      drift between them shows here;
#   4. tests/tools/doc-render.md, one of every construct the renderer
#      knows, renders byte for byte to tests/tools/doc-render.golden -
#      the page's <main>, so the sidebar that lists every page is not
#      pinned and a new document does not move the golden;
#   5. refusals: no documentation set is exit 1, one stderr line and
#      nothing on stdout; an unwritable --out is exit 1.
#
# A negative probe renders a page with a dangling anchor and a link to
# a file that is not there, and requires the link check to say so.
# --------------------------------------------------------------------
doc_check() {  # <repo_root> <site> -> exit 0 when every property holds
  python3 - "$1" "$2" <<'PY_DOC'
import glob, html, os, re, sys
root, site = sys.argv[1], sys.argv[2]
bad = []
pages = {p: open(p, encoding="utf-8").read()
         for p in glob.glob(os.path.join(site, "**", "*.html"), recursive=True)}
ids = {p: set(re.findall(r'\sid="([^"]+)"', t)) for p, t in pages.items()}

want = {os.path.join(site, "index.html"): "docs/README.md"}
for d in sorted(glob.glob(os.path.join(root, "docs", "*.md"))):
    b = os.path.basename(d)
    if b != "README.md":
        want[os.path.join(site, b[:-3] + ".html")] = "docs/" + b
for src, out in (("README.md", "readme.html"), ("CONTRIBUTING.md", "contributing.html"),
                 ("CHANGELOG.md", "changelog.html"), ("examples/README.md", "examples.html")):
    if os.path.exists(os.path.join(root, src)):
        want[os.path.join(site, out)] = src
for out, src in want.items():
    if out not in pages:
        bad.append(f"{src} produced no page at {os.path.relpath(out, site)}")
if not os.path.exists(os.path.join(site, "style.css")):
    bad.append("style.css was not written")

checked, dangling = 0, []
for p, t in pages.items():
    here = os.path.relpath(p, site)
    for href in re.findall(r'href="([^"]+)"', t):
        href = html.unescape(href)
        if re.match(r"(https?:|mailto:)", href):
            continue
        checked += 1
        if href.startswith("file://"):
            if not os.path.exists(href[7:].partition("#")[0]):
                dangling.append(f"{here} -> {href}: no such file")
            continue
        path, _, frag = href.partition("#")
        target = p if path == "" else os.path.normpath(os.path.join(os.path.dirname(p), path))
        if path and target not in pages and not os.path.exists(target):
            dangling.append(f"{here} -> {href}: no such file")
        elif frag and target in ids and frag not in ids[target]:
            dangling.append(f"{here} -> {href}: no id {frag!r}")
    for src in re.findall(r'<img src="([^"]+)"', t):
        if re.match(r"(https?:|data:)", src):
            continue
        checked += 1
        if not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(p), src))):
            dangling.append(f"{here} -> image {src}: no such file")
for d in dangling[:30]:
    print("     " + d)
if dangling:
    bad.append(f"{len(dangling)} internal links do not resolve")
if checked < 200:
    bad.append(f"only {checked} internal links checked; the floor is 200")

mods = sorted(glob.glob(os.path.join(root, "stdlib", "**", "*.ax"), recursive=True))
names = 0
for m in mods:
    rel = os.path.relpath(m, os.path.join(root, "stdlib"))
    page = os.path.join(site, "stdlib", rel[:-3] + ".html")
    if page not in pages:
        bad.append(f"stdlib module {rel} has no page")
        continue
    for name in re.findall(r"^\(pub :: ([^\s)]+)", open(m, encoding="utf-8").read(), re.M):
        names += 1
        if name not in ids[page]:
            bad.append(f"{rel}: `{name}` has no anchor on its page")
if len(mods) < 40 or names < 500:
    bad.append(f"{len(mods)} modules and {names} public names found; the floors are 40 and 500")

lexicon = open(os.path.join(root, "self_host", "lexicon.ax"), encoding="utf-8").read()
def block(src, head):
    i = src.index(head)
    return src[i:src.index("\n(pub ::", i + 1)]
words = re.findall(r'\(vecPush v "([^"]+)"\)', block(lexicon, "(pub fn (lexWords)"))
keys = re.findall(r'\(vecPush v "([^"]+)"\)', block(lexicon, "(pub fn (lexTagKeys)"))
heads = [html.unescape(h) for h in re.findall(r"<h3><code>([^<]*)</code></h3>",
                                                pages.get(os.path.join(site, "language.html"), ""))]
for w in words:
    if w not in heads:
        bad.append(f"the language index lacks the word `{w}`")
for k in keys:
    if ";@axiom:" + k not in heads:
        bad.append(f"the language index lacks the AXTAG key `{k}`")
if len(words) < 40 or len(keys) < 5:
    bad.append(f"{len(words)} words and {len(keys)} keys extracted from the lexicon")

print(f"     {len(pages)} pages, {checked} links checked, {len(mods)} modules, "
      f"{names} public names, {len(words)} words, {len(keys)} AXTAG keys")
for b in bad:
    print("     " + b)
sys.exit(1 if bad else 0)
PY_DOC
}

echo "== doc: the documentation set renders, and every link resolves =="
docout="$work/doc"
if ! (cd "$neutral" && AXIOM_DOC="$repo_root" "$work/axc" doc "$repo_root/tests/tools/doc-render.md" \
        --out "$docout" --no-open >"$work/doc.out" 2>"$work/doc.err"); then
  echo "FAIL doc: rendering exited non-zero"
  sed 's/^/     /' "$work/doc.err" | head -5
  failed=$((failed + 1))
elif [[ "$(cat "$work/doc.out")" != "$docout/index.html" ]] || [[ -s "$work/doc.err" ]]; then
  echo "FAIL doc: stdout was '$(cat "$work/doc.out")' (want the index path) and stderr $(wc -c <"$work/doc.err" | tr -d ' ') bytes"
  sed 's/^/     /' "$work/doc.err" | head -5
  failed=$((failed + 1))
elif doc_check "$repo_root" "$docout"; then
  echo "ok   every page, module and public name rendered, and every link resolves"
else
  echo "FAIL doc: a page, a name or a link is missing (listed above)"
  failed=$((failed + 1))
fi

# The negative probe: the link check must see a dangling anchor and a
# link to a file that is not there. The anchor is on the probe's own
# page and the file is not a document, so `check-doc-drift.sh`, which
# reads every `.md` link target this script names, has nothing to
# resolve here.
printf '# Probe\n\nA [bad anchor](#no-such-heading-anywhere) and a\n[bad file](../no-such-dir/x.ax).\n' > "$work/doc-probe.md"
(cd "$neutral" && AXIOM_DOC="$repo_root" "$work/axc" doc "$work/doc-probe.md" \
   --out "$work/doc-probe" --no-open >/dev/null 2>&1)
if doc_check "$repo_root" "$work/doc-probe" >"$work/doc-probe.log" 2>&1; then
  echo "FAIL doc: the link check passed a page with a dangling anchor and a missing file"
  failed=$((failed + 1))
elif ! grep -q 'no-such-heading-anywhere' "$work/doc-probe.log" || ! grep -q 'no-such-dir' "$work/doc-probe.log"; then
  echo "FAIL doc: the link check failed the probe without naming both of its bad links"
  sed 's/^/     /' "$work/doc-probe.log" | tail -4
  failed=$((failed + 1))
else
  echo "ok   the link check names a dangling anchor and a missing file"
fi

echo "== doc: the renderer fixture, against the golden =="
doc_golden="tests/tools/doc-render.golden"
sed -n '/^<main>$/,/^<\/main>$/p' "$docout/doc-render.html" > "$work/doc-render.body"
if [[ "${AXIOM_BLESS:-0}" == 1 ]]; then
  cp "$work/doc-render.body" "$repo_root/$doc_golden"
  echo "blessed $doc_golden"
fi
if [[ ! -f "$doc_golden" ]]; then
  echo "FAIL doc: $doc_golden is missing; run with AXIOM_BLESS=1"
  failed=$((failed + 1))
elif ! cmp -s "$doc_golden" "$work/doc-render.body"; then
  echo "FAIL doc: the fixture's rendering differs from $doc_golden"
  diff "$doc_golden" "$work/doc-render.body" | head -12 | sed 's/^/     /'
  failed=$((failed + 1))
elif [[ ! -f "$docout/assets/doc-render.svg" ]]; then
  echo "FAIL doc: the fixture's image was not copied beside the page"
  failed=$((failed + 1))
elif (( $(grep -c '<span class="' "$doc_golden") < 10 )) || ! grep -q '<table>' "$doc_golden" \
     || ! grep -q '<ol start="3">' "$doc_golden" || ! grep -q 'id="repeated-heading-1"' "$doc_golden"; then
  echo "FAIL doc: the golden lost a construct the fixture exercises (highlighting, a table, a numbered list, a duplicate heading)"
  failed=$((failed + 1))
else
  echo "ok   the fixture renders as the golden says ($(wc -l <"$doc_golden" | tr -d ' ') lines)"
fi

echo "== doc: --missing names exactly the reference's blank summaries =="
(cd "$neutral" && AXIOM_DOC="$repo_root" "$work/axc" doc --missing >"$work/doc.missing" 2>"$work/doc.missing.err"); mrc=$?
# The reference covers one `Sys/Platform` file (check-stdlib-api.sh
# names `darwin`); the other five declare the same names and are left
# out of this comparison for the same reason.
grep -v 'Sys/Platform\.\(baremetal\|freebsd\|linux\|windows\)' "$work/doc.missing" \
  | sed -E 's/.*: //' | sort > "$work/doc.missing.names"
grep '^| `.*| *|$' "$repo_root/docs/stdlib-api.md" | sed -E 's/^\| `([^`]*)`.*/\1/' | sort > "$work/doc.blank.names"
mn=$(grep -c . "$work/doc.missing.names" || true)
if [[ -s "$work/doc.missing.err" ]]; then
  echo "FAIL doc: --missing wrote to stderr"
  sed 's/^/     /' "$work/doc.missing.err" | head -3
  failed=$((failed + 1))
elif ! cmp -s "$work/doc.missing.names" "$work/doc.blank.names"; then
  echo "FAIL doc: --missing and docs/stdlib-api.md disagree about which names have no doc comment"
  diff "$work/doc.blank.names" "$work/doc.missing.names" | head -8 | sed 's/^/     /'
  failed=$((failed + 1))
elif { (( mn > 0 )) && [[ $mrc != 1 ]]; } || { (( mn == 0 )) && [[ $mrc != 0 ]]; }; then
  echo "FAIL doc: --missing listed $mn names and exited $mrc"
  failed=$((failed + 1))
elif ! python3 - "$work/doc.missing" <<'PY_MISS'
import sys
bad = 0
for line in open(sys.argv[1], encoding="utf-8"):
    path, ln, _ = line.rstrip("\n").split(":", 2)
    text = open(path, encoding="utf-8").read().split("\n")[int(ln) - 1]
    if not (text.startswith("(pub ") or text.startswith("(effect ")):
        print(f"     {path}:{ln} is not a public declaration: {text[:50]!r}")
        bad += 1
sys.exit(1 if bad else 0)
PY_MISS
then
  echo "FAIL doc: a --missing line names a line that is not a public declaration"
  failed=$((failed + 1))
else
  echo "ok   --missing names $mn declarations, the same $mn the reference leaves blank, each at its own line"
fi

echo "== doc: refusals =="
mkdir -p "$work/nodocs/bin"
cp "$work/axc" "$work/nodocs/bin/axiom"
(cd "$neutral" && AXIOM_DOC=/nonexistent "$work/nodocs/bin/axiom" doc --out "$work/doc-nd" --no-open \
   >"$work/nd.out" 2>"$work/nd.err"); rc=$?
if [[ $rc == 1 && ! -s "$work/nd.out" && "$(grep -c . "$work/nd.err")" == 1 && ! -d "$work/doc-nd" ]]; then
  ok_nd=1
else
  echo "FAIL doc: AXIOM_DOC=/nonexistent exited $rc with $(wc -c <"$work/nd.out" | tr -d ' ') bytes on stdout and $(grep -c . "$work/nd.err") stderr lines"
  failed=$((failed + 1))
fi
(cd "$neutral" && env -u AXIOM_DOC "$work/nodocs/bin/axiom" doc --out "$work/doc-nd" --no-open \
   >"$work/nd2.out" 2>"$work/nd2.err"); rc=$?
if [[ $rc == 1 && ! -s "$work/nd2.out" && "$(grep -c . "$work/nd2.err")" == 1 ]]; then
  [[ "${ok_nd:-0}" == 1 ]] && echo "ok   no documentation set: exit 1, one stderr line, nothing on stdout, nothing written"
else
  echo "FAIL doc: with no AXIOM_DOC and no docs/ beside the binary, exit $rc with $(grep -c . "$work/nd2.err") stderr lines"
  failed=$((failed + 1))
fi
(cd "$neutral" && AXIOM_DOC="$repo_root" "$work/axc" doc --out /dev/null/axiom-doc --no-open \
   >"$work/uw.out" 2>"$work/uw.err"); rc=$?
if [[ $rc == 1 && ! -s "$work/uw.out" ]] && grep -q 'error:' "$work/uw.err"; then
  echo "ok   an unwritable --out is refused with exit 1"
else
  echo "FAIL doc: --out /dev/null/axiom-doc exited $rc: $(head -1 "$work/uw.err")"
  failed=$((failed + 1))
fi


echo "== source migration: lexical scopes, constructors and embedded inputs =="
if ! python3 "$repo_root/tests/tools/verify-route-b-migration.py"; then
  echo "FAIL source migration: the migration tool changed a checked source property"
  failed=$((failed + 1))
fi

echo
if [[ $failed -eq 0 ]]; then
  echo "tools-selfhost: all checks passed"
else
  echo "tools-selfhost: $failed check(s) failed"
  exit 1
fi
