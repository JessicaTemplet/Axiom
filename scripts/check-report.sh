#!/usr/bin/env bash
# The resource report and the restricted profile (R-D1): what
# `scripts/axiom-report.py` reads off the compiler, what it refuses,
# and the stack bound it computes from machine code.
#
# The tool's docstring is the scope statement of record. This gate
# holds it to five things, and to an ablation for each rule:
#
#   1. The bound algorithm on graphs answered by hand (`--selftest`):
#      a call chain sums, a diamond takes the heavier arm, a tail call
#      replaces its caller's frame, a cycle of tail calls is a loop, a
#      cycle through a call is unbounded, and code with no frame size
#      is unbounded, in whatever order the roots are visited.
#   2. Facts read off `symbols --calls`: the per-function marks of a
#      program with one of everything (allocation, IO, recursion direct
#      and mutual, a call through a parameter, a spawn, an isr),
#      compared exactly, and `#extern`, the compiler's own marker. Then
#      the trap statuses each function may end the process with, for a
#      program with one source of each (division, bounds, a contract, an
#      atomic, an arena reset, an unhandled effect, a shift), compared
#      exactly.
#   3. The profile: tests/profile/ok-*.ax pass with exit 0; each
#      tests/profile/rpN-*.ax is refused with exit 1 by exactly the rule
#      its name gives, and by no other. RP-8 is an interrupt handler that
#      may block, read off the syscall-number constant its body passes.
#      RP-9 is a function holding an `asm` form, admitted by
#      `--allow-asm` only with nostack on every selected arm, and remains
#      a review obligation either way.
#   4. The stack bound, where llc can build an AArch64 ELF object: the
#      conforming program and tests/embedded/blink.ax are bounded under
#      the 8 KiB the baremetal-aarch64 link reserves; tree recursion is
#      unbounded; a 64-byte budget is refused; every reported bound is
#      the sum of the frames on its own path; and the tool's ELF reader
#      agrees with `llvm-readobj --stack-sizes`, an independent parser,
#      on every function's frame. Without llvm-readobj that check is a
#      named SKIP, never a pass. Section 4b asks the same of x86-64.
#   5. Ablations: a copy of the tool with one rule's refusal disabled
#      must stop refusing that rule's fixture, so the fixture is caught
#      by the rule it names. A copy without the call-cycle check, or one
#      that never reads E8 as an x86-64 call, must call tree recursion
#      bounded, so the unbounded verdict is the algorithm's. A copy with
#      no trap leaves must get section 2's trap sets wrong, and one with
#      no blocking kernel entries must pass RP-8's fixture.
#
# What a green run does not show: that a bounded stack is a bounded
# latency (nothing here is a WCET bound), anything about targets other
# than AArch64 and x86-64 ELF for the stack half, or traps the graph has
# no edge for (count exhaustion from emitted retains, stack exhaustion,
# a CPU fault from an Unsafe access), which the report names as
# obligations.
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/gate.sh"
gate_init
gate_build_axc axc

failed=0
checks=0
skipped=0
ok()   { echo "ok   $*"; checks=$((checks + 1)); }
bad()  { echo "FAIL $*"; failed=$((failed + 1)); }
skip() { echo "SKIP $*"; skipped=$((skipped + 1)); }

