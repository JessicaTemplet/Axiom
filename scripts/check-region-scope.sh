#!/usr/bin/env bash
# `(region r body)` is a CHECKED SCOPE - S2 of
# docs/memory-model.md §3.6 - and this is what holds it.
#
# WHAT A REGION IS, one sentence each way. The allocator's waterline
# is read when the body starts and rolled back when it ends, so
# everything the body allocated is reclaimed in one pointer move:
# MM-RGN-1 over the runtime MM-ALLOC-15/16 already specify, with the
# mark cell on the STACK (`emitRegion`, self_host/codegen.ax) so that a
# region per loop iteration leaks nothing. And the checker refuses the
# two ways a reference could leave one that need no region TYPES to
# see - the region's own value, and a `set` on a binding bound outside
# it - as AX3059, with a nested region reusing an open name refused as
# AX3058 (MM-RGN-2). Everything else a reference can do is
# MM-ALLOC-16's program obligation until S3, and the checker's header
# says so.
#
# Four checks. The fourth is the ABLATION, which is this repository's
# rule for a refusal: show what accepting the program does.
#
#   1. A PROGRAM THAT WRITES NO `region` EMITS NO REGION. `emit-llvm
#      self_host/main.ax` - the largest program there is, and one that
#      writes no region - contains no region cell. The claim this
#      stands for is MM-RGN-4's: a program with no region is the
#      one-region instance and emits what it emitted before. That was
#      measured once, against the compiler built from the commit before
#      this landed, byte for byte (the CHANGELOG entry carries the
#      number). A gate cannot hold that against a compiler it does not
#      have, so it holds the MECHANISM: the cell is emitted only by
#      `emitRegion`, which only a `region` node reaches - and the
#      positive half beside it, a program with two nested regions
#      emits exactly two cells, so the count is not zero because the
#      grep stopped matching.
#
#      The cell has two widths (`emitRegionCell`): three words, or
#      four in a module with the owner registry, whose fourth word is
#      the registry's counter (MM-EXEC-20). The registry is on in a
#      module that makes resource owners and resets the arena
#      (`resRegistryWanted`). `self_host` is one, and so is any
#      program with a region that imports IO, whose `File` is an
#      owner. So the negative counts both widths, and the positive
#      half is two probes, one per width, each also asserting the
#      registry state that picks its width: a grep for one width
#      alone reads zero in a module that emits the other.
#
#   2. RECLAIM, AS A RATIO. One program, two spellings one word apart:
#      a region that allocates and fills 64 KiB, four thousand times,
#      and the same body with `region r` deleted. Without the reset
#      the arena grows by 256 MiB of touched pages; with it the
#      waterline never moves. Peak RSS of the second must be at least
#      8x the first. A ratio and not a bound, so a loaded runner
#      cannot fail it - `bench-compile.sh`'s rule, and every timing
#      gate here follows it.
#
#   3. THE REFUSALS COUNT. tests/diagnostics/631-region-escape.ax draws
#      exactly three AX3059 rows and 630 exactly one AX3058, and the
#      silent shapes beside them draw nothing. `check-diagnostics.sh`
#      holds the bytes; this holds the COUNTS, because a fixture whose
#      refusal was ablated still has a golden that could be re-blessed
#      to agree with the silence.
#
#   4. THE ABLATION. The tree is copied, `rgTyScalar` in
#      self_host/typecheck.ax is made to answer 1 for every type - the
#      refusal still exists, still runs, and never fires - and S3's
#      region-form reporting is silenced the same way (its report
#      flag back to 0 on the region-form branch, the rule still
#      running beside the stamp walk and answering nothing). Both
#      blinds are needed since the callee-mediated fix: S2's
#      blindness alone no longer accepts the probe, because S3 sees
#      the store through the facts rather than the scalar test - and
#      a compiler is built from it. The program check 3 refuses then
#      compiles, runs, and prints the NEXT allocation's bytes where it
#      meant to print its own: `hello world`, stored into an outer
#      binding from inside a region and read after a fresh string of
#      the same length was built, comes back as that fresh string. The
#      descriptor lands at the same address because the waterline was
#      rolled back to exactly where the region began. That is §1.4's
#      "reading freed memory", measured, and it is what AX3059 stands
#      between a program and.
#
# Cost: one extra compiler build for check 4, the price
# `check-static-release.sh` pays for the same reason.
#
# Usage:
#   scripts/check-region-scope.sh
#   AXIOM=path/to/compiler scripts/check-region-scope.sh

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/gate.sh"
gate_init
command -v python3 >/dev/null || { echo "FAIL: python3 is not on PATH"; exit 1; }

