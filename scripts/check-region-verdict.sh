#!/usr/bin/env bash
# S4's success criterion, run at last (docs/memory-model.md
# §4, the S4 table row): "re-run §1.1's ablation and expect the binary
# win with the RSS win intact - the one measurement that decides
# whether any of this was worth it". Slices 1-5 each pinned their own
# delta, answers and RSS; this gate pins the END-TO-END property no
# slice gate can: the whole S4 elision, on at once, buys binary bytes
# and costs no memory.
#
# WHY FIXTURES, NOT THE COMPILER ITSELF, FOR THE REGION HALF. §1.1's
# ablation ran on the compiler's own IR because the traffic it deleted
# was everywhere. Compiler source has no explicit region form, and
# Task's imported region changes no region release in this workload.
# The slice gates hold the compiler IR identical with region elisions
# on or off,
# and a verdict run there would assert a zero and prove nothing. The
# verdict runs where the traffic is: the six S4 fixtures, each under
# the compiler under test against ONE fully-ablated compiler with
# every S4 spend off at once.
#
# THE FULL ABLATION IS THREE RULE-KILLS: slice 1's depth guard (the
# reclaim gate's own anchor), the stamp (`rgnStamping`, TC word 40,
# never set, so slices 2-4 stamp nothing anywhere), and the
# static-sentinel answer (the static-release gate's own anchor). Each
# slice gate ablates only its own spend and pins the delta on its
# fixture; here every spend is off at once, so each fixture's delta
# is its OWN traffic plus the other slices' cross-traffic in the same
# module, the stdlib it prints through included - new pins
# (17/22/20/23/20/25), each above its slice's, each decomposed at the
# pin list below. A seventh probe carries slice 5's four literal
# positions with deterministic stdout (its gate's probe answers a
# pointer cast, which ASLR moves).
#
# THREE WORKLOADS, §1.1's three columns (peak RSS, binary, output):
#
#   W1. THE §1.1 WORKLOAD ITSELF. Both compilers emit
#       `self_host/main.ax`: the ablated IR carries thousands more
#       releases, every restored one on a `@strhdr_*` literal (the
#       region kills are inert in this workload), the IR
#       diff is release traffic and dependent ownership guards. The
#       emitting compiler's own peak RSS holds its ratio. The elisions delete
#       calls that free nothing, so the emit cannot cost memory.
#   W2. THE SIX FIXTURES PLUS THE LITERAL PROBE. Per program: the
#       release delta equals the pin, the IR diff is release lines
#       only, both binaries answer identically (stdout and exit), and
#       the verdict binary's CODE is smaller - the text section
#       (`__text` / `.text`, read by `llvm-size -A`), one per arm.
#
#       NOT `wc -c`, although §1.1 measured file bytes and this gate
#       did until 2026-09-27. A file carries linker tables that do not
#       move with the code, and on darwin they moved the other way:
#       at 99bd5415, 483's `__text` was 8,552 bytes under test against
#       8,840 ablated - 288 bytes of removed release traffic - while
#       the file was 16 bytes LARGER, because LC_FUNCTION_STARTS (a
#       ULEB table of function-start deltas, padded to 8) went 64 -> 72
#       bytes and the code signature tracks the file's own size. The
#       gate said "the elision grew the backend" about a backend that
#       had shrunk. File bytes are still printed; they are not asserted.
#   W3. ONE RSS LOOP, COMBINED TRAFFIC. 300,000 regions, each routing
#       a fresh box through a call (slice 2's path), a construction
#       (slice 1's) and a literal touch (slice 5's): same answer both
#       ways, peak RSS within 1.5x. The slice gates pin each path's
#       RSS alone; this pins them together, where a load-bearing
#       release dropped by mistake would leak ~10 MiB no noise hides.
#
# What this gate does NOT assert, stated rather than left to be
# found: no wall-clock claim (§1.1's own correction stands - load
# moves it, and this gate runs on loaded runners), and no absolute
# code size (toolchains move it; the DIRECTION is the assertion).
# Per file the direction is must-not-grow, because function alignment
# can absorb a few removed calls into padding (three fixtures tied to
# the byte when this measured whole files); the strict win is pinned
# on the seven files' aggregate, where padding cannot absorb every
# file at once.
#
# Cost: one extra compiler build plus two emits of `self_host`, the
# same shape every slice gate already pays once.
#
# Usage:
#   scripts/check-region-verdict.sh
#   AXIOM=path/to/compiler scripts/check-region-verdict.sh

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/gate.sh"
gate_init
command -v python3 >/dev/null || { echo "FAIL: python3 is not on PATH"; exit 1; }
command -v llvm-size >/dev/null || { echo "FAIL: llvm-size is not on PATH; it ships with LLVM alongside llc"; exit 1; }