tool="$repo_root/scripts/axiom-report.py"
fxdir="$repo_root/tests/profile"
blink="$repo_root/tests/embedded/blink.ax"
[[ -f "$tool" ]] || { echo "FAIL: $tool is missing"; exit 1; }
[[ -f "$blink" ]] || { echo "FAIL: $blink is missing"; exit 1; }
nfx=$(ls "$fxdir"/*.ax 2>/dev/null | wc -l | tr -d ' ')
(( nfx >= 7 )) || { echo "FAIL: $fxdir holds $nfx fixtures, fewer than the 7 this gate reads"; exit 1; }

report() {  # report <tool> <out-json> <args...>: exit status of the tool
  local t="$1" out="$2"; shift 2
  python3 "$t" --axiom "$axc" --format json "$@" > "$out" 2> "$out.err"
}
rules_of() {  # the sorted, de-duplicated refusal rules in a JSON report
  python3 -c 'import json,sys; print(" ".join(sorted(set(r["rule"] for r in json.load(open(sys.argv[1]))["refusals"]))))' "$1"
}

echo "== 1. the bound algorithm on graphs answered by hand =="
if python3 "$tool" --selftest > "$work/self.out" 2>&1; then
  ok "selftest: $(tail -1 "$work/self.out")"
else
  bad "selftest:"; sed 's/^/     /' "$work/self.out"
fi

echo
echo "== 2. facts read off the compiler =="
cat > "$work/facts.ax" <<'AX'
(import IO)
(import Vec)

(:: countdown (-> Int Int))
(fn (countdown n)
  (if (<= n 0)
    0
    (countdown (- n 1))))

(:: ping (-> Int Int))
(fn (ping n)
  (if (<= n 0)
    0
    (pong (- n 1))))

(:: pong (-> Int Int))
(fn (pong n)
  (ping n))

(:: apply (-> (-> Int Int) Int Int))
(fn (apply f x)
  (f x))

(:: build (-> Int (Vec Int)))
(fn (build n)
  (let ((v vecNew))
    {
      (vecPush v n)
      v
    }))

;@axiom:isr
(fn (tick)
  0)

; Two effect tags render as two `#effect=` keys, and `unsafe` FIRST here,
; so a reader keeping only the last key would lose the direct-Unsafe mark.
(:: peek (-> Int Int))
;@axiom:effect(unsafe)
;@axiom:effect(io)
(fn (peek a)
  {
    (println "peek")
    (__load64 a 0)
  })

(:: main Int)
;@axiom:effect(io)
;@axiom:effect(spawn)
;@axiom:effect(block)
(fn (main)
  {
    (println "hi")
    (countdown 3)
    (ping 4)
    (apply (lambda (x) (+ x 1)) 2)
    (vecLen (build 3))
    (tick)
    (peek 0)
    (parallel p ((a (+ 1 2)) (b (+ 3 4)))
      (+ a b))
  })
AX
report "$tool" "$work/facts.json" "$work/facts.ax"; rc=$?
if [[ "$rc" != 0 ]]; then
  bad "facts: the tool exited $rc without a profile (only refusals may make it non-zero):"
  head -5 "$work/facts.json.err" | sed 's/^/     /'
else
  # name -> the marks the text renderer prints: A I U X R ~ S K T B
  python3 - "$work/facts.json" > "$work/facts.marks" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
rec = set(q for c in r['cycles'] for q in c)
for q in sorted(r['functions']):
    fa = r['functions'][q]
    print(q, ''.join([
        'A' if fa['alloc'] else '.', 'I' if fa['io'] else '.', 'U' if fa['unsafe'] else '.',
        'X' if fa['extern'] else '.', 'R' if q in rec else '.', '~' if fa['indirect'] else '.',
        'S' if (fa['spawn'] or fa['join']) else '.', 'K' if fa['kernel'] else '.',
        'T' if fa['traps'] else '.', 'B' if fa['blocks'] else '.'])
          + (' isr' if fa['isr'] else ''))
print('roots', ','.join(r['roots']))
PY
  while read -r name want; do
    got="$(grep -E "^$(printf '%s' "$name" | sed 's/[$.]/\\&/g') " "$work/facts.marks" | cut -d' ' -f2-)"
    if [[ "$got" == "$want" ]]; then
      ok "facts: $name $want"
    else
      bad "facts: $name is [$got], wanted [$want]"
    fi
  done <<'ROWS'
countdown ....R.....
ping ....R.....
pong ....R.....
apply .....~....
build A.......T.
tick .......... isr
main AI....S.TB
peek AIU.....TB
Mem$memAlloc A.U.....T.
IO$ioWriteFd AIU....KTB
roots main,tick
ROWS
fi

# Trap statuses, one source of each, compared exactly. `traps` is
# transitive: `main` answers what `dv` and `half` can end the process
# with.
cat > "$work/traps.ax" <<'AX'
(import Vec)

(:: dv (-> Int Int Int))
(fn (dv a b)
  (+ (/ a b) (% a b)))

(:: ix (-> (Vec Int) Int))
(fn (ix v)
  (vecGet v 3))

(:: at (-> Int Int))
;@axiom:effect(unsafe)
(fn (at p)
  (__atomic_load p))

(:: rs (-> Int Int))
;@axiom:effect(unsafe)
(fn (rs m)
  (__axiom_arena_reset m))

;@axiom:pre((> n 0))
(:: half (-> Int Int))
(fn (half n)
  (/ n 2))

(effect Ask (ask :: (-> Int Int)))

;@axiom:effect(ask)
(:: asker Int)
(fn (asker)
  (ask 1))

(:: sh (-> Int Int))
(fn (sh x)
  (<< x 3))

(:: main Int)
(fn (main)
  (+ (dv 1 1) (half 4)))
AX
trapfacts() {  # trapfacts <tool> <out>: `name traps` per root
  report "$1" "$2.json" "$work/traps.ax" --root asker --root at --root rs --root ix --root sh || return 2
  python3 - "$2.json" > "$2" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
for q in ('main', 'dv', 'ix', 'at', 'rs', 'half', 'asker', 'sh'):
    fa = r['functions'][q]
    print(q, ','.join(str(t) for t in fa['traps']) or '-')
PY
}
cat > "$work/traps.want" <<'WANT'
main 72,80,83
dv 72,83
ix 77
at 82
rs 70,75,76
half 72,80,83
asker 71
sh 84
WANT
if trapfacts "$tool" "$work/traps.got" && diff -u "$work/traps.want" "$work/traps.got" > "$work/traps.diff"; then
  ok "traps: eight functions' statuses, exactly"
else
  bad "traps: the statuses differ:"; sed 's/^/     /' "$work/traps.diff" "$work/traps.got.json.err" 2>/dev/null | head -20
fi
# `#extern` is the compiler's own marker: without it an extern row is a
# function with no calls and `#effects=IO`, which is also what a body
# writing one syscall looks like.
"$axc" --diagnostic-format=ai symbols --calls "$fxdir/rp3-foreign.ax" > "$work/ext.sym" 2>&1
if grep -qE '^F add .*#extern' "$work/ext.sym" && ! grep -qE '^F main .*#extern' "$work/ext.sym"; then
  ok "symbols marks the extern item #extern, and only it"
else
  bad "symbols does not mark the extern item alone:"; grep -E '^F (add|main) ' "$work/ext.sym" | sed 's/^/     /'
fi

echo
echo "== 3. the profile: conforming programs pass, each rule refuses its fixture =="
# fixture, extra args, the exact rule set (or 'none')
while IFS='|' read -r fx extra want; do
  [[ -z "$fx" ]] && continue
  # shellcheck disable=SC2086
  report "$tool" "$work/p-$fx.json" "$fxdir/$fx" --profile restricted $extra; rc=$?
  if [[ "$rc" == 2 ]]; then
    bad "$fx: the tool could not answer:"; head -5 "$work/p-$fx.json.err" | sed 's/^/     /'; continue
  fi
  got="$(rules_of "$work/p-$fx.json")"; [[ -z "$got" ]] && got=none
  wrc=1; [[ "$want" == none ]] && wrc=0
  if [[ "$got" == "$want" && "$rc" == "$wrc" ]]; then
    ok "$fx${extra:+ ($extra)}: refusals [$got], exit $rc"
  else
    bad "$fx${extra:+ ($extra)}: refusals [$got] exit $rc, wanted [$want] exit $wrc"
  fi
done <<'ROWS'
ok-periodic.ax||none
rp1-recursion.ax||RP-1
rp2-indirect.ax||RP-2
rp3-foreign.ax||RP-3
rp3-foreign.ax|--allow-foreign add|none
rp3-imported.ax||RP-3
rp3-imported.ax|--allow-foreign add --allow-foreign mods.ForeignLeft$add --allow-foreign mods.ForeignRight$add|none
rp3-imported-bare.ax|--allow-foreign mods.ForeignLeft$add|none
rp3-imported-selective.ax|--allow-foreign mods.ForeignLeft$add|none
rp4-spawn.ax||RP-4
rp4-syscall-spawn.ax||RP-4
rp5-steady.ax|--steady step|RP-5
rp5-steady.ax||none
rp5-selected-root.ax||none
rp5-selected-root.ax|--steady step|RP-5
rp8-selected-root.ax||none
rp8-selected-root.ax|--nonblocking later|RP-8
rp8-selected-root.ax|--nonblocking main|none
rp8-blocking.ax||RP-8
rp8-number.ax|--nonblocking main|RP-8
rp8-kernel-unknown.ax|--nonblocking main|RP-8
rp8-tagged-block.ax|--nonblocking main|RP-8
rp9-asm.ax||RP-9
rp9-asm.ax|--allow-asm spin|none
rp9-stack.ax|--allow-asm pushUnknown --allow-asm erased|RP-9
rp9-multiple.ax|--allow-asm spin|RP-9
rp9-target-arm.ax|--target linux-aarch64 --allow-asm spin|none
rp9-target-arm.ax|--target linux-x86_64 --allow-asm spin|RP-9
ROWS

echo
echo "== 3b. named entries and exceptions fail closed =="
# A named entry must be a source function. Unknown or ineffective options
# cannot produce a successful assurance report.
while IFS='|' read -r fx extra want; do
  [[ -z "$fx" ]] && continue
  # shellcheck disable=SC2086
  report "$tool" "$work/invalid.json" "$fxdir/$fx" $extra; rc=$?
  if [[ "$rc" == 2 ]] && grep -Fq -- "$want" "$work/invalid.json.err"; then
    ok "$extra: exit 2, $want"
  else
    bad "$extra: exit $rc, expected exit 2 with [$want]"
    head -3 "$work/invalid.json.err" | sed 's/^/     /'
  fi
done <<'ROWS'
ok-periodic.ax|--profile restricted --root missing|names no source function
ok-periodic.ax|--profile restricted --steady missing|names no source function
ok-periodic.ax|--profile restricted --nonblocking missing|names no source function
ok-periodic.ax|--profile restricted --root +|names no source function
rp3-foreign.ax|--profile restricted --allow-foreign missing|names no source function
rp3-foreign.ax|--profile restricted --allow-foreign main|with no extern
rp9-asm.ax|--profile restricted --allow-asm missing|names no source function
rp9-asm.ax|--profile restricted --allow-asm main|with no asm
ok-periodic.ax|--stack-budget 8192|requires --stack
ok-periodic.ax|--stack --stack-budget 0|positive byte count
ok-periodic.ax|--stack-root main|requires --stack
ok-periodic.ax|--opt -1|invalid choice
ok-periodic.ax|--opt 4|invalid choice
rp3-foreign.ax|--allow-foreign add|require --profile restricted
rp9-asm.ax|--allow-asm spin|require --profile restricted
ROWS
# Two modules can export the same asm spelling. An exception must
# identify the intended item rather than authorising both at once.
mkdir -p "$work/mods"
for mod in Left Right; do
  cat > "$work/mods/$mod.ax" <<'AX'
(pub :: spin Int)
;@axiom:effect(unsafe)
(pub fn (spin)
  (asm (aarch64 "yield" (options nostack))
       (x86_64 "pause" (options nostack))))
AX
done
cat > "$work/ambiguous.ax" <<'AX'
(import mods.Left)
(import mods.Right)

(:: main Int)
(fn (main)
  (+ (mods.Left::spin) (mods.Right::spin)))
AX
report "$tool" "$work/ambiguous.json" "$work/ambiguous.ax" --profile restricted --allow-asm spin; rc=$?
if [[ "$rc" == 2 ]] && grep -q 'more than one function' "$work/ambiguous.json.err"; then
  ok "a bare exception matching two imported functions is refused"
else
  bad "ambiguous exception: exit $rc, expected 2"
  head -3 "$work/ambiguous.json.err" | sed 's/^/     /'
fi
report "$tool" "$work/qualified.json" "$work/ambiguous.ax" --profile restricted --allow-asm 'mods.Left$spin'; rc=$?
if [[ "$rc" == 1 ]] && python3 - "$work/qualified.json" <<'PYTEST'
import json, sys
r = json.load(open(sys.argv[1]))
assert [(v['rule'], v['path'][-1]) for v in r['refusals']] == [('RP-9', 'mods.Right$spin')]
PYTEST
then
  ok "a qualified exception admits its own item and refuses the other module"
else
  bad "qualified exception: exit $rc, expected one RP-9 for mods.Right\$spin"
  head -3 "$work/qualified.json.err" | sed 's/^/     /'
fi
report "$tool" "$work/qualified-both.json" "$work/ambiguous.ax" --profile restricted --allow-asm 'mods.Left$spin' --allow-asm 'mods.Right$spin'; rc=$?
if [[ "$rc" == 0 && -z "$(rules_of "$work/qualified-both.json")" ]]; then
  ok "two reviewed qualified exceptions admit both asm items"
else
  bad "two qualified exceptions: exit $rc, expected 0"
fi

# Checking unoptimised IR keeps an unknown-stack pure block visible
# even if a later optimiser would discard its unused answer.
report "$tool" "$work/asm-stack.json" "$fxdir/rp9-stack.ax" --profile restricted --allow-asm pushUnknown --allow-asm erased; rc=$?
if [[ "$rc" == 1 ]] && python3 - "$work/asm-stack.json" <<'PYTEST'
import json, sys
r = json.load(open(sys.argv[1]))
assert sorted((v['rule'], v['path'][-1]) for v in r['refusals']) == [('RP-9', 'erased'), ('RP-9', 'pushUnknown')]
assert all('nostack' in v['message'] for v in r['refusals'])
PYTEST
then
  ok "unknown stack use is refused in both side-effecting and removable pure blocks"
else
  bad "unknown asm stack use: exit $rc, expected RP-9 for both blocks"
fi

# Foreign declarations keep their own source and checked type, even
# when their Axiom spellings match. No duplicate row may be hidden by
# the evidence reader, and one exception must never admit another item.
report "$tool" "$work/foreign.json" "$fxdir/rp3-imported.ax" --profile restricted; rc=$?
if [[ "$rc" == 1 ]] && python3 - "$work/foreign.json" <<'PYTEST'
import json, sys
r = json.load(open(sys.argv[1]))
want = {'add', 'mods.ForeignLeft$add', 'mods.ForeignRight$add'}
assert {q for q, f in r['functions'].items() if f['extern']} == want
assert {v['path'][-1] for v in r['refusals'] if v['rule'] == 'RP-3'} == want
assert r['functions']['main']['unsafe']
assert {v['path'][-1] for v in r['obligations'] if v['kind'] == 'foreign'} == want
PYTEST
then
  ok "three same-spelled foreign declarations retain separate evidence identities"
else
  bad "foreign provenance: exit $rc, expected three distinct RP-3 items"
  head -3 "$work/foreign.json.err" | sed 's/^/     /'
fi
report "$tool" "$work/foreign-one.json" "$fxdir/rp3-imported.ax" --profile restricted --allow-foreign add; rc=$?
if [[ "$rc" == 1 ]] && python3 - "$work/foreign-one.json" <<'PYTEST'
import json, sys
r = json.load(open(sys.argv[1]))
assert {v['path'][-1] for v in r['refusals']} == {'mods.ForeignLeft$add', 'mods.ForeignRight$add'}
PYTEST
then
  ok "an exact entry-file exception leaves both imported items refused"
else
  bad "entry-file exception: exit $rc, expected both imported items refused"
fi
"$axc" --diagnostic-format=ai symbols --calls "$fxdir/rp3-imported.ax" > "$work/foreign.axsym"
"$axc" --diagnostic-format=ai symbols --axir "$fxdir/rp3-imported.ax" > "$work/foreign.axir"
if python3 - "$work/foreign.axsym" "$work/foreign.axir" <<'PYTEST'
import re, sys
pat = re.compile(r'^(F \S+ \S+ "[^"]*"(?: @[0-9a-f]+)?)')
def headers(path):
    return [pat.match(l).group(1) for l in open(path) if l.startswith('F ')]
a, b = map(headers, sys.argv[1:])
assert a == b and len(a) == len(set(a))
assert sum(l.startswith('F add ') for l in a) == 3
PYTEST
then
  ok "foreign AXSYM and AXIR headers agree in order, without duplicate evidence"
else
  bad "foreign AXSYM/AXIR evidence identities differ"
fi
# Exercise real linker-symbol selection, rather than only checking the
# compiler's own account of its calls. The three C bodies differ, and
# the entry-file item also has a different checked Axiom type.
cat > "$work/foreign.c" <<'C'
#include <stdint.h>
int64_t assurance_left_add(int64_t a, int64_t b) { return a + b; }
int64_t assurance_right_add(int64_t a, int64_t b) { return a * b; }
int64_t assurance_bool_add(int64_t a, int64_t b) { return a || b; }
C
if cc -c "$work/foreign.c" -o "$work/foreign.o" && ar rcs "$work/libassurance_demo.a" "$work/foreign.o"; then
  for fx in rp3-imported rp3-imported-bare rp3-imported-selective; do
    for level in 0 2; do
      if "$axc" --diagnostic-format=ai build --opt "$level" --link-search "$work" "$fxdir/$fx.ax" -o "$work/$fx" > "$work/$fx.build" 2>&1 && "$work/$fx"; then
        ok "$fx at O$level: native foreign calls reach their declared linker symbols"
      else
        bad "$fx at O$level: foreign call routing or build failed"
        head -3 "$work/$fx.build" | sed 's/^/     /'
      fi
    done
  done
else
  bad "cannot build the foreign-call evidence archive"
fi

# Imported foreign items share the module visibility rules of functions.
# The probe includes native linker controls and refused source programs.
if python3 "$fxdir/verify-foreign-resolution.py" --axiom "$axc" --work "$work" > "$work/foreign-resolution.out" 2>&1; then
  ok "$(tail -1 "$work/foreign-resolution.out"): selective, repeated and transitive imports"
else
  bad "foreign import visibility or provenance differs"
  tail -15 "$work/foreign-resolution.out" | sed 's/^/     /'
fi

# Built-in IO refinements are not unhandled user effects. Handler
# subtraction must also survive the report's own call-graph closure.
for fx in effect-entropy effect-handled rp4-syscall-spawn rp8-tagged-block; do
  report "$tool" "$work/$fx.json" "$fxdir/$fx.ax"
done
if python3 - "$work" <<'PYTEST'
import json, pathlib, sys
base = pathlib.Path(sys.argv[1])
for name in ('effect-entropy', 'effect-handled', 'rp4-syscall-spawn', 'rp8-tagged-block'):
    r = json.loads((base / (name + '.json')).read_text())
    assert 71 not in r['functions']['main']['traps'], name
    if name == 'effect-handled':
        assert r['functions']['asker']['traps'] == [71]
    if name == 'rp8-tagged-block':
        assert r['functions']['main']['blocks']
    if name == 'rp4-syscall-spawn':
        assert r['functions']['main']['spawn_effect']
PYTEST
then
  ok "built-in effect refinements and handled custom effects do not invent trap 71"
else
  bad "trap or blocking facts disagree with the checked effect rows"
fi

echo
echo "== 4. the stack bound, from AArch64 machine code =="
bm=baremetal-aarch64
if ! command -v llc >/dev/null 2>&1 || ! command -v opt >/dev/null 2>&1; then
  skip "llc/opt not on PATH: the stack half cannot build its object here"
else
  for fx in "$fxdir/ok-periodic.ax" "$blink"; do
    n="$(basename "$fx")"
    report "$tool" "$work/s-$n.json" "$fx" --target $bm --profile restricted --stack --stack-budget 8192 --keep; rc=$?
    b="$(python3 -c 'import json,sys; r=json.load(open(sys.argv[1]))["stack"]["results"]["_start"]; print(r["bytes"] if r["bounded"] else "UNBOUNDED")' "$work/s-$n.json" 2>/dev/null)"
    if [[ "$rc" == 0 && "$b" =~ ^[0-9]+$ ]] && (( b > 0 && b <= 8192 )); then
      ok "$n: bounded at $b bytes from _start, under the 8 KiB the link reserves, no refusal"
    else
      bad "$n: exit $rc, bound [$b]:"; head -5 "$work/s-$n.json.err" | sed 's/^/     /'
    fi
  done
  report "$tool" "$work/s-fib.json" "$fxdir/rp1-recursion.ax" --target $bm --profile restricted --stack; rc=$?
  got="$(rules_of "$work/s-fib.json" 2>/dev/null)"
  if [[ "$rc" == 1 && "$got" == "RP-1 RP-7" ]]; then
    ok "rp1-recursion.ax under --stack: tree recursion is unbounded (RP-1 RP-7)"
  else
    bad "rp1-recursion.ax under --stack: exit $rc, refusals [$got], wanted RP-1 RP-7"
  fi
  report "$tool" "$work/s-budget.json" "$blink" --target $bm --profile restricted --stack --stack-budget 64; rc=$?
  if [[ "$rc" == 1 ]] && grep -q 'over the 64-byte budget' "$work/s-budget.json"; then
    ok "blink.ax against a 64-byte budget: refused as RP-7, over budget"
  else
    bad "blink.ax against a 64-byte budget: exit $rc, not refused as over budget"
  fi
  # Self-consistency: every bound is the sum of the frames on its own
  # path. A plain step contributes its frame; a tail step (`f~>`) and a
  # loop member reaching the loop's heaviest member (`f ~(loop)~>`)
  # contribute nothing, because their frame is gone by then.
  for n in ok-periodic.ax blink.ax; do
    if python3 - "$work/s-$n.json" > "$work/sum-$n.out" 2>&1 <<'PY'
import json, sys
st = json.load(open(sys.argv[1]))['stack']
fr = st['frames']
for root, res in st['results'].items():
    if not res['bounded']:
        continue
    total = sum(fr[f] for f in res['path'] if not f.endswith('~>'))
    if total != res['bytes']:
        print('%s: bound %d, but its path sums to %d' % (root, res['bytes'], total))
        sys.exit(1)
    print('%s: %d = %s' % (root, total, ' + '.join('%s(%d)' % (f, fr[f]) for f in res['path'] if not f.endswith('~>'))))
PY
    then
      ok "$n: the bound is the sum of its path's frames - $(head -1 "$work/sum-$n.out" | cut -c1-150)"
    else
      bad "$n: $(cat "$work/sum-$n.out")"
    fi
  done
  # An independent reader of the same object: every frame the tool read
  # from `.stack_sizes` equals what llvm-readobj says, and it read them
  # all. A second ELF parser is the check on the tool's own.
  readobj="$(command -v llvm-readobj || true)"
  if [[ -z "$readobj" ]]; then
    skip "llvm-readobj not on PATH: the frame reader is not cross-checked here"
    for n in ok-periodic.ax blink.ax; do
      rm -rf "$(dirname "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["stack"]["obj"])' "$work/s-$n.json" 2>/dev/null)")" 2>/dev/null
    done
  else
    for n in ok-periodic.ax blink.ax; do
      objf="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["stack"]["obj"])' "$work/s-$n.json")"
      "$readobj" --stack-sizes "$objf" > "$work/ro-$n.txt" 2>&1
      if python3 - "$work/s-$n.json" "$work/ro-$n.txt" > "$work/ro-$n.out" 2>&1 <<'PY'
import json, re, sys
fr = json.load(open(sys.argv[1]))['stack']['frames']
txt = open(sys.argv[2]).read()
theirs = {}
for fn, sz in re.findall(r'Functions: \[([^\]]+)\]\s*\n\s*Size: (0x[0-9a-fA-F]+)', txt):
    theirs[fn] = int(sz, 16)
if not theirs:
    print('llvm-readobj reported no stack sizes'); sys.exit(1)
diff = sorted(set(fr) ^ set(theirs)) + [f for f in fr if f in theirs and fr[f] != theirs[f]]
if diff:
    print('disagree on %s' % diff[:8]); sys.exit(1)
print('%d functions, every frame equal' % len(fr))
PY
      then
        ok "$n: the tool's .stack_sizes reader agrees with llvm-readobj - $(cat "$work/ro-$n.out")"
      else
        bad "$n: the tool's frames and llvm-readobj's disagree - $(cat "$work/ro-$n.out")"
      fi
      rm -rf "$(dirname "$objf")"
    done
  fi
fi

echo
echo "== 4a. every selected root and the full interrupt extent =="
if ! command -v llc >/dev/null 2>&1 || ! command -v opt >/dev/null 2>&1; then
  skip "llc/opt not on PATH: selected-root and IRQ object evidence unavailable"
else
  report "$tool" "$work/selected-stack.json" "$fxdir/rp7-selected-root.ax" --target baremetal-aarch64 --profile restricted --root later --stack; rc=$?
  if [[ "$rc" == 1 && "$(rules_of "$work/selected-stack.json")" == "RP-1 RP-7" ]]; then
    ok "an uncalled selected callback is not cleared through main's stack bound"
  else
    bad "selected callback stack: exit $rc, expected RP-1 RP-7"
  fi
  report "$tool" "$work/extra-stack.json" "$fxdir/rp1-recursion.ax" --target baremetal-aarch64 --profile restricted --stack-root __axiom_mmu_tables --stack; rc=$?
  if [[ "$rc" == 1 && "$(rules_of "$work/extra-stack.json")" == "RP-1 RP-7" ]]; then
    ok "an additional machine root cannot suppress the required program entry"
  else
    bad "additional machine root: exit $rc, expected RP-1 RP-7"
  fi
  report "$tool" "$work/irq.json" "$repo_root/tests/embedded/periodic.ax" --target baremetal-aarch64 --profile restricted --stack --stack-budget 2048 --keep; rc=$?
  if [[ "$rc" == 0 ]] && python3 - "$work/irq.json" > "$work/irq-budget" <<'PYTEST'
import json, sys
st = json.load(open(sys.argv[1]))['stack']
irq = st['irq_stub']
assert irq['frame_bytes'] == st['frames'][irq['entry']] > 0
res = st['results'][irq['entry']]
assert res['bounded'] and res['path'][0] == irq['entry']
assert res['bytes'] == sum(st['frames'][f] for f in res['path'] if not f.endswith('~>'))
assert res['bytes'] >= irq['frame_bytes']
total = st['results']['_start']['bytes'] + res['bytes']
assert 0 < total <= 2048
print(total)
PYTEST
  then
    total="$(cat "$work/irq-budget")"
    ok "the IRQ bound includes the assembled save area and dispatch path ($total bytes with main)"
    report "$tool" "$work/irq-low.json" "$repo_root/tests/embedded/periodic.ax" --target baremetal-aarch64 --profile restricted --stack --stack-budget "$((total - 1))"; rc=$?
    if [[ "$rc" == 1 && "$(rules_of "$work/irq-low.json")" == "RP-7" ]]; then
      ok "one byte below the interrupted-program bound is refused"
    else
      bad "IRQ low budget: exit $rc, expected RP-7"
    fi
    report "$tool" "$work/irq-exact.json" "$repo_root/tests/embedded/periodic.ax" --target baremetal-aarch64 --profile restricted --stack --stack-budget "$total"; rc=$?
    if [[ "$rc" == 0 ]]; then
      ok "the complete interrupted-program bound fits exactly"
    else
      bad "IRQ exact budget: exit $rc, expected 0"
    fi
    if python3 "$repo_root/tests/profile/verify-irq-stack.py" "$tool" "$work/irq.json" > "$work/irq-mutants.out" 2>&1; then
      ok "$(tail -1 "$work/irq-mutants.out")"
    else
      bad "assembled IRQ stack mutants:"
      head -8 "$work/irq-mutants.out" | sed 's/^/     /'
    fi
  else
    bad "the complete IRQ object bound is unavailable (exit $rc)"
    head -5 "$work/irq.json.err" | sed 's/^/     /'
  fi
  python3 - "$work/irq.json" <<'PYTEST'
import json, shutil, sys
r = json.load(open(sys.argv[1]))
if r.get('stack', {}).get('workdir'):
    shutil.rmtree(r['stack']['workdir'])
PYTEST
fi

echo
echo "== 4b. the stack bound, from x86-64 machine code =="
# The same questions of an x86-64 ELF object (linux-x86_64). A call and
# a tail jump are told apart by the opcode byte before the relocated
# displacement, and every frame is charged the 8-byte return address a
# call pushes, which `.stack_sizes` does not count.
x86=linux-x86_64
if ! command -v llc >/dev/null 2>&1 || ! command -v opt >/dev/null 2>&1; then
  skip "llc/opt not on PATH: the x86-64 stack half cannot build its object here"
else
  for fx in "$fxdir/ok-periodic.ax" "$blink"; do
    n="$(basename "$fx")"
    report "$tool" "$work/x-$n.json" "$fx" --target $x86 --stack --stack-budget 8192 --keep; rc=$?
    b="$(python3 -c 'import json,sys; r=json.load(open(sys.argv[1]))["stack"]["results"]["main"]; print(r["bytes"] if r["bounded"] else "UNBOUNDED")' "$work/x-$n.json" 2>/dev/null)"
    if [[ "$rc" == 0 && "$b" =~ ^[0-9]+$ ]] && (( b > 0 && b <= 8192 )); then
      ok "$n on x86-64: bounded at $b bytes from main, no refusal"
    else
      bad "$n on x86-64: exit $rc, bound [$b]:"; head -5 "$work/x-$n.json.err" | sed 's/^/     /'
    fi
    if python3 - "$work/x-$n.json" > "$work/xsum-$n.out" 2>&1 <<'PY'
import json, sys
st = json.load(open(sys.argv[1]))['stack']
fr, extra = st['frames'], st['frame_extra']
if st['machine'] != 'x86-64' or extra != 8:
    print('read as %s with %s extra bytes a frame' % (st['machine'], extra)); sys.exit(1)
res = st['results']['main']
steps = [f for f in res['path'] if not f.endswith('~>')]
total = sum(fr[f] + extra for f in steps)
if total != res['bytes'] or len(steps) < 2:
    print('bound %d, path of %d calls sums to %d' % (res['bytes'], len(steps), total)); sys.exit(1)
print('%d = %d frames + 8 each: %s' % (total, len(steps), ' -> '.join(steps)))
PY
    then
      ok "$n on x86-64: the bound is its path's frames plus a return address each - $(cut -c1-140 "$work/xsum-$n.out")"
    else
      bad "$n on x86-64: $(cat "$work/xsum-$n.out")"
    fi
    readobj="$(command -v llvm-readobj || true)"
    objf="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["stack"]["obj"])' "$work/x-$n.json" 2>/dev/null)"
    if [[ -z "$readobj" ]]; then
      skip "llvm-readobj not on PATH: the x86-64 frame reader is not cross-checked here"
    elif python3 - "$work/x-$n.json" <("$readobj" --stack-sizes "$objf" 2>&1) > "$work/xro-$n.out" 2>&1 <<'PY'
import json, re, sys
fr = json.load(open(sys.argv[1]))['stack']['frames']
theirs = {}
for fns, sz in re.findall(r'Functions: \[([^\]]+)\]\s*\n\s*Size: (0x[0-9a-fA-F]+)', open(sys.argv[2]).read()):
    for fn in fns.split(','):
        theirs[fn.strip()] = int(sz, 16)
diff = sorted(set(fr) ^ set(theirs)) + [f for f in fr if f in theirs and fr[f] != theirs[f]]
if not theirs or diff:
    print('disagree on %s' % diff[:8]); sys.exit(1)
print('%d functions, every frame equal' % len(fr))
PY
    then
      ok "$n on x86-64: the frames agree with llvm-readobj - $(cat "$work/xro-$n.out")"
    else
      bad "$n on x86-64: the frames and llvm-readobj's disagree - $(cat "$work/xro-$n.out")"
    fi
    [[ -n "$objf" ]] && rm -rf "$(dirname "$objf")"
  done
  report "$tool" "$work/x-fib.json" "$fxdir/rp1-recursion.ax" --target $x86 --profile restricted --stack; rc=$?
  got="$(rules_of "$work/x-fib.json" 2>/dev/null)"
  if [[ "$rc" == 1 && "$got" == "RP-1 RP-7" ]]; then
    ok "rp1-recursion.ax on x86-64: tree recursion is unbounded (RP-1 RP-7)"
  else
    bad "rp1-recursion.ax on x86-64: exit $rc, refusals [$got], wanted RP-1 RP-7"
  fi
fi

echo
echo "== 5. ablations: each rule is what refuses its fixture =="
# A copy of the tool with one site disabled. The seam is an exact
# string and must match exactly once, or the ablation proves nothing.
ablate() {  # ablate <name> <from> <to>: writes $work/abl-<name>.py
  python3 - "$tool" "$work/abl-$1.py" "$2" "$3" <<'PY'
import sys
src, dst, a, b = sys.argv[1:5]
s = open(src).read()
n = s.count(a)
if n != 1:
    print('seam [%s] matches %d times' % (a, n)); sys.exit(1)
open(dst, 'w').write(s.replace(a, b))
PY
}
while IFS='|' read -r rule fx extra; do
  [[ -z "$rule" ]] && continue
  if ! ablate "$rule" "refuse('$rule'," "(lambda *a: None)('$rule',"; then
    bad "ablation $rule: the seam did not match exactly once"; continue
  fi
  # shellcheck disable=SC2086
  report "$work/abl-$rule.py" "$work/a-$rule.json" "$fxdir/$fx" --profile restricted $extra; rc=$?
  got="$(rules_of "$work/a-$rule.json" 2>/dev/null)"
  if [[ "$rc" == 0 && -z "$got" ]]; then
    ok "ablation $rule: with its refusal disabled, $fx passes - the fixture is caught by $rule and nothing else"
  else
    bad "ablation $rule: with its refusal disabled, $fx still exits $rc with [$got]"
  fi
done <<'ROWS'
RP-1|rp1-recursion.ax|
RP-2|rp2-indirect.ax|
RP-3|rp3-foreign.ax|
RP-4|rp4-spawn.ax|
RP-5|rp5-steady.ax|--steady step
RP-8|rp8-blocking.ax|
RP-9|rp9-asm.ax|
ROWS
if ablate selected-roots "for flag in ('root', 'steady', 'nonblocking'):" "for flag in ('root',):"; then
  report "$work/abl-selected-roots.py" "$work/a-selected.json" "$fxdir/rp8-selected-root.ax" --profile restricted --nonblocking later; rc=$?
  if [[ "$rc" == 0 && -z "$(rules_of "$work/a-selected.json")" ]]; then
    ok "ablation selected roots: omitting the selected entry misses its blocking helper"
  else
    bad "ablation selected roots: selected blocking entry still exits $rc"
  fi
else
  bad "ablation selected roots: the seam did not match exactly once"
fi
if ablate asm-stack "nostack = not re.search(r'\\balignstack\\b', m.group(1))" "nostack = True"; then
  report "$work/abl-asm-stack.py" "$work/a-asm-stack.json" "$fxdir/rp9-multiple.ax" --profile restricted --allow-asm spin; rc=$?
  if [[ "$rc" == 0 && -z "$(rules_of "$work/a-asm-stack.json")" ]]; then
    ok "ablation asm stack: ignoring the emitted stack claim admits an unknown-stack arm"
  else
    bad "ablation asm stack: unknown-stack arm still exits $rc"
  fi
else
  bad "ablation asm stack: the seam did not match exactly once"
fi
if ablate spawn-effect "if fa['spawn_effect'] and not concurrent and not any(" "if False and not any("; then
  report "$work/abl-spawn-effect.py" "$work/a-spawn-effect.json" "$fxdir/rp4-syscall-spawn.ax" --profile restricted; rc=$?
  if [[ "$rc" == 0 && -z "$(rules_of "$work/a-spawn-effect.json")" ]]; then
    ok "ablation Spawn: ignoring the checked effect misses raw process creation"
  else
    bad "ablation Spawn: raw process creation still exits $rc"
  fi
else
  bad "ablation Spawn: the seam did not match exactly once"
fi
if ablate block-effect "if 'Block' in f.effects and not any(" "if False and not any("; then
  report "$work/abl-block-effect.py" "$work/a-block-effect.json" "$fxdir/rp8-tagged-block.ax" --profile restricted --nonblocking main; rc=$?
  if [[ "$rc" == 0 && -z "$(rules_of "$work/a-block-effect.json")" ]]; then
    ok "ablation Block: ignoring the checked effect misses an unfamiliar wait binding"
  else
    bad "ablation Block: unfamiliar wait binding still exits $rc"
  fi
else
  bad "ablation Block: the seam did not match exactly once"
fi
if ablate kernel-block "if JOIN_RE.match(l) or KERNEL_RE.match(l)]" "if JOIN_RE.match(l)]"; then
  report "$work/abl-kernel-block.py" "$work/a-kernel-block.json" "$fxdir/rp8-kernel-unknown.ax" --profile restricted --nonblocking main; rc=$?
  if [[ "$rc" == 0 && -z "$(rules_of "$work/a-kernel-block.json")" ]]; then
    ok "ablation kernel blocking: ignoring unknown kernel entries grants false clearance"
  else
    bad "ablation kernel blocking: unknown kernel entry still exits $rc"
  fi
else
  bad "ablation kernel blocking: the seam did not match exactly once"
fi
if ablate handled-effect "[t for t in facts[q]['trap_direct'] if t != 71]" "facts[q]['trap_direct']"; then
  report "$work/abl-handled-effect.py" "$work/a-handled-effect.json" "$fxdir/effect-handled.ax"
  if python3 - "$work/a-handled-effect.json" <<'PYTEST'
import json, sys
r = json.load(open(sys.argv[1]))
assert 71 in r['functions']['main']['traps']
PYTEST
  then
    ok "ablation handling: blindly closing trap 71 over calls undoes the handler"
  else
    bad "ablation handling: handler trap facts did not change"
  fi
else
  bad "ablation handling: the seam did not match exactly once"
fi
if ablate selected-machine-roots "machine_roots += [q for q in roots if q != 'main' and q not in machine_roots]" "machine_roots += [q for q in isrs if q not in machine_roots]"; then
  if command -v llc >/dev/null 2>&1 && command -v opt >/dev/null 2>&1; then
    report "$work/abl-selected-machine-roots.py" "$work/a-selected-machine.json" "$fxdir/rp7-selected-root.ax" --target baremetal-aarch64 --profile restricted --root later --stack; rc=$?
    if [[ "$rc" == 0 ]]; then
      ok "ablation machine roots: an omitted callback receives false recursion clearance"
    else
      bad "ablation machine roots: omitted callback still exits $rc"
    fi
  fi
else
  bad "ablation machine roots: the seam did not match exactly once"
fi
if ablate irq-frame "frames[IRQ_STACK_ENTRY] = irq_stub['frame_bytes']" "frames[IRQ_STACK_ENTRY] = 0"; then
  if [[ -s "$work/irq-budget" ]]; then
    report "$work/abl-irq-frame.py" "$work/a-irq-frame.json" "$repo_root/tests/embedded/periodic.ax" --target baremetal-aarch64 --profile restricted --stack --stack-budget "$(( $(cat "$work/irq-budget") - 1 ))"; rc=$?
    if [[ "$rc" == 0 ]]; then
      ok "ablation IRQ frame: omitting the save area falsely admits the low budget"
    else
      bad "ablation IRQ frame: low budget still exits $rc without the save area"
    fi
  fi
else
  bad "ablation IRQ frame: the seam did not match exactly once"
fi
# With no trap leaves the exact statuses above must come out wrong, and
# with no blocking kernel entry RP-8's fixture must pass. Each shows the
# fact is read off the graph.
if ablate traps "TRAP_LEAVES = {" "TRAP_LEAVES = {} and {"; then
  if trapfacts "$work/abl-traps.py" "$work/a-traps.got" && ! diff -q "$work/traps.want" "$work/a-traps.got" >/dev/null; then
    ok "ablation traps: with no trap leaves, $(diff "$work/traps.want" "$work/a-traps.got" | grep -c '^>') rows come out wrong"
  else
    bad "ablation traps: the trap statuses are unchanged with no trap leaves"
  fi
else
  bad "ablation traps: the seam did not match exactly once"
fi
if ablate blocking "BLOCKING_KERNEL = {" "BLOCKING_KERNEL = set() and {"; then
  report "$work/abl-blocking.py" "$work/a-blocking.json" "$fxdir/rp8-number.ax" --profile restricted --nonblocking main; rc=$?
  if [[ "$rc" == 0 && -z "$(rules_of "$work/a-blocking.json")" ]]; then
    ok "ablation blocking: with no blocking kernel entry, rp8-number.ax passes - RP-8 reads the syscall its body names"
  else
    bad "ablation blocking: rp8-number.ax still exits $rc with no blocking kernel entry"
  fi
else
  bad "ablation blocking: the seam did not match exactly once"
fi
# x86-64 calls are read off the opcode byte: a copy that never reads
# E8 as a call loses every call edge, and tree recursion comes out
# bounded, so 4b's unbounded verdict is the opcode reading's.
if ablate x86call "                if op == 0xE8:" "                if False:"; then
  if command -v llc >/dev/null 2>&1 && command -v opt >/dev/null 2>&1; then
    report "$work/abl-x86call.py" "$work/a-x86call.json" "$fxdir/rp1-recursion.ax" --target linux-x86_64 --stack
    b="$(python3 -c 'import json,sys; r=json.load(open(sys.argv[1]))["stack"]["results"]["main"]; print(r["bounded"])' "$work/a-x86call.json" 2>/dev/null)"
    if [[ "$b" == "True" ]]; then
      ok "ablation x86call: with E8 not read as a call, tree recursion comes out BOUNDED - 4b reads the calls"
    else
      bad "ablation x86call: tree recursion is [$b] without the E8 reading"
    fi
  fi
else
  bad "ablation x86call: the seam did not match exactly once"
fi
# A copy that ignores a call edge inside a cycle, treating recursion as
# if it held no frame, must fail the selftest and call tree recursion
# bounded. Then section 4's unbounded verdict is the algorithm's.
if ablate cycle "elif any(g in cs for g in calls.get(f, ())):" "elif False:"; then
  if python3 "$work/abl-cycle.py" --selftest > "$work/abl-self.out" 2>&1; then
    bad "ablation cycle: the selftest still passes with the call-cycle check removed"
  else
    ok "ablation cycle: the selftest fails - $(grep -c '^FAIL' "$work/abl-self.out") case(s) wrong without the check"
  fi
  if command -v llc >/dev/null 2>&1 && command -v opt >/dev/null 2>&1; then
    report "$work/abl-cycle.py" "$work/a-cycle.json" "$fxdir/rp1-recursion.ax" --target baremetal-aarch64 --stack
    b="$(python3 -c 'import json,sys; r=json.load(open(sys.argv[1]))["stack"]["results"]["_start"]; print(r["bounded"])' "$work/a-cycle.json" 2>/dev/null)"
    if [[ "$b" == "True" ]]; then
      ok "ablation cycle: tree recursion comes out BOUNDED without the check - section 4 is the check's verdict"
    else
      bad "ablation cycle: tree recursion is [$b] without the check; section 4's verdict is not the algorithm's"
    fi
  fi
else
  bad "ablation cycle: the seam did not match exactly once"
fi

echo
if (( failed > 0 )); then
  echo "check-report: $failed of $((checks + failed)) checks failed ($skipped skipped)"
  exit 1
fi
echo "check-report: $checks checks passed, $skipped skipped"
