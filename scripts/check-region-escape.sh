#!/usr/bin/env bash
# Lexical region safety: origins through callees, closures and loops;
# complete proofs; and a reporting-pass ablation that exposes reclaimed
# memory. Ordinary signatures carry no region syntax.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/gate.sh"
gate_init
gate_build_axc axc
failed=0
checks=0
ok()  { echo "ok   $*"; checks=$((checks + 1)); }
bad() { echo "FAIL $*"; failed=$((failed + 1)); }

fix="$repo_root/tests/diagnostics"
mkdir -p "$work/run"

# Accepted function results, field stores and containers use ordinary types.
echo "== 1. inferred origins preserve valid calls =="
checks=$((checks + 1))
if "$axc" --diagnostic-format=ai run "$repo_root/tests/stdlib/468-region-signatures.ax" \
    > "$work/run/inferred.out" 2> "$work/run/inferred.err" \
    && cmp -s "$work/run/inferred.out" "$repo_root/tests/stdlib/468-region-signatures.out"; then
  ok "468: inferred calls run to their golden"
else
  bad "468: a valid inferred call failed"
  head -10 "$work/run/inferred.err"
fi
# Removing annotation syntax must cover formatter and checker surfaces.
cp "$repo_root/tests/diagnostics/1413-retired-region-annotation.axbad" "$work/run/retired.ax"
"$axc" --diagnostic-format=ai check "$work/run/retired.ax" > /dev/null 2> "$work/run/retired.err"
if grep -q '^E AX1001 .*unexpected character.*@' "$work/run/retired.err"; then
  ok "signature region annotations are refused"
else
  bad "signature region annotations remain accepted"
  head -8 "$work/run/retired.err"
fi
if "$axc" --diagnostic-format=ai fmt --check "$work/run/retired.ax" > /dev/null 2>&1; then
  bad "formatter accepted a retired region annotation"
else
  ok "formatter refuses retired region annotations"
fi

# ---------------------------------------------------------------
# 2. the rule refuses, by code and by name
# ---------------------------------------------------------------
echo "== 2. the fixtures are refused for the codes they name =="
refused() {  # <case> <code> <name-in-message>
  local case="$1" code="$2" name="$3"
  checks=$((checks + 1))
  cp "$fix/$case.ax" "$work/run/$case.ax"
  ( cd "$work/run" && "$axc" --diagnostic-format=ai check "$case.ax" ) > /dev/null 2> "$work/run/$case.err"
  if grep -q "^[EW] $code .*$name" "$work/run/$case.err"; then
    ok "$case: $code names \`$name\`"
  else
    bad "$case: no $code naming \`$name\`"; sed 's/^/     /' "$work/run/$case.err" | cut -c1-160 | head -6
  fi
}
refused 645-region-escape-store   AX3060 'parameter `c` of `putField`'
refused 645-region-escape-store   AX3060 'parameter `v` of `putVec`'
refused 646-region-escape-return  AX3059 'picked'
refused 646-region-escape-return  AX3059 'allocated'
refused 647-region-capture        AX3062 'captures `s`'
refused 648-region-argument       AX3060 'pair'
refused 649-restrict-no-escape    AX3049 'through `vecPush`'
refused 649-restrict-no-escape    AX3051 'dispatch'
refused 649-restrict-no-escape    AX3057 'dispatchStrict'

if python3 "$repo_root/tests/lsp/region-boundary-hints.py" "$axc"; then
  ok "the LSP preserves memory-boundary remedies and related spans"
else
  bad "the LSP lost a memory-boundary remedy or span"
fi

# A callback's captures are destinations too. The three stores were
# accepted when the origin walk considered only its explicit arguments.
cp "$repo_root/tests/region/escape-closure-call.ax" "$work/run/escape-closure-call.ax"
( cd "$work/run" && "$axc" --diagnostic-format=ai check escape-closure-call.ax ) \
  > /dev/null 2> "$work/run/escape-closure-call.err"
for extent in direct indirect field; do
  checks=$((checks + 1))
  if grep -q "^E AX3060 .*region \`$extent\`" "$work/run/escape-closure-call.err"; then
    ok "$extent callback refuses a store into an older capture"
  else
    bad "$extent callback accepted an escaping reference"
    head -8 "$work/run/escape-closure-call.err"
  fi