# The bytes of code in a linked binary: the `__text` (Mach-O) or
# `.text` (ELF) section, summed. 0 means the reader found no such
# section, which is a broken measurement, never a small binary.
text_bytes() { # <binary>
  llvm-size -A "$1" 2>/dev/null \
    | awk '$1 == "__text" || $1 == ".text" { s += $2 } END { print s + 0 }'
}

failed=0
checks=0
ok()  { echo "ok   $*"; checks=$((checks + 1)); }
bad() { echo "FAIL $*"; failed=$((failed + 1)); }

gate_build_axc axc

# `measure-memory-baseline.sh`'s reader, and its rule: fail rather
# than skip when neither `time` answers.
# (`max_rss_kb` itself is defined once, in scripts/lib/gate.sh.)

releases_in() { # <ll> -> count of release call sites (not the define)
  grep -c 'call void @axiom_release' "$1"
}

# `check-static-release.sh`'s counter, copied: count
# `axiom_release` sites whose operand is defined, in the same define,
# by a `ptrtoint ... @strhdr_*`. Prints "<static> <total> <hdrs>".
count_static_releases() {
  python3 - "$1" <<'PY'
import re, sys
txt = open(sys.argv[1], encoding="utf-8", errors="replace").read()
static = total = 0
hdrs = len(re.findall(r'^@strhdr_\d+ = ', txt, re.M))
for b in re.split(r'\n(?=define )', txt):
    defs = {}
    for L in b.split("\n"):
        m = re.match(r'\s*(%[\w.]+) = (.*)', L)
        if m:
            defs[m.group(1)] = m.group(2)
    for L in b.split("\n"):
        m = re.match(r'^\s*call void @axiom_release\(i64 (%[.\w]+)\)', L)
        if m:
            total += 1
            if "@strhdr_" in defs.get(m.group(1), ""):
                static += 1
print(static, total, hdrs)
PY
}

# ---------------------------------------------------------------
echo "== 0. the full ablation: three rule-kills, one compiler =="
# ---------------------------------------------------------------
# Ablated on a COPY of the tree: `gate_source_stamp` hashes
# `self_host/`, so an ablation left behind would silently become the
# tree every later gate builds from.
abl="$work/tree"
mkdir -p "$abl"
cp -R "$repo_root/self_host" "$repo_root/stdlib" "$abl/" || {
  echo "FAIL: could not copy the tree to ablate" >&2; exit 1; }

if ! python3 - "$abl/self_host/codegen.ax" "$abl/self_host/typecheck.ax" <<'PY'
import re, sys
cgp, tcp = sys.argv[1], sys.argv[2]
cg = open(cgp, encoding="utf-8").read()
tc = open(tcp, encoding="utf-8").read()

# Kill 1, slice 1: the reclaim gate's own anchor. The first depth
# guard read after the predicate's head answers 0, so the rule still
# exists, still runs, and never fires.
head = "(pub fn (isRegionCoveredCon cg e)"
if cg.count(head) != 1:
    sys.exit("kill 1: predicate head found %d times, wanted 1" % cg.count(head))
i = cg.index(head)
j = cg.index("(pairSlot cg 9)", i)
cg = cg[:j] + "0" + cg[j + len("(pairSlot cg 9)"):]

# Kill 3, slice 5: turn off only the literal branch. Named function
# descriptors keep their static sentinel, isolating the string traffic.
pat = re.compile(r"(\(pub fn \(isStaticSentinelNode cg e\)\s+\(if \(nodeSame e nilNode\)\s+0\s+\(if \(== \(nodeTag e\) TAG_E_STR\)\s+)1(?=\s)")
if len(pat.findall(cg)) != 1:
    sys.exit("kill 3: literal sentinel branch found %d times, wanted 1" % len(pat.findall(cg)))
cg = pat.sub(r"\g<1>0", cg, count=1)
open(cgp, "w", encoding="utf-8").write(cg)

