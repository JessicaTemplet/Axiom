#!/usr/bin/env node
// Print the TextMate scopes the grammar gives each token of a file, the
// way VS Code's "Inspect Editor Tokens and Scopes" would, without
// VS Code. Needs `vscode-textmate` and `vscode-oniguruma`, which are
// not dependencies of the extension; install them anywhere and name
// that directory:
//
//   npm install --prefix /tmp/tm vscode-textmate vscode-oniguruma
//   node editors/vscode/scripts/tokens-smoke.mjs tests/lsp/060-outline.ax /tmp/tm
//
// The second argument defaults to the extension directory itself.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const file = process.argv[2];
if (!file) {
  console.error("usage: tokens-smoke.mjs <file.ax> [dir-with-node_modules]");
  process.exit(2);
}
const modules = resolve(process.argv[3] || join(here, ".."));
const req = createRequire(join(modules, "package.json"));
let tm, onig;
try {
  tm = req("vscode-textmate");
  onig = req("vscode-oniguruma");
} catch (e) {
  console.error(`vscode-textmate and vscode-oniguruma are not installed under ${modules}: ${e.message}`);
  process.exit(2);
}

const wasm = readFileSync(req.resolve("vscode-oniguruma/release/onig.wasm"));
const onigLib = onig.loadWASM(wasm.buffer).then(() => ({
  createOnigScanner: (s) => new onig.OnigScanner(s),
  createOnigString: (s) => new onig.OnigString(s),
}));
const grammarPath = join(here, "..", "syntaxes", "axiom.tmLanguage.json");
const registry = new tm.Registry({
  onigLib,
  loadGrammar: async (scope) =>
    scope === "source.axiom" ? tm.parseRawGrammar(readFileSync(grammarPath, "utf8"), grammarPath) : null,
});

const grammar = await registry.loadGrammar("source.axiom");
let state = tm.INITIAL;
for (const line of readFileSync(file, "utf8").split("\n")) {
  const r = grammar.tokenizeLine(line, state);
  for (const t of r.tokens) {
    const text = line.slice(t.startIndex, t.endIndex);
    if (text.trim() === "") continue;
    // The innermost scopes are the informative ones; `source.axiom` and
    // the `meta.*` nesting are the same on every token.
    const scopes = t.scopes.filter((s) => s !== "source.axiom" && !s.startsWith("meta."));
    console.log(`${JSON.stringify(text).padEnd(28)} ${scopes.join(" ")}`);
  }
  state = r.ruleStack;
}
