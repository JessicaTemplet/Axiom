#!/usr/bin/env node
// Exercise extension commands and client wiring without launching VS Code.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";

const nodeRequire = createRequire(import.meta.url);

const commands = new Map();
const watchers = [];
const clients = [];
const opened = [];
const errors = [];
const information = [];
const terminalCommands = [];
const token = { isCancellationRequested: false };
let request;
let progress;
let saves = true;
const disposable = () => ({ dispose() { this.disposed = true; } });
const document = {
  fileName: "/tmp/Axiom project/main.ax",
  uri: { toString: () => "file:///tmp/Axiom%20project/main.ax" },
  isDirty: true,
  async save() { return saves; },
};
class CancellationError extends Error {}
const State = { Running: 2, Starting: 3, Stopped: 1 };
const LSPErrorCodes = { RequestCancelled: -32800, ContentModified: -32801, ServerCancelled: -32802 };
class LanguageClient {
  constructor(id, name, server, options) {
    Object.assign(this, { id, name, server, options, stops: 0 });
    clients.push(this);
  }
  onDidChangeState(listener) { this.listener = listener; return disposable(); }
  async start() { this.listener({ newState: State.Running }); }
  async stop() { this.stops++; this.listener({ newState: State.Stopped }); }
  sendRequest(method, params, cancellation) {
    assert.equal(method, "axiom/expandMacro");
    assert.equal(params.textDocument.uri, document.uri.toString());
    assert.equal(cancellation, token, "macro expansion did not forward cancellation");
    return request();
  }
}
const output = disposable();
const vscode = {
  CancellationError,
  EventEmitter: class { constructor() { this.event = () => disposable(); } fire() {} },
  TreeItem: class { constructor(label, collapsibleState) { this.label = label; this.collapsibleState = collapsibleState; } },
  TreeItemCollapsibleState: { None: 0, Collapsed: 1 },
  ThemeIcon: class { constructor(id) { this.id = id; } },
  ProgressLocation: { Notification: 15 },
  StatusBarAlignment: { Left: 1 },
  commands: { registerCommand(name, handler) { commands.set(name, handler); return disposable(); } },
  workspace: {
    textDocuments: [document],
    getConfiguration: () => ({ get: (_key, fallback) => fallback }),
    createFileSystemWatcher(pattern) {
      const watcher = Object.assign(disposable(), { pattern });
      watchers.push(watcher);
      return watcher;
    },
    onDidChangeConfiguration: disposable,
    async openTextDocument(value) { opened.push(value); return value; },
    async findFiles() { return []; },
  },
  window: {
    activeTextEditor: { document, selection: { active: { line: 1, character: 2 } } },
    createOutputChannel: () => output,
    createStatusBarItem: () => ({ ...disposable(), show() {} }),
    createTerminal: () => ({ show() {}, sendText: (command) => terminalCommands.push(command) }),
    onDidCloseTerminal: disposable,
    showInformationMessage: (message) => information.push(message),
    showErrorMessage: (message) => errors.push(message),
    setStatusBarMessage() {},
    showTextDocument: async (_doc, options) => assert.equal(options.preview, true),
    withProgress: async (options, task) => { progress = options; return task({}, token); },
    registerTreeDataProvider: () => disposable(),
    registerCustomEditorProvider: () => disposable(),
  },
};
const module = { exports: {} };
const source = readFileSync(new URL("../extension.js", import.meta.url), "utf8");
vm.runInNewContext(source, { module, require: (name) => {
  if (name === "vscode") return vscode;
  if (name === "vscode-languageclient/node") return { LanguageClient, State, LSPErrorCodes };
  if (name === "./axqlite") return nodeRequire("../axqlite.js");
  throw new Error("unexpected module " + name);
}}, { filename: "extension.js" });
const context = { subscriptions: [] };
await module.exports.activate(context);
assert.deepEqual(watchers.map((w) => w.pattern), ["**/*.ax", "**/axiom.pkg"]);
assert.equal(clients.length, 1);
assert.equal(clients[0].server.command, "axiom");
assert.equal(JSON.stringify(clients[0].server.args), JSON.stringify(["lsp"]));
assert.equal(JSON.stringify(clients[0].options.documentSelector), JSON.stringify([{ scheme: "file", language: "axiom" }]));
assert.equal(clients[0].options.traceOutputChannel, output);
assert.deepEqual(Array.from(clients[0].options.synchronize.fileEvents), watchers);
const expand = commands.get("axiom.expandMacro");
request = async () => ({ name: "deriveTag", expansion: "(fn (tag) 7)" });
await expand();
assert.equal(progress.cancellable, true);
assert.equal(progress.location, vscode.ProgressLocation.Notification);
assert.equal(opened.length, 1);
assert.equal(opened[0].language, "axiom");
assert.match(opened[0].content, /\(fn \(tag\) 7\)/);
for (const code of Object.values(LSPErrorCodes)) {
  request = async () => { throw Object.assign(new Error("cancelled"), { code }); };
  await expand();
}
request = async () => { throw new CancellationError(); };
await expand();
assert.equal(errors.length, 0, "transient cancellation was shown as an error");
assert.equal(opened.length, 1);
token.isCancellationRequested = true;
request = async () => ({ name: "late", expansion: "late" });
await expand();
assert.equal(opened.length, 1, "cancelled expansion opened a late result");
token.isCancellationRequested = false;
request = async () => { throw Object.assign(new Error("worker failed"), { code: -32603 }); };
await expand();
assert.equal(errors.length, 1);
assert.match(errors[0], /worker failed/);
request = async () => null;
await expand();
assert.equal(information.at(-1), "Nothing to expand here");
saves = false;
await commands.get("axiom.run")();
assert.equal(terminalCommands.length, 0, "Run ignored a cancelled save");
saves = true;
await commands.get("axiom.run")();
assert.equal(terminalCommands.length, 1);
assert.equal(terminalCommands[0], "'axiom' run '/tmp/Axiom project/main.ax'");
await commands.get("axiom.run")("/tmp/it's main.ax");
assert.equal(terminalCommands.length, 2);
if (process.platform === "win32") {
  assert.equal(terminalCommands[1], "'axiom' run '/tmp/it''s main.ax'");
} else {
  assert.equal(terminalCommands[1], "'axiom' run '/tmp/it'\\''s main.ax'");
}
await commands.get("axiom.restartServer")();
assert.equal(clients[0].stops, 1);
assert.equal(clients.length, 2);
assert.equal(watchers.length, 2, "restart duplicated workspace watchers");
assert.deepEqual(Array.from(clients[1].options.synchronize.fileEvents), watchers);
await module.exports.deactivate();
assert.equal(clients[1].stops, 1);
for (const item of context.subscriptions) item.dispose();
assert.ok(watchers.every((w) => w.disposed));
console.log("ok   VS Code watchers, trace channel, cancellable expansion, stale-result suppression, save refusal and restart cleanup");