# Kill 2, slices 2-4: `rgnStamping` (TC word 40) is never set, so the
# post-fixpoint walks stamp nothing anywhere and every stamp spend
# keeps its release. The enabling store in `rgnCheckAll`, and only that.
old40 = "(memSetWord tc 40 1)"
if tc.count(old40) != 1:
    sys.exit("kill 2: stamping set found %d times, wanted 1" % tc.count(old40))
tc = tc.replace(old40, "(memSetWord tc 40 0)")
open(tcp, "w", encoding="utf-8").write(tc)
PY
then
  bad "could not apply the full ablation - a rule moved, and this gate is asserting nothing"
  echo "     nothing was ablated, so every arm below proves nothing"
else
  echo "-- rebuilding the compiler from the fully-ablated tree --"
  if gate_build_tree "$axiom" "$abl" "$abl/stdlib" \
       "$work/axc-ablated" >"$work/ablated.build.log" 2>&1; then
    ok "the fully-ablated compiler builds (all three rule-kills applied)"
  else
    bad "the fully-ablated compiler did not build"
    sed 's/^/     /' "$work/ablated.build.log" | head -20
  fi
fi

# ---------------------------------------------------------------
echo
echo "== W1. the §1.1 workload: emit self_host both ways =="
# ---------------------------------------------------------------
if [[ -f "$work/axc-ablated" ]]; then
  if "$axc" emit-llvm "$repo_root/self_host/main.ax" -o "$work/host.ll" >"$work/w1.log" 2>&1 \
     && "$work/axc-ablated" emit-llvm "$repo_root/self_host/main.ax" -o "$work/host-abl.ll" >>"$work/w1.log" 2>&1; then
    n_new="$(releases_in "$work/host.ll")"
    n_abl="$(releases_in "$work/host-abl.ll")"
    read -r st_new tot_new hd <<<"$(count_static_releases "$work/host.ll")"
    read -r st_abl tot_abl hd_abl <<<"$(count_static_releases "$work/host-abl.ll")"
    if (( n_abl - n_new < 1000 )); then
      bad "self_host release delta is $((n_abl - n_new)) ($n_abl ablated, $n_new under test), wanted thousands"
    elif (( st_abl - st_new != n_abl - n_new )); then
      bad "self_host delta is $((n_abl - n_new)) releases but only $((st_abl - st_new)) on literals - a region optimisation changed a nonliteral release in this workload"
    else
      # Restored releases flip `musttail` decisions downstream (a
      # pending release kills the jump, so the ablated arm branches
      # where the verdict arm jumps), and the flips renumber
      # registers and drift `diff`'s alignment along the way. So the
      # comparison normalises both IRs first - release lines out,
      # register and block numbers folded - then cancels every
      # diff line whose twin stands on the other side (alignment
      # drift shows identical lines as changed), and requires every
      # line left to be a flip artifact: the branch, the block
      # label, the line comment, the call against its `musttail`
      # twin, the phi its merge reshaped, the return, and the
      # `@__axiom_line*` rows - the file/line/col table, the block
      # addresses, the table size with its bound check - which scale
      # with the release count (one entry per site). Anything OUTSIDE that
      # vocabulary is the ablation moving something else. A forwarded
      # call result also needs a retain before an argument is released.
      # Normalise that guard only when the adjacent call, result and
      # restored literal release establish the dependency. A matching
      # peer musttail call also removes its return-slot store; the
      # comparison checks that dependency before normalising it.
      # The residual looseness - a breakage spelled entirely
      # in flip words - is stated, and is covered from the other
      # side by the static-equality above (every restored release
      # is on a literal) and the answers-identity of W2/W3 below.
      other_rc=0
      other="$(python3 - "$work/host-abl.ll" "$work/host.ll" <<'PY'
import re, subprocess, sys
from collections import Counter
def literal_result_guards(lines):
    literals, guards = set(), set()
    for i, line in enumerate(lines):
        if line.startswith('define '):
            literals.clear()
        literal = re.match(r'\s*(%[.\w]+) = ptrtoint .*@strhdr_\d+\b', line)
        if literal:
            literals.add(literal.group(1))
        retain = re.fullmatch(r'\s*call void @axiom_retain\(i64 (%[.\w]+)\)\s*', line)
        if not retain or i == 0 or i + 1 == len(lines):
            continue
        call = re.fullmatch(r'\s*(%[.\w]+) = (?:tail )?call i64 @[^ (]+\((.*)\)\s*', lines[i - 1])
        release = re.fullmatch(r'\s*call void @axiom_release\(i64 (%[.\w]+)\)\s*', lines[i + 1])
        if (call and release and retain.group(1) == call.group(1)
                and release.group(1) in literals
                and re.search(r'\bi64 ' + re.escape(release.group(1)) + r'(?=[,)])', call.group(2) + ')')):
            guards.add(i)
    return guards

