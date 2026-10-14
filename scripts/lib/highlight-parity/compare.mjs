// Compare tree-sitter's colouring of each file with VS Code's, character
// by character, and print one report line per finding.
//
// VS Code's colour is the language server's semantic token where it sends
// one, mapped through the extension's `semanticTokenScopes`, and the
// TextMate grammar's scope everywhere else. Tree-sitter's is what its own
// highlighter answers (`tree-sitter highlight`, which reads locals.scm as
// Helix does). Both sides are reduced to one ROLE per character by the
// two tables below; a theme colours the scopes and captures of one role
// alike.
//
// Usage: node compare.mjs REPO GRAMMAR_DIR TSCONFIG SEM.json FILE...
// Output: `RUNS n AGREE m`, `OVERLAP n` (a node captured twice), and one
// `PAIR <ts role> <vs role> <count> <example>` per disagreement.
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const [repo, grammarArg, tsconfig, semFile, ...files] = process.argv.slice(2);
const grammarDir = resolve(grammarArg);
const here = dirname(fileURLToPath(import.meta.url));
const req = createRequire(join(here, "package.json"));
const tm = req("vscode-textmate"), onig = req("vscode-oniguruma");
const onigLib = onig.loadWASM(readFileSync(req.resolve("vscode-oniguruma/release/onig.wasm")).buffer)
  .then(() => ({ createOnigScanner: (s) => new onig.OnigScanner(s), createOnigString: (s) => new onig.OnigString(s) }));
const registry = new tm.Registry({ onigLib, loadGrammar: async () =>
  tm.parseRawGrammar(readFileSync(join(repo, "editors/vscode/syntaxes/axiom.tmLanguage.json"), "utf8"), "axiom.tmLanguage.json") });
const grammar = await registry.loadGrammar("source.axiom");
const sem = JSON.parse(readFileSync(semFile, "utf8"));
const pkg = JSON.parse(readFileSync(join(repo, "editors/vscode/package.json"), "utf8"));
const semScopes = Object.entries(pkg.contributes.semanticTokenScopes[0].scopes)
  .map(([k, v]) => { const [ty, ...ms] = k.split("."); return { ty, ms, scope: v[0] }; });
const ts = join(repo, "tree-sitter-axiom/node_modules/.bin/tree-sitter");

// TextMate scope -> role: the first prefix that matches, innermost scope
// first. A string's escapes and interpolations are the string's, as the
// server paints the whole literal; every `keyword.operator.*` is an
// operator; an effect is painted through `support.type`/`entity.name.type`,
// so it is a type's role.
const TEXTMATE = [
  ["comment.block.documentation", "doc"], ["comment", "comment"],
  ["entity.other.attribute-name", "attribute"], ["string.other.axtag", "attribute"], ["punctuation.definition.annotation", "attribute"],
  ["constant.character.escape", "string"], ["variable.other.interpolation", "string"], ["constant.other.format-spec", "string"],
  ["punctuation.definition.interpolation", "string"], ["punctuation.separator.format", "string"],
  ["string", "string"], ["constant.character", "string"], ["constant.numeric", "number"], ["constant.language", "boolean"],
  ["invalid", "error"], ["variable.language", "variable.builtin"],
  ["support.type.effect", "type.builtin"], ["entity.name.type.effect", "type"],
  ["keyword.operator", "operator"], ["keyword", "keyword"], ["storage.modifier", "keyword"],
  ["support.type.builtin", "type.builtin"], ["entity.name.type.parameter", "type.param"], ["entity.name.type.constructor", "constructor"], ["entity.name.type", "type"],
  ["variable.parameter", "parameter"], ["variable.other.property", "field"], ["variable.other", "variable"], ["support.variable", "variable"],
  ["entity.name.function.macro", "macro"], ["support.function", "function"], ["entity.name.function", "function"],
  ["entity.name.namespace", "namespace"], ["entity.name.label", "label"], ["punctuation", "punct"]];
// tree-sitter capture -> role. `function.call` is `function`, as
// `entity.name.function.call` is `entity.name.function` to every theme.
const TREESITTER = [
  ["comment.documentation", "doc"], ["comment", "comment"], ["attribute", "attribute"],
  ["string", "string"], ["number", "number"], ["boolean", "boolean"], ["keyword", "keyword"], ["operator", "operator"],
  ["punctuation", "punct"], ["type.builtin", "type.builtin"], ["type.parameter", "type.param"], ["type", "type"],
  ["constructor", "constructor"], ["function.macro", "macro"], ["function", "function"],
  ["variable.parameter", "parameter"], ["variable.member", "field"], ["variable.builtin", "variable.builtin"], ["variable", "variable"],
  ["module", "namespace"], ["label", "label"], ["error", "error"]];