failed=0
checks=0
ok()  { echo "ok   $*"; checks=$((checks + 1)); }
bad() { echo "FAIL $*"; failed=$((failed + 1)); }

gate_build_axc axc

# `measure-memory-baseline.sh`'s reader, and its rule: fail rather
# than skip when neither `time` answers.
# (`max_rss_kb` itself is defined once, in scripts/lib/gate.sh.)

# A region's mark cell in its two widths (`emitRegionCell`), and the
# registry global whose presence picks the four-word one (MM-EXEC-20).
# No other alloca has either shape: a recovery record is sixteen words
# or more.
CELL3='alloca i64, i64 3, align 16'
CELL4='alloca i64, i64 4, align 16'
REGISTRY='^@__axiom_res_seq = '

onoff() { if [[ "$1" -eq 1 ]]; then echo on; else echo off; fi; }

# ---------------------------------------------------------------
# 1. no region, no cell - and two regions, two cells, in each width
# ---------------------------------------------------------------
echo "== 1. a program that writes no region emits no region cell =="
checks=$((checks + 1))
if ! "$axc" emit-llvm "$repo_root/self_host/main.ax" -o "$work/self.ll" >"$work/self.log" 2>&1; then
  bad "could not emit self_host/main.ax"
  sed 's/^/     /' "$work/self.log" | head -20
else
  c3=$(grep -c "$CELL3" "$work/self.ll" || true)
  c4=$(grep -c "$CELL4" "$work/self.ll" || true)
  reg=$(grep -c "$REGISTRY" "$work/self.ll" || true)
  lines=$(wc -l < "$work/self.ll" | tr -d ' ')
  if [[ $((c3 + c4)) -eq 0 ]]; then
    ok "self_host/main.ax: 0 region cells of either width in $lines lines of IR (owner registry $(onoff "$reg"))"
  else
    bad "self_host/main.ax emits $c3 three-word and $c4 four-word region cell(s) and writes no region"
  fi
fi

# One program, two nested regions, emitted twice. With no import
# nothing makes an owner, so the registry is off and each cell is three
# words. Importing IO brings `File`, an owner, so the registry is on
# and each cell is four.
two_regions() {  # <name> <import line> <width: 3|4> <registry: 0|1>
  local name="$1" import="$2" width="$3" want_reg="$4"
  local word want_cell other_cell cells others reg resets
  if [[ "$width" -eq 4 ]]; then
    word=four; want_cell="$CELL4"; other_cell="$CELL3"
  else
    word=three; want_cell="$CELL3"; other_cell="$CELL4"
  fi
  printf '%s\n\n(:: main Int)\n\n(fn (main) (region a (region b 0)))\n' \
    "$import" > "$work/$name.ax"
  checks=$((checks + 1))
  if ! "$axc" emit-llvm "$work/$name.ax" -o "$work/$name.ll" >"$work/$name.log" 2>&1; then
    bad "could not emit the two-region probe for the $word-word cell"
    sed 's/^/     /' "$work/$name.log" | head -20
    return
  fi
  cells=$(grep -c "$want_cell" "$work/$name.ll" || true)
  others=$(grep -c "$other_cell" "$work/$name.ll" || true)
  reg=$(grep -c "$REGISTRY" "$work/$name.ll" || true)
  resets=$(grep -c 'call i64 @__axiom_arena_reset_fn' "$work/$name.ll" || true)
  # the runtime's own callers of the reset (the keep helper, recovery)
  # are in every binary; two more is the two regions
  if [[ "$reg" -ne "$want_reg" ]]; then
    bad "the $word-word probe has the owner registry $(onoff "$reg") - it no longer picks the width it stands for"
  elif [[ "$cells" -eq 2 && "$others" -eq 0 ]]; then
    ok "two nested regions, owner registry $(onoff "$reg"): 2 $word-word cells, $resets reset calls in the module"
  else
    bad "two nested regions emitted $cells $word-word and $others other-width cell(s), wanted 2 and 0 - the grep above is not measuring the cell"
  fi
}
two_regions two3 "" 3 0
two_regions two4 "(import IO)" 4 1