# A copied result needs this guard; unrelated retains must survive.
probe = ['define i64 @probe(i64 %heap) {',
         '  %lit = ptrtoint ptr @strhdr_1 to i64',
         '  %result = call i64 @forward(i64 %heap, i64 %lit)',
         '  call void @axiom_retain(i64 %result)',
         '  call void @axiom_release(i64 %lit)', '}']
assert literal_result_guards(probe) == {3}
for index, replacement in [(2, '  %result = call i64 @forward(i64 %heap)'),
                           (3, '  call void @axiom_retain(i64 %heap)'),
                           (4, '  call void @axiom_release(i64 %heap)'),
                           (4, '  ret i64 %result')]:
    mutant = probe.copy()
    mutant[index] = replacement
    assert not literal_result_guards(mutant), (index, replacement)

def call_key(function, callee, args):
    args = re.sub(r"%t\d+|%\.\w*\d+", "%RN", args)
    return function, callee, args

def tail_calls(lines):
    calls, function = Counter(), None
    for line in lines:
        start = re.match(r'define [^@]*@([^ (]+)', line)
        if start:
            function = start.group(1)
        call = re.fullmatch(r'\s*%[.\w]+ = musttail call i64 @([^ (]+)\((.*)\)\s*', line)
        if call:
            calls[call_key(function, *call.groups())] += 1
    return calls

def literal_merge_stores(lines, peer_tails):
    """Only a literal-release-induced call-to-tail flip's result stores.

    Require the peer's matching musttail call, a released literal
    argument, the exact three-instruction store of the call's result,
    and a slot loaded on the function's return dependency graph.
    """
    skip, function, start = set(), None, 0
    for end, line in enumerate(lines):
        head = re.match(r'define [^@]*@([^ (]+)', line)
        if head:
            function, start = head.group(1), end
        if line.strip() != '}' or function is None:
            continue
        body = lines[start:end + 1]
        defs, roots = {}, []
        for text in body:
            definition = re.match(r'\s*(%[.\w]+) = (.*)', text)
            if definition:
                defs[definition.group(1)] = definition.group(2)
            ret = re.fullmatch(r'\s*ret i64 (%[.\w]+)\s*', text)
            if ret:
                roots.append(ret.group(1))
        seen = set()
        while roots:
            value = roots.pop()
            if value not in seen:
                seen.add(value)
                roots.extend(re.findall(r'%[.\w]+', defs.get(value, '')))
        slots = {match.group(1) for value in seen
                 if (match := re.fullmatch(r'inttoptr i64 (%[.\w]+) to ptr', defs.get(value, '')))}
        literals = set()
        for i in range(start, end - 4):
            literal = re.match(r'\s*(%[.\w]+) = ptrtoint .*@strhdr_\d+\b', lines[i])
            if literal:
                literals.add(literal.group(1))
            call = re.fullmatch(r'\s*(%[.\w]+) = call i64 @([^ (]+)\((.*)\)\s*', lines[i])
            release = re.fullmatch(r'\s*call void @axiom_release\(i64 (%[.\w]+)\)\s*', lines[i + 1])
            base = re.fullmatch(r'\s*(%[.\w]+) = inttoptr i64 (%[.\w]+) to ptr\s*', lines[i + 2])
            field = re.fullmatch(r'\s*(%[.\w]+) = getelementptr i64, ptr (%[.\w]+), i64 0\s*', lines[i + 3])
            store = re.fullmatch(r'\s*store i64 (%[.\w]+), ptr (%[.\w]+)\s*', lines[i + 4])
            if not all((call, release, base, field, store)):
                continue
            key = call_key(function, call.group(2), call.group(3))
            if (peer_tails[key] and release.group(1) in literals
                    and re.search(r'\bi64 ' + re.escape(release.group(1)) + r'(?=[,)])', call.group(3) + ')')
                    and base.group(2) in slots and field.group(2) == base.group(1)
                    and store.groups() == (call.group(1), field.group(1))):
                skip.update(range(i + 2, i + 5))
                peer_tails[key] -= 1
        function = None
    return skip

