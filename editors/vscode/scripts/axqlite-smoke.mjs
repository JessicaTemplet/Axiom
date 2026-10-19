// The AXQLite reader against the format's own worked example, plus a
// hand-built one-table database. Run: node scripts/axqlite-smoke.mjs
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const axqlite = require("../axqlite.js");
const { renderDatabase } = require("../axqlite-view.js");
const manifest = require("../package.json");
const iconTheme = require("../icons/file-icon-theme.json");
import fs from "fs";
import os from "os";
import path from "path";

let failed = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`${ok ? "ok" : "FAIL"} ${name}${ok ? "" : `: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`}`);
  if (!ok) failed++;
}

// docs/axqlite-format.md: [NULL, -2, 1.5, 'ab', x''] is 33 bytes.
const example = Buffer.from(
  "0005 00 01 02 03 04 fffffffffffffffe 3ff8000000000000 00000002 6162 00000000".replace(/\s/g, ""), "hex");
const row = axqlite.decodeRecord(example);
check("record NULL", row[0], null);
check("record INTEGER", axqlite.renderValue(row[1]), "-2");
check("record REAL", row[2], 1.5);
check("record TEXT", row[3], "ab");
check("record BLOB", axqlite.renderValue(row[4]), "x''");

check("columns", axqlite.parseColumns("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, score REAL)"),
  ["id", "name", "score"]);
check("columns skip constraints", axqlite.parseColumns("CREATE TABLE t (a INTEGER, PRIMARY KEY (a))"), ["a"]);

// A minimal database: header page + a schema leaf holding one record
// [TEXT 'table', TEXT 't', TEXT 't', INTEGER 2, TEXT 'CREATE TABLE t (a INTEGER)'],
// and page 2 a table leaf with one row keyed by rowid 1: [INTEGER 42].
function record(values) {
  const types = { null: 0, int: 1, real: 2, text: 3, blob: 4 };
  const head = Buffer.alloc(2 + values.length);
  head.writeUInt16BE(values.length, 0);
  values.forEach((v, i) => { head[2 + i] = types[v.k]; });
  const parts = [head];
  for (const v of values) {
    if (v.k === "int") { const b = Buffer.alloc(8); b.writeBigInt64BE(BigInt(v.v)); parts.push(b); }
    if (v.k === "real") { const b = Buffer.alloc(8); b.writeDoubleBE(v.v); parts.push(b); }
    if (v.k === "text") { const b = Buffer.from(v.v); const l = Buffer.alloc(4); l.writeUInt32BE(b.length); parts.push(l, b); }
  }
  return Buffer.concat(parts);
}
function leaf(cells) {
  const p = Buffer.alloc(4096);
  p[0] = 1;
  p.writeUInt16BE(cells.length, 2);
  let at = 4088;
  cells.forEach((cell, i) => {
    at -= cell.length;
    cell.copy(p, at);
    p.writeUInt16BE(at, 12 + 2 * i);
  });
  return p;
}
function cell(key, value) {
  const c = Buffer.alloc(6 + key.length + value.length);
  c.writeUInt16BE(key.length, 0);
  c.writeUInt32BE(value.length, 2);
  key.copy(c, 6);
  value.copy(c, 6 + key.length);
  return c;
}
const header = Buffer.alloc(4096);
header.write(axqlite.MAGIC, 0, "latin1");
header.writeUInt32BE(1, 20);
header.writeUInt32BE(4096, 24);
header.writeUInt32BE(3, 28);
header.writeUInt32BE(1, 48);
const rowid1 = Buffer.from("8000000000000001", "hex");
const schema = leaf([cell(rowid1, record([
  { k: "text", v: "table" }, { k: "text", v: "t" }, { k: "text", v: "t" },
  { k: "int", v: 2 }, { k: "text", v: "CREATE TABLE t (a INTEGER)" },
]))]);
const table = leaf([cell(rowid1, record([{ k: "int", v: 42 }]))]);
const file = path.join(os.tmpdir(), "axqlite-smoke.db");
fs.writeFileSync(file, Buffer.concat([header, schema, table]));

const db = axqlite.readDatabase(file);
check("header pages", db.header.pageCount, 3);
check("tables", db.tables.map((t) => [t.name, t.rootPage, t.columns]), [["t", 2, ["a"]]]);
check("rows", axqlite.readTableRows(file, 2).map((r) => [r.rowid, axqlite.renderValue(r.values[0])]), [[1, "42"]]);

const preview = renderDatabase(file);
check("preview limits are visible", preview.includes("Up to 100 rows per table"), true);
check("preview links to its table", preview.includes('href="#object-0"'), true);
check("preview contains the row", preview.includes('<td>42</td>'), true);
check("preview is read only", preview.includes("Read only"), true);
check("preview has a search label", preview.includes('aria-label="Filter preview rows"'), true);
const nonce = /script-src 'nonce-([a-f0-9]+)'/.exec(preview)?.[1];
check("preview script and style use its CSP nonce", !!nonce && preview.includes(`<script nonce="${nonce}">`) && preview.includes(`<style nonce="${nonce}">`), true);
check("preview refuses inline handlers", preview.includes("unsafe-inline"), false);
fs.writeFileSync(file, Buffer.concat([header, schema,
  leaf([cell(rowid1, record([{ k: "text", v: '<img src=x onerror="alert(1)">' }]))]) ]));
const escapedPreview = renderDatabase(file);
check("preview escapes database values", escapedPreview.includes("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"), true);
check("preview adds no data HTML", escapedPreview.includes('<img src=x'), false);
try {
  axqlite.readDatabase(path.join(os.tmpdir(), "axqlite-smoke-missing.db"));
  check("missing file refused", "no throw", "throw");
} catch { check("missing file refused", "throw", "throw"); }

// Language registrations supply fallback icons when a different file theme is active.
for (const [id, kind, names] of [
  ["axqlite-db", "database", [".db", ".axqlite"]],
  ["axql", "database", [".axql"]],
  ["axiom-package", "package", ["axiom.pkg", "axiom.axiom.pkg"]],
]) {
  const language = manifest.contributes.languages.find((entry) => entry.id === id);
  check(`${id} association`, [...(language?.extensions || []), ...(language?.filenames || [])], names);
  check(`${id} light icon`, language?.icon?.light, `./icons/${kind}.svg`);
  check(`${id} dark icon`, language?.icon?.dark, `./icons/${kind}.svg`);
  check(`${id} theme icon`, iconTheme.languageIds[id], kind);
  check(`${id} asset`, fs.existsSync(new URL(`../${language.icon.light}`, import.meta.url)), true);
}
check("unknown files keep their theme icon", iconTheme.file, undefined);

console.log(failed ? `${failed} check(s) failed` : "axqlite smoke ok");
process.exit(failed ? 1 : 0);