# ---------------------------------------------------------------
# 2. reclaim, as a ratio
# ---------------------------------------------------------------
echo "== 2. the region's reset keeps the arena flat: RSS ratio =="
probe() {  # <open> <close> -> a program on stdout
  cat <<EOF
(import IO)

(import Mem)

(:: burn (-> Int Int))

(fn (burn n)
  (if (<= n 0)
    0
    {
      $1 (memSet (memAlloc 65536) 7 65536) $2
      (burn (- n 1))
    }
  )
)

(:: main Int)

(fn (main) (burn 4000))
EOF
}
probe "(region r" ")" > "$work/with.ax"
probe "{" "}" > "$work/without.ax"
checks=$((checks + 1))
if ! "$axc" build --input "$work/with.ax" --output "$work/with" >"$work/with.build" 2>&1; then
  bad "could not build the region probe"; sed 's/^/     /' "$work/with.build" | head -20
elif ! "$axc" build --input "$work/without.ax" --output "$work/without" >"$work/without.build" 2>&1; then
  bad "could not build the control"; sed 's/^/     /' "$work/without.build" | head -20
else
  rss_with=$(max_rss_kb "$work/with") || rss_with=""
  rss_without=$(max_rss_kb "$work/without") || rss_without=""
  if [[ -z "$rss_with" || -z "$rss_without" || "$rss_with" -le 0 ]]; then
    bad "could not measure peak RSS (with=$rss_with without=$rss_without)"
  else
    ratio=$(( rss_without / rss_with ))
    if [[ "$ratio" -ge 8 ]]; then
      ok "4000 x 64 KiB: ${rss_with} KiB with the region, ${rss_without} KiB without - ${ratio}x"
    else
      bad "4000 x 64 KiB: ${rss_with} KiB with the region against ${rss_without} KiB without - ${ratio}x, the floor is 8x"
    fi
  fi
fi

# ---------------------------------------------------------------
# 3. the refusals, counted
# ---------------------------------------------------------------
echo "== 3. the fixtures draw exactly the rows their headers promise =="
count_code() {  # <file> <code> -> rows
  "$axc" check "$1" 2>&1 | grep -c "error\[$2\]" || true
}
checks=$((checks + 1))
n59=$(count_code "$repo_root/tests/diagnostics/631-region-escape.ax" AX3059)
n58=$(count_code "$repo_root/tests/diagnostics/630-region-name-shadowed.ax" AX3058)
if [[ "$n59" -eq 3 && "$n58" -eq 1 ]]; then
  ok "631 draws 3 AX3059 rows, 630 draws 1 AX3058 row"
else
  bad "631 draws $n59 AX3059 row(s) (wanted 3); 630 draws $n58 AX3058 row(s) (wanted 1)"
fi
checks=$((checks + 1))
if "$axc" check "$repo_root/tests/stdlib/168-region.ax" >"$work/168.log" 2>&1; then
  ok "168-region.ax, every silent shape, checks clean"
else
  bad "168-region.ax does not check"; sed 's/^/     /' "$work/168.log" | head -20
fi

# ---------------------------------------------------------------
# 4. the ablation: accept everything, and read freed memory
# ---------------------------------------------------------------
echo "== 4. ablation: both guards blinded lets a program read freed memory =="
cat > "$work/esc.ax" <<'EOF'
(import IO)

(import Str)

(:: main Int)

