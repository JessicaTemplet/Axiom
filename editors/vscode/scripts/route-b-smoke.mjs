#!/usr/bin/env node
// Exercise the real TextMate engine for block locals and named fields.
// An optional argument names a directory containing the two test engines.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const modules = resolve(process.argv[2] || join(here, ".."));
const req = createRequire(join(modules, "package.json"));
const tm = req("vscode-textmate");
const onig = req("vscode-oniguruma");
const wasm = readFileSync(req.resolve("vscode-oniguruma/release/onig.wasm"));
await onig.loadWASM(wasm.buffer);
const path = join(here, "..", "syntaxes", "axiom.tmLanguage.json");
const registry = new tm.Registry({
  onigLib: Promise.resolve({
    createOnigScanner: (patterns) => new onig.OnigScanner(patterns),
    createOnigString: (text) => new onig.OnigString(text),
  }),
  loadGrammar: async (scope) => scope === "source.axiom"
    ? tm.parseRawGrammar(readFileSync(path, "utf8"), path) : null,
});
const grammar = await registry.loadGrammar("source.axiom");
let checks = 0;
function expect(line, text, scope, occurrence = 0) {
  const result = grammar.tokenizeLine(line, tm.INITIAL);
  let offset = -1;
  for (let i = 0; i <= occurrence; i++) offset = line.indexOf(text, offset + 1);
  assert.ok(offset >= 0, `missing ${JSON.stringify(text)} in ${line}`);
  const token = result.tokens.find((item) => item.startIndex <= offset
    && item.endIndex >= offset + text.length);
  assert.ok(token?.scopes.includes(scope),
    `${JSON.stringify(text)} in ${line}: expected ${scope}, got ${token?.scopes.join(" ")}`);
  checks++;
}
const local = "(let point (Point :y 20 :x input))";
expect(local, "let", "keyword.control.axiom");
expect(local, "point", "variable.other.axiom");
expect(local, "Point", "entity.name.type.constructor.axiom");
expect(local, "y", "variable.other.property.axiom");
expect(local, "x", "variable.other.property.axiom");
expect(local, "20", "constant.numeric.integer.axiom");
const mutable = "(let mut Total (compute value))";
expect(mutable, "mut", "keyword.other.axiom");
expect(mutable, "Total", "variable.other.axiom");
expect(mutable, "compute", "entity.name.function.call.axiom");
expect("(let _ (compute value))", "_", "variable.language.wildcard.axiom");
expect("(let mut _ (compute value))", "_", "variable.language.wildcard.axiom");
expect("(let _kept 1)", "_kept", "variable.other.axiom");
expect("(Point :mut 1 :Int 2)", "mut", "variable.other.property.axiom");
expect("(Point :mut 1 :Int 2)", "Int", "variable.other.property.axiom");
expect("(Point :x -5)", "-5", "constant.numeric.integer.axiom");
expect("(Geom::Point :x 1)", "Geom", "entity.name.namespace.axiom");
expect("(Geom::Point :x 1)", "x", "variable.other.property.axiom");
expect("(struct Point (x : Int))", "Int", "support.type.builtin.axiom");
expect('(Point :x "literal :hidden 1")', ":hidden", "string.quoted.double.axiom");
expect("(Point :x 1) ; :hidden", ":hidden", "comment.line.semicolon.axiom");

const snippets = JSON.parse(readFileSync(join(here, "..", "snippets", "axiom.json"), "utf8"));
assert.deepEqual(snippets.Let.body, ["(let ${1:x} ${2:value})"]);
assert.deepEqual(snippets["Mutable local"].body, ["(let mut ${1:x} ${2:value})"]);
checks += 2;
console.log(`${checks} local, constructor-label and snippet checks passed`);