# A return slot and its peer tail call are both required; arbitrary
# field stores and unrelated call results stay in the comparison.
merge_probe = ['define i64 @probe(i64 %heap) {',
               '  %lit = ptrtoint ptr @strhdr_1 to i64',
               '  %result = call i64 @forward(i64 %heap, i64 %lit)',
               '  call void @axiom_release(i64 %lit)',
               '  %p = inttoptr i64 %slot to ptr',
               '  %q = getelementptr i64, ptr %p, i64 0',
               '  store i64 %result, ptr %q',
               '  %p2 = inttoptr i64 %slot to ptr',
               '  %q2 = getelementptr i64, ptr %p2, i64 0',
               '  %answer = load i64, ptr %q2', '  ret i64 %answer', '}']
peer = Counter({call_key('probe', 'forward', 'i64 %heap, i64 %lit'): 1})
assert literal_merge_stores(merge_probe, peer.copy()) == {4, 5, 6}
assert not literal_merge_stores(merge_probe, Counter())
for index, replacement in [(6, '  store i64 %heap, ptr %q'),
                           (5, '  %q = getelementptr i64, ptr %p, i64 1'),
                           (3, '  call void @axiom_release(i64 %heap)'),
                           (10, '  ret i64 %heap')]:
    mutant = merge_probe.copy()
    mutant[index] = replacement
    assert not literal_merge_stores(mutant, peer.copy()), (index, replacement)

def norm(p, peer):
    out = []
    lines = open(p, encoding="utf-8", errors="replace").readlines()
    guards = literal_result_guards(lines) | literal_merge_stores(lines, tail_calls(peer))
    for i, L in enumerate(lines):
        if i in guards or "call void @axiom_release" in L:
            continue
        L = re.sub(r"%t\d+", "%tN", L)
        L = re.sub(r"%\.\w*\d+", "%RN", L)
        L = re.sub(r"\.L+\d+", ".LN", L)
        out.append(L)
    return out
a, b = norm(sys.argv[1], open(sys.argv[2]).readlines()), norm(sys.argv[2], open(sys.argv[1]).readlines())
import tempfile, os
fa = tempfile.NamedTemporaryFile("w", delete=False); fa.write("".join(a)); fa.close()
fb = tempfile.NamedTemporaryFile("w", delete=False); fb.write("".join(b)); fb.close()
r = subprocess.run(["diff", fa.name, fb.name], capture_output=True, text=True)
os.unlink(fa.name); os.unlink(fb.name)
less, more = Counter(), Counter()
for L in r.stdout.split("\n"):
    if L.startswith("< "):
        less[L[2:]] += 1
    elif L.startswith("> "):
        more[L[2:]] += 1
resid = list((less - more).elements()) + list((more - less).elements())
voc = re.compile(r"musttail|br label|ret i64|@@line|^\.LN:|phi i64| = call i64 @|@__axiom_(line|filen)|icmp uge i64 %li,")
bad = [L for L in resid if not voc.search(L)]
for L in bad[:10]:
    print("RESID: " + L)
print("COUNT: %d" % len(bad))
PY
)" || other_rc=$?
      other_n="$(echo "$other" | sed -n 's/^COUNT: //p')"
      if (( other_rc != 0 )) || [[ ! "$other_n" =~ ^[0-9]+$ ]]; then
        bad "the self_host IR comparison did not produce a verdict"
      elif (( other_n != 0 )); then
        bad "the two self_host IRs differ by $other_n line(s) outside the release+flip vocabulary"
        echo "$other" | grep '^RESID: ' | head -10 | sed 's/^/     /'
      else
        ok "self_host: $((n_abl - n_new)) releases restored, every one on a literal ($hd_abl headers), other changes depend on those releases"
      fi
    fi
    rss_new="$(max_rss_kb "$axc" emit-llvm "$repo_root/self_host/main.ax" -o "$work/host.rss.ll")" || rss_new=""
    rss_abl="$(max_rss_kb "$work/axc-ablated" emit-llvm "$repo_root/self_host/main.ax" -o "$work/host-abl.rss.ll")" || rss_abl=""
    if [[ -z "$rss_new" || -z "$rss_abl" ]]; then
      bad "could not measure emit peak RSS on this host"
    elif (( rss_abl == 0 )); then
      bad "ablated emit peak RSS reads 0 - the measurement is broken, not flat"
    else
      ratio=$(( rss_new * 100 / rss_abl ))
      if (( ratio > 150 )); then
        bad "emit peak RSS ratio ${ratio}% (${rss_new} KiB against ${rss_abl} KiB) - the elisions cost memory"
      else
        ok "emitting self_host peaks ${rss_new} KiB against ${rss_abl} KiB ablated (${ratio}%) - the RSS win intact"
      fi
    fi
  else
    bad "could not emit self_host under one or both compilers"
    sed 's/^/     /' "$work/w1.log" | head -10
  fi