;@axiom:effect(io)
(fn (main)
  { (let mut out "")
    {
      (region r
        {
          (set out (concat "hello" " world"))
          0
        })
      { (let fresh (concat "XXXXX" "XXXXXX"))
        {
          (println out)
          (strLen fresh)
        }}
    }
  }
)
EOF
checks=$((checks + 1))
if "$axc" check "$work/esc.ax" >"$work/esc.check" 2>&1; then
  bad "the tree's compiler ACCEPTED the escape probe; nothing below can mean anything"
elif ! grep -q 'error\[AX3059\]' "$work/esc.check"; then
  bad "the tree's compiler refused the escape probe for some other reason:"
  sed 's/^/     /' "$work/esc.check" | head -10
else
  ok "the tree's compiler refuses the probe with AX3059"
fi

abl="$work/tree"
mkdir -p "$abl"
cp -R "$repo_root/self_host" "$repo_root/stdlib" "$abl/" || {
  echo "FAIL: could not copy the tree to ablate" >&2; exit 1; }
python3 - "$abl/self_host/typecheck.ax" <<'PY'
import sys, re
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
head = "(pub fn (rgTyScalar tc ty0)\n"
i = s.find(head)
if i < 0 or s.count(head) != 1:
    sys.exit("the ablation matched %d times, wanted 1" % s.count(head))
# the function body is one balanced form after the head line
j = i + len(head)
depth = 1  # the `(pub fn` paren is open
k = j
while depth > 0:
    c = s[k]
    if c == "(": depth += 1
    elif c == ")": depth -= 1
    k += 1
new = head + "  1\n)\n"
open(p, "w", encoding="utf-8").write(s[:i] + new + s[k:].lstrip("\n"))
# Second blind, same discipline: S3's region-form reporting would
# refuse the probe through the facts (AX3060) with S2's scalar test
# blinded, so the ablation silences that branch's report flag too -
# the walk still runs, still stamps, and diagnoses nothing. Anchored
# on the tc39 branch. Read fresh: the first splice moved every index after
# it, so offsets computed before the re-read would land mid-line.
s = open(p, encoding="utf-8").read()
branch = "(if (== (memGetWord tc 39) 1)"
b = s.find(branch)
if b < 0:
    sys.exit("the tc39 branch moved, wanted 1")
call = "(rgnPass tc (memGetWordVec tc 10) 0 1)"
c = s.find(call, b)
if c < 0:
    sys.exit("the tc39 report-1 walk moved, wanted 1")
s = s[:c] + "(rgnPass tc (memGetWordVec tc 10) 0 0)" + s[c+len(call):]
open(p, "w", encoding="utf-8").write(s)
PY
checks=$((checks + 1))
if [[ $? -ne 0 ]]; then
  bad "could not apply the ablation - rgTyScalar has moved, and this gate is asserting nothing"
elif ! gate_build_tree "$axc" "$abl" "$abl/stdlib" \
     "$work/axc-ablated" >"$work/ablated.build.log" 2>&1; then
  bad "the ablated compiler did not build"
  sed 's/^/     /' "$work/ablated.build.log" | head -20
elif ! AXIOM_STDLIB="$repo_root/stdlib" "$work/axc-ablated" build --input "$work/esc.ax" \
     --output "$work/esc" >"$work/esc.build" 2>&1; then
  bad "the ablated compiler did not accept the escape probe - the ablation is not what it says"
  sed 's/^/     /' "$work/esc.build" | head -20
else
  got="$("$work/esc" 2>/dev/null | head -1)"
  if [[ "$got" == "hello world" ]]; then
    bad "ablated: the probe printed 'hello world' - the store survived the reset, and AX3059 is refusing something harmless"
  else
    ok "ablated: the probe printed '$got' where it stored 'hello world' - the outer binding read the next allocation's bytes"
  fi
fi

echo
if [[ $failed -eq 0 ]]; then
  echo "check-region-scope: $checks checks, 0 failed"
  exit 0
fi
echo "check-region-scope: $checks checks, $failed failed"
exit 1
