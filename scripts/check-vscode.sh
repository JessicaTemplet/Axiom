#!/usr/bin/env bash
# The VS Code extension in editors/vscode/ agrees with the compiler it
# fronts.
#
# The extension is three JSON files, a TextMate grammar and ninety
# lines of JavaScript, none of which any other gate reads, and two of
# them restate facts the compiler owns:
#
#   - the grammar's head-keyword alternation is a COPY of the set
#     `self_host/parser.ax` dispatches on (its `kwEq name ...` call
#     sites) and `self_host/lsp.ax` offers (`lspKeywords`).
#     `editors/vscode/scripts/sync-keywords.mjs` writes that copy; this
#     gate derives the set again, by its own extraction, and requires
#     the grammar to carry exactly it. A keyword the parser learns and
#     the grammar forgets is red here, and so is the reverse.
#   - `semanticTokenScopes` in package.json maps the server's semantic
#     token legend to TextMate scopes. Every legend type must have a
#     mapping, and every mapped type and modifier must be in the legend,
#     or a token the server sends is coloured by nobody. The legend is
#     read from `lspSemanticTypes`/`lspSemanticModifiers` in lsp.ax
#     when those functions exist, and from the agreed list below until
#     the server side lands.
#
# The rest is the cheap half: the JSON parses, `extension.js` is
# syntactically JavaScript, `package.json` states the version `VERSION`
# does (a release bump rewrites it through version-sites.sh), and the
# sync script is a fixed point of the tree.
#
# Two floors and one ablation keep the equality from being vacuous: a
# derived set under twenty keywords means the extraction stopped
# matching, a legend under ten types the same, and a doctored grammar
# missing one keyword must be caught by the same comparison the real
# one passes.
#
# No compiler is built or run. The gate reads sources and needs only
# `node`, which the CI job that runs it installs for the tree-sitter
# CLI. It belongs in the cheap job beside check-tree-sitter.sh.
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root" || { echo "FAIL: no repository root at $repo_root" >&2; exit 1; }

# No `gate_init`: it bootstraps a compiler on a clean checkout, and this
# gate compares files. `check-ci-coverage.sh` does the same.

ext="$repo_root/editors/vscode"
grammar="$ext/syntaxes/axiom.tmLanguage.json"
parser_ax="$repo_root/self_host/parser.ax"
lsp_ax="$repo_root/self_host/lsp.ax"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

failed=0
checks=0
ok()  { echo "ok   $*"; checks=$((checks + 1)); }
bad() { echo "FAIL $*"; failed=$((failed + 1)); }

node="$(command -v node || true)"
[[ -n "$node" ]] || { echo "FAIL: node is not on PATH, and the gate reads JSON with it"; exit 1; }

# --------------------------------------------------------------------
echo "--- the files parse ---"
for f in package.json language-configuration.json syntaxes/axiom.tmLanguage.json snippets/axiom.json; do
  if [[ ! -f "$ext/$f" ]]; then
    bad "$f is missing"
  elif "$node" -e 'JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"))' "$ext/$f" 2>"$work/json.err"; then
    ok "$f is JSON"
  else
    bad "$f is not JSON: $(head -1 "$work/json.err")"
  fi
done

if "$node" --check "$ext/extension.js" 2>"$work/js.err"; then
  ok "extension.js is JavaScript"
else
  bad "extension.js: $(head -1 "$work/js.err")"
fi

for f in README.md LICENSE .vscodeignore .vscode/launch.json scripts/client-smoke.mjs scripts/sync-keywords.mjs icon.png icons/axiom.svg snippets/axiom.json; do
  [[ -f "$ext/$f" ]] && ok "$f is present" || bad "$f is missing"
done

if "$node" "$ext/scripts/client-smoke.mjs" >"$work/client.out" 2>"$work/client.err"; then
  ok "client lifecycle, dependency watchers and cancellation: $(cat "$work/client.out")"
else
  bad "client behaviour: $(cat "$work/client.err")"
fi

# --------------------------------------------------------------------
echo "--- the Axiom icon is on the extension and on the file ---"
# The marketplace icon is a square PNG of at least 128px (the brand
# avatar in assets/logo, resized), named by package.json's `icon`.
# The file icon is the brand tile as SVG, named for both themes by the
# language contribution, so a `.ax` file carries the mark in the
# explorer and in tabs.
if "$node" -e '
  const fs = require("fs");
  const p = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
  const must = (c, m) => { if (!c) { console.error(m); process.exit(1); } };
  must(p.icon === "icon.png", "package.json icon is not icon.png");
  const lang = (p.contributes.languages || []).find((l) => l.id === "axiom");
  must(lang && lang.icon && lang.icon.light === "./icons/axiom.svg" && lang.icon.dark === "./icons/axiom.svg",
    "language axiom does not name ./icons/axiom.svg for both themes");
  const png = fs.readFileSync(process.argv[2]);
  must(png.length > 8 && png.readUInt32BE(0) === 0x89504e47, "icon.png is not a PNG");
  const w = png.readUInt32BE(16), h = png.readUInt32BE(20);
  must(w === h && w >= 128, `icon.png is ${w}x${h}, not a square of at least 128`);
  const svg = fs.readFileSync(process.argv[3], "utf8");
  must(svg.includes("<svg") && svg.includes("#0b0d0c") && svg.includes("#c3f36b"),
    "icons/axiom.svg is not the graphite and lime brand tile");
