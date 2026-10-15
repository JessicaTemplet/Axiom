#!/usr/bin/env node
// Write the grammar's two keyword alternations from the compiler.
//
// The head keywords are whatever `self_host/parser.ax` dispatches on,
// read from its `kwEq name "..."` call sites and the `kwFn`-style
// constants they compare against, plus the menu `self_host/lsp.ax`
// offers in `lspKeywords`. The grammar's `keyword-head` and
// `keyword-removed` rules are rewritten from that set, so a keyword the
// parser learns reaches the editor without anyone typing it twice.
// `scripts/check-vscode.sh` recomputes the same set and requires the
// grammar to carry it, and requires this script to change nothing when
// it is run again.
//
//   node editors/vscode/scripts/sync-keywords.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..", "..");
const grammarPath = join(here, "..", "syntaxes", "axiom.tmLanguage.json");

// Heads the parser names only to refuse (AX2004). They parse, so the
// editor marks them as what the compiler will say they are.
const REMOVED = ["union", "foreign", "trait", "impl", "cond", "begin", "alloc", "consume", "define", "emacro", "cast"];

// Heads the grammar colours whatever the two sources say. They are all
// in the sources today; the list keeps a renamed call site from
// silently dropping one.
const FIXED = [
  "fn", "let", "if", "match", "data", "struct", "macro", "pub", "import",
  "extern", "effect", "handle", "region", "parallel", "while", "for", "lambda", "set",
  "type", "subtype", "syntax/for",
];

// The character that may continue an identifier, from `isIdentChar` in
// self_host/lexer.ax, plus `.`, `:` and `?`, which the lexer stops on
// but which must not follow a keyword either.
const BOUND = "(?![A-Za-z0-9_'!*+/<>=%&|^.:?-])";

export function derivedKeywords() {
  const parser = readFileSync(join(repo, "self_host", "parser.ax"), "utf8");
  const lsp = readFileSync(join(repo, "self_host", "lsp.ax"), "utf8");
  const defs = new Map();
  for (const m of parser.matchAll(/\(pub fn \((kw[A-Za-z]+)\)\s+"([^"]+)"\)/g)) defs.set(m[1], m[2]);
  const words = new Set(FIXED);
  for (const m of parser.matchAll(/kwEq\s+\w+\s+"([^"]+)"/g)) words.add(m[1]);
  for (const m of parser.matchAll(/kwEq\s+\w+\s+(kw[A-Za-z]+)/g)) {
    const w = defs.get(m[1]);
    if (!w) throw new Error(`parser.ax compares against ${m[1]}, whose spelling this script could not read`);
    words.add(w);
  }
  const body = lsp.slice(lsp.indexOf("(pub fn (lspKeywords)"));
  const end = body.indexOf("\n(");
  for (const m of body.slice(0, end < 0 ? undefined : end).matchAll(/\(vecPush v "([^"]+)"\)/g)) words.add(m[1]);
  return [...words].filter((word) => /^[A-Za-z]/.test(word)).sort();
}

export function alternation(words) {
  return "\\G\\s*(?:(pub)\\s+)?(" + words.join("|") + ")" + BOUND;
}

// With `write` false nothing is written; `changed` then says whether a
// write would have changed the file, which is what the gate asks.
export function sync(write = true) {
  const all = derivedKeywords();
  const removed = all.filter((w) => REMOVED.includes(w));
  const live = all.filter((w) => !REMOVED.includes(w));
  const grammar = JSON.parse(readFileSync(grammarPath, "utf8"));
  grammar.repository["keyword-head"].match = alternation(live);
  grammar.repository["keyword-removed"].match = alternation(removed);
  const text = JSON.stringify(grammar, null, 2) + "\n";
  const before = readFileSync(grammarPath, "utf8");
  if (write && text !== before) writeFileSync(grammarPath, text);
  return { live, removed, changed: text !== before };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const check = process.argv.includes("--check");
  const r = sync(!check);
  const verdict = check ? (r.changed ? "would change" : "up to date") : (r.changed ? "rewritten" : "unchanged");
  console.log(`${r.live.length} head keywords, ${r.removed.length} removed; grammar ${verdict}`);
  if (check && r.changed) process.exit(1);
}