else
  bad "no ablated compiler, so W1 proves nothing"
fi

# ---------------------------------------------------------------
echo
echo "== W2. six fixtures and a literal probe: pins, answers, bytes =="
# ---------------------------------------------------------------
# The literal probe: slice 5's four guarded positions (argument,
# field, `let` scope end, tail-call temporary) with deterministic
# stdout - its gate's probe answers a pointer cast, which ASLR moves,
# so that shape cannot carry an answers-identity check.
cat > "$work/litprobe.ax" <<'AX'
(import IO)

(import Str)

(struct Box (label : String) (n : Int))

(:: argOf (-> Int Int))

(fn (argOf n) (strLen (strDup "in argument position")))

(:: boxOf (-> Int Int))

(fn (boxOf n) (as (Box :label "in field position" :n n) Int))

(:: letOf (-> Int Int))

(fn (letOf n) { (let s "in let position") (strLen s)})

(:: tailOf (-> Int String Int))

(fn (tailOf n acc)
  (if (<= n 0)
    (strLen acc)
    (tailOf (- n 1) "in tail position")))

(:: main ())

;@axiom:effect(io)
(fn (main)
  {
    (println (+ (argOf 1) (+ (letOf 1) (tailOf 3 "seed"))))
    (boxOf 1)
  })