done
if "$axc" --diagnostic-format=ai check "$repo_root/tests/region/closure-local.ax" \
    > /dev/null 2> "$work/run/closure-local.err"; then
  ok "callbacks whose captures and arguments share a region are accepted"
else
  bad "a callback confined to one region was refused"
  head -8 "$work/run/closure-local.err"
fi

# A loop back edge carries aliases beyond a fixed number of walks.
cp "$repo_root/tests/region/escape-loop-backedge.ax" "$work/run/escape-loop-backedge.ax"
"$axc" --diagnostic-format=ai check "$work/run/escape-loop-backedge.ax" \
  > /dev/null 2> "$work/run/escape-loop-backedge.err"
if grep -q '^E AX3060 .*delayStore.*parameter `inner`' "$work/run/escape-loop-backedge.err"; then
  ok "a delayed loop alias is refused at the callee's store"
else
  bad "a loop back edge hid an escaping alias"
  head -8 "$work/run/escape-loop-backedge.err"
fi
for case in loop-backedge-local loop-extents-stable; do
  if "$axc" --diagnostic-format=ai run "$repo_root/tests/region/$case.ax" \
      > "$work/run/$case.out" 2> "$work/run/$case.err"; then
    ok "$case: valid same-region aliases run to exit 0"
  else
    bad "$case: a valid loop failed"
    head -8 "$work/run/$case.err"
  fi
done

# A proof must never reuse an origin bit when the domain is full.
python3 - "$work/run" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
for count in (61, 62):
    body = "\n".join(f"    (region r{i} 0)" for i in range(count))
    (root / f"capacity-{count}.ax").write_text(
        f"(:: main Int)\n(fn (main) {{\n{body}\n    0 }})\n")
for count in (61, 62):
    params = " ".join(f"p{i}" for i in range(count))
    types = " ".join("String" for _ in range(count))
    (root / f"parameters-{count}.ax").write_text(
        f"(:: wide (-> {types} String))\n"
        f"(fn (wide {params}) p{count - 1})\n"
        "(:: main Int)\n(fn (main) (region trigger 0))\n")
PY
for shape in capacity parameters; do
  if "$axc" --diagnostic-format=ai check "$work/run/$shape-61.ax" \
      > /dev/null 2> "$work/run/$shape-61.err"; then
    ok "$shape: all 61 origins remain representable"
  else
    bad "$shape: the last representable origin was refused"
    head -8 "$work/run/$shape-61.err"
  fi
  "$axc" --diagnostic-format=ai check "$work/run/$shape-62.ax" \
    > /dev/null 2> "$work/run/$shape-62.err"
  if grep -q '^E AX3060 .*region safety cannot be proved' "$work/run/$shape-62.err"; then
    ok "$shape: an unrepresentable lifetime is refused"
  else
    bad "$shape: origin overflow was accepted"
    head -8 "$work/run/$shape-62.err"
  fi
done

"$axc" --diagnostic-format=ai check "$repo_root/tests/region/lifetime-unconverged.ax" \
  > /dev/null 2> "$work/run/lifetime-unconverged.err"
if grep -q '^E AX3060 .*lifetime analysis did not converge' "$work/run/lifetime-unconverged.err"; then
  ok "an unfinished recursive lifetime proof is refused"
else
  bad "an unfinished lifetime proof permitted emission"
  head -8 "$work/run/lifetime-unconverged.err"
fi
cat > "$work/run/lifetime-converged.ax" <<'EOF'
(:: rotate (-> String String Int String))
(fn (rotate a b n) (if (== n 0) a (rotate b a (- n 1))))
(:: main ())
(fn (main) (region r ()))
EOF
if "$axc" --diagnostic-format=ai check "$work/run/lifetime-converged.ax" \
    > /dev/null 2> "$work/run/lifetime-converged.err"; then
  ok "a converged recursive lifetime proof is accepted"
else
  bad "a completed lifetime proof was refused"
  head -8 "$work/run/lifetime-converged.err"