const roleOf = (table, name) => { for (const [p, r] of table) if (name === p || name.startsWith(p + ".")) return r; return null; };
const SKIP = /^(meta\.|source\.|punctuation\.definition\.(comment|string))/;
const semScope = (ty, mods) => {
  let best = null;
  for (const e of semScopes) if (e.ty === ty && e.ms.every((m) => mods.includes(m)) && (!best || e.ms.length > best.ms.length)) best = e;
  return best ? best.scope : null;
};
const unescape = (t) => t.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");
const run = (args) => execFileSync(ts, args, { cwd: grammarDir, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], maxBuffer: 1 << 28 });

const pairs = new Map(); let runs = 0, agree = 0, overlaps = 0;
for (const file of files) {
  const lines = readFileSync(file, "utf8").split("\n");

  // A node with two captures colours differently in Neovim (last wins)
  // and Helix (first wins), so the queries capture each node once.
  const seen = new Map();
  for (const l of run(["query", join(grammarDir, "queries/highlights.scm"), resolve(file)]).split("\n")) {
    const m = l.match(/capture: \d+ - ([\w.]+), start: \((\d+), (\d+)\), end: \((\d+), (\d+)\)/);
    if (!m) continue;
    const key = `${m[2]}:${m[3]}-${m[4]}:${m[5]}`, prev = seen.get(key);
    if (prev && prev !== m[1]) { overlaps++; console.log(`OVERLAP_AT ${file}:${+m[2] + 1}:${+m[3] + 1} ${prev} ${m[1]}`); }
    else seen.set(key, m[1]);
  }

  // Tree-sitter's colour per character, from its highlighter's HTML.
  const tsAt = lines.map((l) => new Array(l.length).fill(null));
  const html = run(["highlight", "-p", ".", "--config-path", tsconfig, "--html", "--css-classes", resolve(file)]);
  [...html.matchAll(/<td class=line>([\s\S]*?)<\/td><\/tr>/g)].forEach((m, r) => {
    if (r >= lines.length) return;
    let c = 0; const stack = []; const re = /<span class='([^']*)'>|<\/span>|([^<]+)/g; let t;
    while ((t = re.exec(m[1].replace(/\n$/, "")))) {
      if (t[1] !== undefined) stack.push(t[1].replace(/ /g, "."));
      else if (t[0] === "</span>") stack.pop();
      else {
        // Columns are UTF-16 code units, as the protocol's and TextMate's
        // are, so a character outside the BMP takes two.
        const text = unescape(t[2]);
        for (let k = 0; k < text.length; k++, c++) if (c < tsAt[r].length && stack.length) tsAt[r][c] = stack[stack.length - 1];
      }
    }
  });

  // The server's colour per character.
  const semAt = lines.map((l) => new Array(l.length).fill(null));
  for (const [r, c, n, ty, mods] of sem[file] || []) {
    const scope = semScope(ty, mods), role = scope ? (roleOf(TEXTMATE, scope) || scope) : ty;
    for (let k = c; k < c + n && r < semAt.length && k < semAt[r].length; k++) semAt[r][k] = role;
  }

  // TextMate's, and the comparison, one run of like-coloured characters
  // at a time.
  let stack = tm.INITIAL;
  lines.forEach((line, r) => {
    const res = grammar.tokenizeLine(line, stack); stack = res.ruleStack;
    for (const tok of res.tokens) {
      let tmRole = "plain";
      for (let i = tok.scopes.length - 1; i >= 0; i--) {
        if (SKIP.test(tok.scopes[i])) continue;
        const ro = roleOf(TEXTMATE, tok.scopes[i]); if (ro) { tmRole = ro; break; }
      }
      const tsRoleAt = (c) => (tsAt[r][c] ? roleOf(TREESITTER, tsAt[r][c]) || tsAt[r][c] : "plain");
      let c = tok.startIndex;
      while (c < tok.endIndex) {
        if (/\s/.test(line[c])) { c++; continue; }
        const tsRole = tsRoleAt(c);
        let e = c + 1;
        while (e < tok.endIndex && !/\s/.test(line[e]) && tsRoleAt(e) === tsRole) e++;
        const vsRole = semAt[r][c] || tmRole;
        runs++;
        if (tsRole === vsRole) agree++;
        else {
          const key = `${tsRole} ${vsRole}`, p = pairs.get(key) || { n: 0, ex: `${file}:${r + 1}:${c + 1}:${JSON.stringify(line.slice(c, e))}` };
          p.n++; pairs.set(key, p);
        }
        c = e;
      }
    }
  });
}
console.log(`RUNS ${runs} AGREE ${agree}`);
console.log(`OVERLAP ${overlaps}`);
for (const [key, p] of [...pairs].sort((a, b) => b[1].n - a[1].n)) console.log(`PAIR ${key} ${p.n} ${p.ex}`);