AX
# name:pin - full-ablation pins, each its slice's pin plus the other
# slices' cross-traffic in the same module, classified by operand
# definer (construction call, other call, phi, load, `@strhdr_*`
# literal) against both IRs:
#   479: 17 = 6 constructions (the slice pin) + 11 literals
#   480: 22 = 7 calls (the slice pin; no join spends - zero phis) + 12 literals, 2 constructions, 1 call
#   481: 20 = 8 fresh calls (the slice pin) + 11 literals, 1 construction
#   482: 23 = 6 joins + 1 scratch load (the slice pin) + 14 literals, 1 construction, 1 load
#   483: 20 = 6 joins + 2 scratch loads (the slice pin) + 11 literals, 1 construction
#   484: 25 = 4 fresh calls + 4 joins (the slice pin) + 15 literals, 1 construction, 1 load
# The dominant cross-traffic is literals - the println and term
# strings every fixture prints through. Slice floors: 6/7/8/7/8/8.
# Three of each program's literals, the literal probe's too, are the
# stdlib's print path rather than the program's own code: one each in
# Err's `mkError`, Fmt's `fmtInt` and Sys.Platform's
# `platformWriteFd`. That share is the same in all seven programs, so
# a change to the print path moves every pin here by one amount and
# leaves the slice classes alone. Re-pin from a classification of both
# IRs, as above, so a moved slice class can't hide in the new number.
pins="479-region-reclaim:17 480-region-fresh-call:22 481-region-fresh-let:20 482-region-phi-call:23 483-region-phi-let:20 484-region-scrutinee:25"
if [[ -f "$work/axc-ablated" ]]; then
  sum_new=0; sum_abl=0
  for pair in $pins; do
    name="${pair%%:*}"; pin="${pair##*:}"
    fx="$repo_root/tests/stdlib/$name.ax"
    "$axc" emit-llvm "$fx" -o "$work/$name.ll" >"$work/w2.log" 2>&1 \
      || { bad "$name does not emit under test"; continue; }
    "$work/axc-ablated" emit-llvm "$fx" -o "$work/$name-abl.ll" >>"$work/w2.log" 2>&1 \
      || { bad "$name does not emit ablated"; continue; }
    n_new="$(releases_in "$work/$name.ll")"
    n_abl="$(releases_in "$work/$name-abl.ll")"
    if (( n_new == 0 )); then
      bad "$name: no releases at all under test - an elision that fires everywhere proves nothing"
      continue
    fi
    if (( n_abl - n_new != pin )); then
      bad "$name: release delta is $((n_abl - n_new)) ($n_abl ablated, $n_new under test), wanted exactly $pin"
      continue
    fi
    other="$(diff "$work/$name-abl.ll" "$work/$name.ll" | grep -E '^[<>]' | grep -vc 'axiom_release' || true)"
    if (( other != 0 )); then
      bad "$name: the two IRs differ by $other non-release line(s)"
      continue
    fi
    if ! "$axc" build --input "$fx" --output "$work/$name" >>"$work/w2.log" 2>&1 \
       || ! "$work/axc-ablated" build --input "$fx" --output "$work/$name-abl" >>"$work/w2.log" 2>&1; then
      bad "$name does not build under one or both compilers"
      sed 's/^/     /' "$work/w2.log" | head -5
      continue
    fi
    "$work/$name" >"$work/$name.out" 2>&1; rc_new=$?
    "$work/$name-abl" >"$work/$name-abl.out" 2>&1; rc_abl=$?
    if (( rc_new != rc_abl )) || ! cmp -s "$work/$name.out" "$work/$name-abl.out"; then
      bad "$name answers differ ($rc_new under test, $rc_abl ablated) - the elision changed it"
      diff "$work/$name-abl.out" "$work/$name.out" | head -5 | sed 's/^/     /'
      continue
    fi
    b_new="$(text_bytes "$work/$name")"; b_abl="$(text_bytes "$work/$name-abl")"
    f_new="$(wc -c < "$work/$name" | tr -d ' ')"; f_abl="$(wc -c < "$work/$name-abl" | tr -d ' ')"
    if (( b_new == 0 || b_abl == 0 )); then
      bad "$name: no text section read ($b_new under test, $b_abl ablated) - the measurement is broken"
      continue
    fi
    # Per file the verdict's code must not be BIGGER; it may tie, because
    # function alignment can absorb a few removed calls into padding.
    # The win itself is pinned on the aggregate below, where padding
    # cannot absorb every file at once.
    if (( b_new > b_abl )); then
      bad "$name code is $b_new bytes under test against $b_abl ablated - the elision grew the backend"
      continue
    fi
    sum_new=$(( sum_new + b_new )); sum_abl=$(( sum_abl + b_abl ))
    ok "$name: delta $pin, identical answers, code $b_new <= $b_abl bytes (files $f_new / $f_abl, not asserted)"
  done
  # The literal probe: pin 9 = its four guarded positions + 2 literals
  # in `main` + the 3 on the stdlib's print path; it prints "51".
  lit_pin=9
  "$axc" emit-llvm "$work/litprobe.ax" -o "$work/litprobe.ll" >"$work/lit.log" 2>&1 \
    || { bad "the literal probe does not emit under test"; }
  "$work/axc-ablated" emit-llvm "$work/litprobe.ax" -o "$work/litprobe-abl.ll" >>"$work/lit.log" 2>&1 \
    || { bad "the literal probe does not emit ablated"; }
  if [[ -f "$work/litprobe.ll" && -f "$work/litprobe-abl.ll" ]]; then
    n_new="$(releases_in "$work/litprobe.ll")"
    n_abl="$(releases_in "$work/litprobe-abl.ll")"
    if (( n_abl - n_new != lit_pin )); then
      bad "literal probe delta is $((n_abl - n_new)) ($n_abl ablated, $n_new under test), wanted exactly $lit_pin"
    elif ! "$axc" build --input "$work/litprobe.ax" --output "$work/litprobe" >>"$work/lit.log" 2>&1 \
         || ! "$work/axc-ablated" build --input "$work/litprobe.ax" --output "$work/litprobe-abl" >>"$work/lit.log" 2>&1; then
      bad "the literal probe does not build under one or both compilers"
    else
      "$work/litprobe" >"$work/litprobe.out" 2>&1; rc_new=$?
      "$work/litprobe-abl" >"$work/litprobe-abl.out" 2>&1; rc_abl=$?
      want="51"
      got="$(cat "$work/litprobe.out")"
      if (( rc_new != 0 || rc_abl != 0 )) || ! cmp -s "$work/litprobe.out" "$work/litprobe-abl.out"; then
        bad "the literal probe answers differ - the elision changed it"
      elif [[ "$got" != "$want" ]]; then
        bad "the literal probe answers '$got', wanted '51' - the probe is not probing"
      else
        b_new="$(text_bytes "$work/litprobe")"; b_abl="$(text_bytes "$work/litprobe-abl")"
        if (( b_new == 0 || b_abl == 0 )); then
          bad "literal probe: no text section read ($b_new under test, $b_abl ablated) - the measurement is broken"
        elif (( b_new > b_abl )); then
          bad "literal probe code is $b_new bytes under test against $b_abl ablated"
        else
          sum_new=$(( sum_new + b_new )); sum_abl=$(( sum_abl + b_abl ))
          ok "literal probe: delta $lit_pin, answers 51 both ways, code $b_new <= $b_abl bytes"
        fi
      fi
    fi
  fi
  if (( sum_abl > 0 )); then
    if (( sum_new >= sum_abl )); then
      bad "aggregate code is $sum_new bytes under test against $sum_abl ablated - the win is missing"
    else
      ok "aggregate code $sum_new < $sum_abl bytes - the win reaches the backend"
    fi
  fi