fi
# the program of section 3, refused by the tree's compiler
checks=$((checks + 1))
cp "$repo_root/tests/region/escape-store.ax" "$work/run/escape-store.ax"
( cd "$work/run" && "$axc" --diagnostic-format=ai check escape-store.ax ) > /dev/null 2> "$work/run/escape-store.err"
if grep -q '^E AX3060 .*parameter `c` of `put`' "$work/run/escape-store.err"; then
  ok "tests/region/escape-store.ax: AX3060 at the store"
else
  bad "tests/region/escape-store.ax is not refused with AX3060"; sed 's/^/     /' "$work/run/escape-store.err" | cut -c1-160 | head -4
fi

# ---------------------------------------------------------------
# 3. the ablation: the pass answering 0 accepts them, and the accepted
#    program reads reclaimed memory
# ---------------------------------------------------------------
echo "== 3. ablation: rgnCheckAll answering 0 =="
abl="$work/tree"
mkdir -p "$abl"
cp -R "$repo_root/self_host" "$repo_root/stdlib" "$abl/" || {
  echo "FAIL: could not copy the tree to ablate" >&2; exit 1; }
cp "$abl/self_host/typecheck.ax" "$work/typecheck.unablated.ax"
python3 - "$abl/self_host/typecheck.ax" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
start = s.index("(pub fn (rgnCheckAll tc)")
end = s.index("(pub :: rgnEnsureFacts", start)
part = s[start:end]
old = "(if (== (memGetWord tc 39) 1)"
if part.count(old) != 1:
    sys.exit("the lexical reporting ablation no longer matches")
s = s[:start] + part.replace(old, "(if (== 0 1)") + s[end:]
open(p, "w", encoding="utf-8").write(s)
PY
if [[ $? -ne 0 ]]; then
  bad "could not apply the ablation - rgnCheckAll has moved, and section 3 is asserting nothing"
elif ! gate_build_tree "$axc" "$abl" "$abl/stdlib" \
     "$work/axc-ablated" > "$work/ablated.build.log" 2>&1; then
  bad "the ablated compiler did not build"; sed 's/^/     /' "$work/ablated.build.log" | head -20
else
  accepted() {  # <case>
    local case="$1"
    checks=$((checks + 1))
    if ( cd "$work/run" && AXIOM_STDLIB="$abl/stdlib" "$work/axc-ablated" check "$case.ax" ) > "$work/run/$case.abl" 2>&1; then
      ok "ablated: $case is ACCEPTED - the pass is what refuses it"
    else
      bad "ablated: $case is still refused, so the refusal does not rest on the pass"
      sed 's/^/     /' "$work/run/$case.abl" | head -4
    fi
  }
  accepted 645-region-escape-store
  accepted 647-region-capture
  accepted 648-region-argument
  accepted escape-store
  accepted escape-closure-call
  accepted escape-loop-backedge
  # 649 rests on the facts, not the pass, and must survive
  checks=$((checks + 1))
  ( cd "$work/run" && AXIOM_STDLIB="$abl/stdlib" "$work/axc-ablated" --diagnostic-format=ai check 649-restrict-no-escape.ax ) > /dev/null 2> "$work/run/649.abl"
  if grep -q '^E AX3049 .*through `vecPush`' "$work/run/649.abl"; then
    ok "ablated: 649's restrict(no-escape) refusal survives - it reads the facts, not the pass"
  else
    bad "ablated: 649's AX3049 vanished with the pass, so no-escape rests on the pass after all"
  fi
  # THE RUN. Built by the ablated compiler, the escape happens.
  checks=$((checks + 1))
  if ! ( cd "$work/run" && AXIOM_STDLIB="$abl/stdlib" "$work/axc-ablated" build escape-store.ax -o escape-store ) > "$work/run/escape-store.build" 2>&1; then
    bad "the ablated compiler did not build tests/region/escape-store.ax"
    sed 's/^/     /' "$work/run/escape-store.build" | head -10
  else
    set +e
    ( cd "$work/run" && ./escape-store ) > "$work/run/escape-store.out" 2> "$work/run/escape-store.stderr"
    status=$?
    set -e
    got="$(head -c 64 "$work/run/escape-store.out" | tr -d '\0' | head -1)"
    if [[ "$status" -eq 0 && "$got" == "hello" ]]; then
      bad "the accepted program printed \`hello\` at exit 0 - the store it makes did not escape, so the refusal guards nothing"
    else
      ok "the accepted program does not answer \`hello\` at exit 0 (exit $status, read back: '${got:0:24}') - the stored value was reclaimed"
    fi
    set +e
  fi