' "$ext/package.json" "$ext/icon.png" "$ext/icons/axiom.svg" 2>"$work/icon.err"; then
  ok "icon.png is a square PNG of at least 128px and icons/axiom.svg is the brand tile, for both themes"
else
  bad "the icon: $(cat "$work/icon.err")"
fi

# --------------------------------------------------------------------
echo "--- package.json states VERSION ---"
want="$(tr -d '[:space:]' < "$repo_root/VERSION")"
have="$("$node" -p 'require(process.argv[1]).version' "$ext/package.json")"
if [[ "$have" == "$want" ]]; then
  ok "package.json version $have is VERSION"
else
  bad "package.json version is $have, VERSION is $want"
fi

if grep -q '^editors/vscode/package.json|' "$repo_root/scripts/lib/version-sites.sh"; then
  ok "version-sites.sh lists editors/vscode/package.json, so a bump moves it"
else
  bad "version-sites.sh does not list editors/vscode/package.json"
fi

# --------------------------------------------------------------------
echo "--- the head keywords are the parser's ---"
#
# The same extraction tests/lsp/drive.py uses: the string literals at
# `kwEq name "..."` sites, the `kwFn`-style constants at the others
# (resolved through their `(pub fn (kwX) "x")` definitions), and the
# `lspKeywords` menu. Written again here rather than imported from the
# sync script, so that a bug in the script's extraction is a
# disagreement this gate can see.
derive_keywords() {
  {
    grep -oE 'kwEq [A-Za-z0-9_]+ "[^"]+"' "$parser_ax" | sed -E 's/.*"([^"]+)"/\1/'
    for name in $(grep -oE 'kwEq [A-Za-z0-9_]+ kw[A-Za-z]+' "$parser_ax" | awk '{print $3}' | sort -u); do
      def="$(grep -A1 -F "(pub fn ($name)" "$parser_ax" | grep -oE '"[^"]+"' | head -1 | tr -d '"')"
      if [[ -z "$def" ]]; then
        echo "FAIL: parser.ax compares against $name, whose spelling could not be read" >&2
        return 1
      fi
      echo "$def"
    done
    awk '/\(pub fn \(lspKeywords\)/ { on = 1; next } on && /^\(/ { exit } on' "$lsp_ax" \
      | grep -oE 'vecPush v "[^"]+"' | sed -E 's/.*"([^"]+)"/\1/'
    # The heads the grammar must colour whatever the sources say.
    printf '%s\n' fn let if match data struct macro pub import extern effect \
      handle region parallel while for lambda set type subtype syntax/for
  } | grep -E '^[A-Za-z]' | LC_ALL=C sort -u
}

# The heads the parser names only to refuse (AX2004), coloured as the
# error they will be. The same list sits in sync-keywords.mjs.
removed_expected="$(printf '%s\n' union foreign trait impl cond begin alloc consume define try emacro cast | LC_ALL=C sort)"

# Print a rule's alternation, one word per line. The match is
# `\G\s*(?:(pub)\s+)?(a|b|c)(?!...)`, and the group after `)?` is it.
grammar_words() { # grammar_words <grammar.json> <rule>
  "$node" -e '
    const g = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
    const rule = g.repository[process.argv[2]];
    if (!rule || !rule.match) { console.error("no match in rule " + process.argv[2]); process.exit(1); }
    const m = rule.match.match(/\)\?\(([^()]+)\)\(\?!/);
    if (!m) { console.error("rule " + process.argv[2] + " has no alternation to read"); process.exit(1); }
    for (const w of m[1].split("|")) console.log(w);
  ' "$1" "$2" | LC_ALL=C sort -u
}

# compare_keywords <grammar.json>: 0 when the grammar's two
# alternations together are exactly the derived set, 1 otherwise, with
# the differences on stdout. A function so the ablation below runs the
# real comparison on a doctored copy.
compare_keywords() {
  local g="$1" live removed both
  live="$(grammar_words "$g" keyword-head)" || return 1
  removed="$(grammar_words "$g" keyword-removed)" || return 1
  both="$(printf '%s\n%s\n' "$live" "$removed" | LC_ALL=C sort -u)"
  local rc=0
  if [[ "$both" != "$derived" ]]; then
    echo "  only in the parser/lsp: $(comm -23 <(echo "$derived") <(echo "$both") | tr '\n' ' ')"
    echo "  only in the grammar:    $(comm -13 <(echo "$derived") <(echo "$both") | tr '\n' ' ')"
    rc=1
  fi
  if [[ -n "$(comm -12 <(echo "$live") <(echo "$removed"))" ]]; then
    echo "  in both alternations: $(comm -12 <(echo "$live") <(echo "$removed") | tr '\n' ' ')"
    rc=1
  fi
  if [[ -n "$(comm -23 <(echo "$removed") <(echo "$removed_expected"))" ]]; then
    echo "  removed but not a refused head: $(comm -23 <(echo "$removed") <(echo "$removed_expected") | tr '\n' ' ')"
    rc=1
  fi
  if [[ -n "$(comm -12 <(echo "$live") <(echo "$removed_expected"))" ]]; then
    echo "  live but refused by the parser: $(comm -12 <(echo "$live") <(echo "$removed_expected") | tr '\n' ' ')"
    rc=1
  fi
  return $rc
}

derived="$(derive_keywords)" || exit 1
n_derived="$(echo "$derived" | grep -c .)"
if (( n_derived >= 20 )); then
  ok "derived $n_derived head keywords from parser.ax and lsp.ax"
else
  bad "derived only $n_derived head keywords; the extraction has stopped matching"
fi

if out="$(compare_keywords "$grammar")"; then
  ok "the grammar's keyword alternations are exactly the derived set"
else
  bad "the grammar's keyword alternations differ from the derived set"
  echo "$out"
fi

if (cd "$ext" && "$node" scripts/sync-keywords.mjs --check >"$work/sync.out" 2>&1); then
  ok "sync-keywords.mjs is a fixed point: $(cat "$work/sync.out")"
else
  bad "sync-keywords.mjs would change the grammar: $(cat "$work/sync.out")"
fi

# Ablation: drop one keyword from a copy and require the comparison to
# see it. `while` is in every version of the set.
sed 's/|while)/)/' "$grammar" > "$work/doctored.json"
if grep -q '|while)' "$work/doctored.json" || ! grep -q '|while)' "$grammar"; then
  bad "ablation could not remove 'while' from a copy of the grammar"
elif compare_keywords "$work/doctored.json" >/dev/null; then
  bad "ablation: a grammar missing 'while' passed the comparison"
else
  ok "ablation: a grammar missing 'while' is caught"
fi

# Symbolic forms are operators, rather than identifier keywords. Verify
# the new pipeline as a whole token and refuse an empty regex alternative.
if "$node" -e '
  const grammar = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
  const expression = new RegExp(grammar.repository["operator-head"].match.replace("\\G", "^"));
  const pipeline = expression.exec("|> value");
  if (!pipeline || pipeline[1] !== "|>" || expression.test("name")) process.exit(1);
' "$grammar"; then
  ok "the pipeline is one operator token and operator-head rejects an identifier"
else
  bad "operator-head does not recognise the pipeline exactly"
fi

# --------------------------------------------------------------------
echo "--- the semantic token legend is mapped ---"
legend_from() { # legend_from <function name>
  awk -v fn="$1" '$0 ~ "\\(pub fn \\(" fn "\\)" { on = 1; next } on && /^\(/ { exit } on' "$lsp_ax" \
    | grep -oE 'vecPush v "[^"]+"' | sed -E 's/.*"([^"]+)"/\1/'
}
if grep -q '(pub fn (lspSemanticTypes)' "$lsp_ax" && grep -q '(pub fn (lspSemanticModifiers)' "$lsp_ax"; then
  legend_types="$(legend_from lspSemanticTypes)"
  legend_mods="$(legend_from lspSemanticModifiers)"
  legend_source="self_host/lsp.ax"
else
  # The agreed legend, until the server side lands. When it does, the
  # branch above reads the real one and this list stops mattering.
  legend_types="$(printf '%s\n' namespace type typeParameter parameter variable property enumMember \
    function macro keyword comment string number operator decorator effect region)"
  legend_mods="$(printf '%s\n' declaration definition readonly modification deprecated documentation defaultLibrary)"
  legend_source="the agreed list in this gate (lsp.ax has no lspSemanticTypes yet)"
fi
n_types="$(echo "$legend_types" | grep -c .)"
n_mods="$(echo "$legend_mods" | grep -c .)"
if (( n_types >= 10 && n_mods >= 5 )); then
  ok "legend: $n_types types and $n_mods modifiers, from $legend_source"
else
  bad "legend: $n_types types and $n_mods modifiers from $legend_source; too few to be the legend"
fi

# The scopes map, as `type` or `type.modifier` keys, one per line.
mapped="$("$node" -e '
  const p = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
  const entries = (p.contributes && p.contributes.semanticTokenScopes) || [];
  const e = entries.find((x) => x.language === "axiom");
  if (!e) { console.error("no semanticTokenScopes entry for language axiom"); process.exit(1); }
  for (const [k, v] of Object.entries(e.scopes)) {
    if (!Array.isArray(v) || v.length === 0) { console.error("empty scope list for " + k); process.exit(1); }
    console.log(k);
  }
' "$ext/package.json")" || { bad "package.json has no usable semanticTokenScopes for axiom"; mapped=""; }

# Every legend type has a bare mapping.
missing=""
while read -r t; do
  [[ -z "$t" ]] && continue
  echo "$mapped" | grep -qx "$t" || missing="$missing $t"
done <<< "$legend_types"
if [[ -z "$missing" ]]; then
  ok "every legend type is mapped to a TextMate scope"
else
  bad "legend types with no mapping:$missing"
fi

# Every mapping names a legend type and, when it has one, a legend
# modifier.
unknown=""
while read -r k; do
  [[ -z "$k" ]] && continue
  t="${k%%.*}"
  m=""
  [[ "$k" == *.* ]] && m="${k#*.}"
  echo "$legend_types" | grep -qx "$t" || unknown="$unknown $k"
  if [[ -n "$m" ]]; then
    echo "$legend_mods" | grep -qx "$m" || unknown="$unknown $k"
  fi
done <<< "$mapped"
if [[ -z "$unknown" ]]; then
  ok "every mapping names a legend type and modifier ($(echo "$mapped" | grep -c .) mappings)"
else
  bad "mappings naming something outside the legend:$unknown"
fi

# The two custom types are declared with a superType, and the legend
# names them.
custom="$("$node" -e '
  const p = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
  for (const t of (p.contributes.semanticTokenTypes || [])) {
    if (!t.superType) { console.error(t.id + " has no superType"); process.exit(1); }
    console.log(t.id);
  }
' "$ext/package.json")" || bad "a custom semantic token type has no superType"
for t in effect region; do
  if echo "$custom" | grep -qx "$t" && echo "$legend_types" | grep -qx "$t"; then
    ok "custom token type '$t' is declared and in the legend"
  else
    bad "custom token type '$t' is not both declared in package.json and in the legend"
  fi
done

# --------------------------------------------------------------------
echo "--- the manifest's fixed points ---"
"$node" -e '
  const p = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
  const must = (c, m) => { if (!c) { console.error(m); process.exit(1); } };
  must(p.name === "axiom-language", "name is not axiom-language");
  must(p.main === "./extension.js", "main is not ./extension.js");
  must(Array.isArray(p.activationEvents) && p.activationEvents.includes("onLanguage:axiom"), "no onLanguage:axiom activation");
  const lang = (p.contributes.languages || []).find((l) => l.id === "axiom");
  must(lang && lang.extensions.includes(".ax"), "language axiom does not claim .ax");
  const gr = (p.contributes.grammars || []).find((g) => g.scopeName === "source.axiom");
  must(gr && gr.path === "./syntaxes/axiom.tmLanguage.json", "no grammar at ./syntaxes/axiom.tmLanguage.json");
  const cmds = (p.contributes.commands || []).map((c) => c.command);
  for (const c of ["axiom.run", "axiom.expandMacro", "axiom.doc", "axiom.restartServer"]) must(cmds.includes(c), "command " + c + " is not contributed");
  const props = (p.contributes.configuration || {}).properties || {};
  must(props["axiom.serverPath"] && props["axiom.serverPath"].default === "axiom", "axiom.serverPath does not default to axiom");
  must(props["axiom.trace.server"], "axiom.trace.server is not a setting");
  const deps = Object.keys(p.dependencies || {});
  must(deps.length === 1 && deps[0] === "vscode-languageclient", "dependencies are not exactly vscode-languageclient");
' "$ext/package.json" 2>"$work/manifest.err" && ok "package.json contributes the language, grammar, four commands and two settings" \
  || bad "package.json: $(cat "$work/manifest.err")"

if "$node" -e '
  const g = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
  if (g.scopeName !== "source.axiom") process.exit(1);
  for (const r of ["comment-block", "axtag", "comment-doc", "string", "form", "keyword-head", "keyword-removed"])
    if (!g.repository[r]) { console.error("no rule " + r); process.exit(1); }
' "$grammar"; then
  ok "the grammar is source.axiom and carries its named rules"
else
  bad "the grammar is missing a rule the extension's README describes"
fi

# --------------------------------------------------------------------
echo
echo "check-vscode: $checks checks, $failed failed"
(( failed == 0 )) || exit 1
exit 0