else
  bad "no ablated compiler, so W2 proves nothing"
fi

# ---------------------------------------------------------------
echo
echo "== W3. three hundred thousand regions of combined traffic =="
# ---------------------------------------------------------------
# Slice 1's loop shape with slice 2's and slice 5's traffic folded
# in: each iteration drops a direct construction (slice 1's spend),
# routes a fresh box through a call (the call-site release slice 2's
# stamp owns - the callee body has no region textually, so only the
# call-site release is elided there), and touches a literal in
# argument position (slice 5's, region or none). Iterative for the
# same reason slice 1's is: a `region` around a self-call is not a
# tail position.
cat > "$work/combo.ax" <<'AX'
(import IO)

(import Str)

(data Box (MkBox Int))

(:: mkFresh (-> Int Box))

(fn (mkFresh i) (MkBox i))

(:: useBox (-> Box Int Int))

(fn (useBox o d) (+ (match o ((MkBox x) x)) d))

(:: loop (-> Int Int))

(fn (loop n)
  { (let mut i n)
    { (let mut acc 0)
      {
        (while (> i 0)
          {
            (region r
              (set acc (+ acc (+ (useBox (mkFresh i) 0) (+ (useBox (MkBox i) 0) (strLen "touch"))))))
            (set i (- i 1))
          })
        acc
      }
    }
  }
)

(:: main ())

;@axiom:effect(io)
(fn (main)
  {
    (println (loop 300000))
  }
)
AX
if [[ -f "$work/axc-ablated" ]] \
   && "$axc" build --input "$work/combo.ax" --output "$work/combo" >"$work/combo.build.log" 2>&1 \
   && "$work/axc-ablated" build --input "$work/combo.ax" --output "$work/combo-abl" >>"$work/combo.build.log" 2>&1; then
  out_new="$("$work/combo" 2>&1)"; rc_new=$?
  out_abl="$("$work/combo-abl" 2>&1)"; rc_abl=$?
  # Twice the triangular sum plus one 5-wide touch per iteration.
  if [[ "$out_new" != "$out_abl" || "$rc_new" != "$rc_abl" ]]; then
    bad "combo answers differ: test '$out_new'/$rc_new against ablated '$out_abl'/$rc_abl"
  elif [[ "$out_new" != "90001800000" ]]; then
    bad "combo answers $out_new, wanted 90001800000"
  else
    rss_new="$(max_rss_kb "$work/combo")" || rss_new=""
    rss_abl="$(max_rss_kb "$work/combo-abl")" || rss_abl=""
    if [[ -z "$rss_new" || -z "$rss_abl" ]]; then
      bad "could not measure peak RSS on this host"
    elif (( rss_abl == 0 )); then
      bad "ablated peak RSS reads 0 - the measurement is broken, not flat"
    else
      ratio=$(( rss_new * 100 / rss_abl ))
      if (( ratio > 150 )); then
        bad "peak RSS ratio ${ratio}% (${rss_new} KiB against ${rss_abl} KiB) - combined traffic leaked"
      else
        ok "combo answers 90001800000 both ways, peak RSS ${rss_new} KiB against ${rss_abl} KiB (${ratio}%)"
      fi
    fi
  fi
else
  bad "could not build the combo loop under one or both compilers"
  [[ -f "$work/combo.build.log" ]] && sed 's/^/     /' "$work/combo.build.log" | head -10
fi

echo
if (( failed > 0 )); then
  echo "check-region-verdict: $failed check(s) failed, $checks passed"
  exit 1
fi
echo "check-region-verdict: $checks checks - the binary win with the RSS win intact"