fi

# Isolate the loop rule: restore the reporting pass and replace only
# rgnWhile's fixpoint with its former two-pass traversal.
echo "== 3b. ablation: two walks of a loop body =="
cp "$work/typecheck.unablated.ax" "$abl/self_host/typecheck.ax"
python3 - "$abl/self_host/typecheck.ax" "$work/run/escape-loop-backedge.ax" \
  "$work/run/escape-loop-backedge-run.ax" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
s = p.read_text()
start = s.index("(pub fn (rgnWhile rc e)")
end = s.index("\n(pub :: rgnMatch ", start)
s = s[:start] + """(pub fn (rgnWhile rc e)
  {
    (let rep (memGetWord rc 11))
    {
      (memSetWord rc 11 0)
      (rgnExpr rc (nodeA e))
      (rgnSeq rc (nodeBVec e) 0 0)
      (memSetWord rc 11 rep)
      (rgnExpr rc (nodeA e))
      (rgnSeq rc (nodeBVec e) 0 0)
      0
    }
  })
""" + s[end:]
p.write_text(s)
s = pathlib.Path(sys.argv[2]).read_text()
for old, new in (
    ("(import Str)", "(import Str)\n(import IO)"),
    ("(:: main Int)\n(fn (main)", "(:: main Int)\n;@axiom:effect(io)\n(fn (main)"),
    ("(strLen c.s)", '{ (let replacement (strDup "fresh"))\n'
                      '        { (println c.s) (println replacement) 0 } }'),
):
    if s.count(old) != 1:
        raise SystemExit("loop runtime probe no longer matches its source fixture")
    s = s.replace(old, new)
pathlib.Path(sys.argv[3]).write_text(s)
PY
if [[ $? -ne 0 ]]; then
  bad "could not apply the two-pass loop ablation"
elif ! gate_build_tree "$axc" "$abl" "$abl/stdlib" \
     "$work/axc-loop-ablated" > "$work/loop-ablated.build.log" 2>&1; then
  bad "the two-pass loop compiler did not build"
  head -12 "$work/loop-ablated.build.log"
else
  if AXIOM_STDLIB="$abl/stdlib" "$work/axc-loop-ablated" --diagnostic-format=ai \
      check "$work/run/escape-loop-backedge.ax" > "$work/run/loop-ablated.check" 2>&1; then
    ok "two-pass ablation accepts the delayed escaping alias"
  else
    bad "the loop refusal does not depend on reaching a fixpoint"
    head -8 "$work/run/loop-ablated.check"
  fi
  printf 'short\nfresh\n' > "$work/run/loop-expected.out"
  if AXIOM_STDLIB="$abl/stdlib" "$work/axc-loop-ablated" --diagnostic-format=ai \
      run "$work/run/escape-loop-backedge-run.ax" \
      > "$work/run/loop-ablated.out" 2> "$work/run/loop-ablated.err"; then
    if cmp -s "$work/run/loop-expected.out" "$work/run/loop-ablated.out"; then
      bad "the accepted delayed alias still reads its original value after reset"
    else
      ok "two-pass ablation exposes a delayed alias reading reclaimed storage"
    fi
  else
    bad "the two-pass runtime probe did not execute"
    head -8 "$work/run/loop-ablated.err"
  fi
fi

# Diagnostic labels must not turn the facts walk into a whole-module
# lookup per argument. The pre-fix compiler fails this growth probe;
# the accepted programs exercise region analysis without parameter stores.
echo "== 4. region analysis avoids unused diagnostic lookups =="
if python3 "$repo_root/tests/region/diagnostic-cost.py" "$axc"; then
  ok "region analysis scales with calls whose parameters store nothing"
else
  bad "unused parameter labels make region analysis scale quadratically"
fi


echo
if [[ "$failed" -eq 0 ]]; then
  echo "check-region-escape: $checks checks, 0 failed"
  exit 0
fi
echo "check-region-escape: $checks checks, $failed failed"
exit 1
