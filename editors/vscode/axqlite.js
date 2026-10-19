"use strict";
// A read-only AXQLite reader in plain Node, with no dependencies.
// The byte layout is docs/axqlite-format.md: 4096-byte pages, a header
// page, slotted B-tree pages rooted at page 1 for the schema, and
// records of typed values in column order. Nothing here writes.
const fs = require("fs");

const PAGE = 4096;
const MAGIC = "AXQLite format 1";

function fail(code, message) {
  const e = new Error(message);
  e.code = code;
  return e;
}

function readHeader(buf) {
  if (buf.length < PAGE || buf.toString("latin1", 0, 16) !== MAGIC) {
    throw fail("axqNotADatabase", "not an AXQLite database (bad magic)");
  }
  const version = buf.readUInt32BE(20);
  if (version !== 1) throw fail("axqNotADatabase", `unsupported AXQLite format version ${version}`);
  const pageSize = buf.readUInt32BE(24);
  if (pageSize !== PAGE) throw fail("axqCorrupt", `unexpected page size ${pageSize}`);
  return {
    version,
    pageSize,
    pageCount: buf.readUInt32BE(28),
    freeListHead: buf.readUInt32BE(32),
    freeListPages: buf.readUInt32BE(36),
    changeCounter: Number(buf.readBigUInt64BE(40)),
    schemaRoot: buf.readUInt32BE(48),
    schemaVersion: Number(buf.readBigUInt64BE(56)),
  };
}

function page(buf, n) {
  const at = n * PAGE;
  if (n < 0 || at + PAGE > buf.length) throw fail("axqCorrupt", `page ${n} is outside the file`);
  return buf.subarray(at, at + PAGE);
}

// Every value of one B-tree, in key order: [{key: Buffer, value: Buffer}].
function readTree(buf, root, depth = 0) {
  if (depth > 40) throw fail("axqCorrupt", "B-tree deeper than 40 levels");
  const p = page(buf, root);
  const type = p[0];
  const cells = p.readUInt16BE(2);
  const offsets = [];
  for (let i = 0; i < cells; i++) offsets.push(p.readUInt16BE(12 + 2 * i));
  const out = [];
  if (type === 1) {
    for (const off of offsets) {
      const k = p.readUInt16BE(off);
      const v = p.readUInt32BE(off + 2);
      const head = 6 + k;
      const inline = 6 + k + v <= 1016 ? v : 1006 - k;
      let value = Buffer.from(p.subarray(off + head, off + head + inline));
      if (inline < v) {
        let next = p.readUInt32BE(off + head + inline);
        while (value.length < v) {
          if (next === 0) throw fail("axqCorrupt", "overflow chain ends early");
          const op = page(buf, next);
          if (op[0] !== 5) throw fail("axqCorrupt", "overflow page has the wrong type");
          const used = op.readUInt16BE(2);
          value = Buffer.concat([value, op.subarray(8, 8 + used)]);
          next = op.readUInt32BE(4);
        }
        value = value.subarray(0, v);
      }
      out.push({ key: Buffer.from(p.subarray(off + 6, off + 6 + k)), value });
    }
    return out;
  }
  if (type === 2) {
    for (const off of offsets) {
      const child = p.readUInt32BE(off);
      out.push(...readTree(buf, child, depth + 1));
    }
    const right = p.readUInt32BE(8);
    if (right !== 0) out.push(...readTree(buf, right, depth + 1));
    return out;
  }
  throw fail("axqCorrupt", `page ${root} is not a table B-tree page (type ${type})`);
}

const TYPES = ["NULL", "INTEGER", "REAL", "TEXT", "BLOB"];

// One row's values, in column order. Integers answer as BigInt so a
// value past 2^53 is shown whole; the caller renders them.
function decodeRecord(value) {
  if (value.length < 2) throw fail("axqCorrupt", "short record");
  const cols = value.readUInt16BE(0);
  if (value.length < 2 + cols) throw fail("axqCorrupt", "short record header");
  const types = [];
  for (let i = 0; i < cols; i++) {
    const t = value[2 + i];
    if (t >= TYPES.length) throw fail("axqCorrupt", `unknown record type code ${t}`);
    types.push(t);
  }
  let at = 2 + cols;
  const row = [];
  for (const t of types) {
    if (t === 0) row.push(null);
    else if (t === 1) { row.push(value.readBigInt64BE(at)); at += 8; }
    else if (t === 2) { row.push(value.readDoubleBE(at)); at += 8; }
    else {
      const len = value.readUInt32BE(at); at += 4;
      if (at + len > value.length) throw fail("axqCorrupt", "short record payload");
      const bytes = value.subarray(at, at + len); at += len;
      row.push(t === 3 ? bytes.toString("utf8") : bytes);
    }
  }
  if (at !== value.length) throw fail("axqCorrupt", "record has leftover bytes");
  return row;
}

// Column names out of a CREATE statement: the first word of each
// top-level comma-separated part inside the parentheses, skipping
// table constraints. AXQLite stores no other column list.
function parseColumns(sql) {
  if (!sql) return [];
  const open = sql.indexOf("(");
  const close = sql.lastIndexOf(")");
  if (open < 0 || close <= open) return [];
  const parts = [];
  let depth = 0, start = open + 1;
  for (let i = open + 1; i < close; i++) {
    if (sql[i] === "(") depth++;
    else if (sql[i] === ")") depth--;
    else if (sql[i] === "," && depth === 0) { parts.push(sql.slice(start, i)); start = i + 1; }
  }
  parts.push(sql.slice(start, close));
  const skip = new Set(["PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"]);
  const names = [];
  for (const part of parts) {
    const word = part.trim().split(/\s+/)[0];
    if (word && !skip.has(word.toUpperCase())) names.push(word.replace(/^["'`[]|["'`\]]$/g, ""));
  }
  return names;
}

// {header, tables: [{kind, name, table, rootPage, sql, columns}]}
// plus rowCounts filled by readTableRows callers, not here: reading a
// schema never walks a table's rows.
function readDatabase(file) {
  const buf = fs.readFileSync(file);
  const header = readHeader(buf);
  const tables = [];
  for (const { value } of readTree(buf, header.schemaRoot)) {
    const row = decodeRecord(value);
    const [kind, name, table, rootPage, sql] = row;
    tables.push({
      kind: String(kind), name: String(name), table: String(table),
      rootPage: Number(rootPage), sql: sql == null ? "" : String(sql),
      columns: parseColumns(sql == null ? "" : String(sql)),
    });
  }
  return { header, tables };
}

// Up to `limit` rows of one table: [{rowid, values}] in rowid order.
function readTableRows(file, rootPage, limit = 200) {
  const buf = fs.readFileSync(file);
  readHeader(buf);
  const rows = [];
  for (const { key, value } of readTree(buf, rootPage)) {
    if (rows.length >= limit) break;
    // A table key is the rowid's 8 bytes with the sign bit flipped.
    const rowid = key.length === 8 ? Number(key.readBigInt64BE(0) ^ (-9223372036854775808n)) : null;
    rows.push({ rowid, values: decodeRecord(value) });
  }
  return rows;
}

function renderValue(v) {
  if (v === null || v === undefined) return "NULL";
  if (typeof v === "bigint") return v.toString();
  if (Buffer.isBuffer(v)) return `x'${v.toString("hex")}'`;
  return String(v);
}

module.exports = { readDatabase, readTableRows, decodeRecord, parseColumns, renderValue, MAGIC };
