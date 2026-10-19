// The Axiom extension: a language client over stdio to `axiom lsp`,
// and the four commands the client owns. Colour comes from
// syntaxes/axiom.tmLanguage.json before the server answers, and from
// the server's semantic tokens once it has.
const vscode = require("vscode");
const { LanguageClient, State, LSPErrorCodes } = require("vscode-languageclient/node");
const axqlite = require("./axqlite");

let client;
let output;
let statusBar;
let fileEvents = [];
const terminals = new Map();

function serverPath() {
  return vscode.workspace.getConfiguration("axiom").get("serverPath", "axiom");
}

// One quoting rule for every terminal we drive. PowerShell doubles a
// quote inside single quotes; a POSIX shell closes, escapes and
// reopens instead - doubling there silently drops the quote and
// mangles a path that contains one.
function shellQuote(value) {
  const s = String(value);
  const platform = typeof process !== "undefined" ? process.platform : "";
  return platform === "win32"
    ? "'" + s.replace(/'/g, "''") + "'"
    : "'" + s.replace(/'/g, "'\\''") + "'";
}

function setStatus(state) {
  if (!statusBar) return;
  if (state === State.Running) {
    statusBar.text = "$(pass-filled) Axiom";
    statusBar.tooltip = "Axiom language server is running. Select to restart it.";
  } else if (state === State.Starting) {
    statusBar.text = "$(loading~spin) Axiom";
    statusBar.tooltip = "Axiom language server is starting.";
  } else {
    statusBar.text = "$(circle-slash) Axiom";
    statusBar.tooltip = "Axiom language server is stopped. Select to restart it.";
  }
  statusBar.show();
}

function startClient() {
  client = new LanguageClient(
    "axiom",
    "Axiom",
    { command: serverPath(), args: ["lsp"] },
    {
      documentSelector: [{ scheme: "file", language: "axiom" }],
      outputChannel: output,
      traceOutputChannel: output,
      synchronize: { fileEvents },
    }
  );
  client.onDidChangeState((e) => setStatus(e.newState));
  setStatus(State.Starting);
  return client.start();
}

async function stopClient() {
  if (!client) return;
  const c = client;
  client = undefined;
  await c.stop();
  setStatus(State.Stopped);
}

// A terminal running an axiom command, shown so its output is visible.
// One terminal per name: running again reuses it instead of stacking.
function runInTerminal(name, command) {
  let t = terminals.get(name);
  if (!t) {
    t = vscode.window.createTerminal(name);
    terminals.set(name, t);
  }
  t.show();
  t.sendText(command);
}

// Code lenses pass the file path, the explorer passes a Uri and the
// palette passes nothing, so the active editor's file is the subject
// then. An unsaved file is saved first, so what runs is what you see.
async function runFile(arg) {
  let file = typeof arg === "string" ? arg : arg && arg.fsPath;
  const editor = vscode.window.activeTextEditor;
  if (!file && editor) file = editor.document.fileName;
  if (!file) {
    vscode.window.showInformationMessage("Open an Axiom file to run it");
    return;
  }
  const doc = vscode.workspace.textDocuments.find((d) => d.fileName === file);
  if (doc && doc.isDirty && !await doc.save()) return;
  runInTerminal("axiom run", shellQuote(serverPath()) + " run " + shellQuote(file));
}

// `axiom/expandMacro` answers the macro's name and its expansion, or
// null when the position is not inside an invocation.
async function expandMacro(uri, position) {
  const editor = vscode.window.activeTextEditor;
  if (!uri && editor) {
    uri = editor.document.uri.toString();
    position = editor.selection.active;
  }
  if (!client) {
    vscode.window.showInformationMessage("The Axiom language server is not running");
    return;
  }
  if (!uri) return;
  let r;
  try {
    const c = client;
    r = await vscode.window.withProgress({
      location: vscode.ProgressLocation.Notification,
      title: "Axiom: Expanding macro",
      cancellable: true,
    }, async (_progress, token) => {
      const result = await c.sendRequest("axiom/expandMacro", { textDocument: { uri }, position }, token);
      return token.isCancellationRequested ? undefined : result;
    });
  } catch (e) {
    if (e instanceof vscode.CancellationError ||
        e?.code === LSPErrorCodes.RequestCancelled ||
        e?.code === LSPErrorCodes.ContentModified ||
        e?.code === LSPErrorCodes.ServerCancelled) return;
    vscode.window.showErrorMessage("Axiom: could not expand the macro: " + (e && e.message ? e.message : e));
    return;
  }
  if (r === undefined) return;
  if (!r) {
    vscode.window.showInformationMessage("Nothing to expand here");
    return;
  }
  const doc = await vscode.workspace.openTextDocument({
    language: "axiom",
    content: "; " + r.name + "\n" + r.expansion + "\n",
  });
  await vscode.window.showTextDocument(doc, { preview: true });
}

// -- AXQLite browsing -------------------------------------------------
// Databases are read directly, in this process, with the reader in
// axqlite.js. A `.db`/`.axqlite` file that is not AXQLite answers with
// the reader's refusal, which is shown rather than swallowed.
const DB_EXTS = [".db", ".axqlite"];

function isDbFile(name) {
  return DB_EXTS.some((e) => name.toLowerCase().endsWith(e));
}

function dbSummaryText(file) {
  const { header, tables } = axqlite.readDatabase(file);
  const lines = [
    `AXQLite database: ${file}`,
    `format ${header.version}, ${header.pageCount} page(s), schema version ${header.schemaVersion}, change counter ${header.changeCounter}`,
    "",
  ];
  for (const t of tables) {
    lines.push(`${t.kind} ${t.name} (root page ${t.rootPage})`);
    if (t.sql) lines.push(`  ${t.sql}`);
    if (t.columns.length) lines.push(`  columns: ${t.columns.join(", ")}`);
    if (t.kind === "table") {
      const rows = axqlite.readTableRows(file, t.rootPage, 20);
      for (const r of rows) {
        const cols = t.columns.length ? t.columns : r.values.map((_, i) => `col${i}`);
        lines.push(`  #${r.rowid}: ` + r.values.map((v, i) => `${cols[i]}=${axqlite.renderValue(v)}`).join(", "));
      }
      if (rows.length === 20) lines.push("  ... (first 20 rows)");
    }
    lines.push("");
  }
  if (!tables.length) lines.push("(no tables)");
  return lines.join("\n");
}

class DbTreeProvider {
  constructor() {
    this._changed = new vscode.EventEmitter();
    this.onDidChangeTreeData = this._changed.event;
  }
  refresh() { this._changed.fire(); }
  getTreeItem(node) { return node; }
  async getChildren(node) {
    try {
      if (!node) {
        const uris = [
          ...(await vscode.workspace.findFiles("**/*.db")),
          ...(await vscode.workspace.findFiles("**/*.axqlite")),
          ...(await vscode.workspace.findFiles("**/*.axql")),
        ].sort((a, b) => a.fsPath.localeCompare(b.fsPath));
        return uris.map((uri) => {
          const item = new vscode.TreeItem(uri, vscode.TreeItemCollapsibleState.Collapsed);
          item.resourceUri = uri;
          item.contextValue = uri.fsPath.endsWith(".axql") ? "axqlFile" : "axiomDatabase";
          item.iconPath = new vscode.ThemeIcon(uri.fsPath.endsWith(".axql") ? "file-code" : "database");
          item.dbFile = uri.fsPath;
          return item;
        });
      }
      if (node.contextValue === "axqlFile") return [];
      if (node.contextValue === "axiomDatabase") {
        const { tables } = axqlite.readDatabase(node.dbFile);
        return tables.map((t) => {
          const item = new vscode.TreeItem(t.name, vscode.TreeItemCollapsibleState.Collapsed);
          item.description = t.columns.join(", ");
          item.contextValue = t.kind === "index" ? "axiomIndex" : "axiomTable";
          item.iconPath = new vscode.ThemeIcon(t.kind === "index" ? "list-ordered" : "table");
          item.dbFile = node.dbFile;
          item.table = t;
          return item;
        });
      }
      if (node.contextValue === "axiomTable") {
        const rows = axqlite.readTableRows(node.dbFile, node.table.rootPage, 100);
        return rows.map((r) => {
          const cols = node.table.columns.length ? node.table.columns : r.values.map((_, i) => `col${i}`);
          const label = `#${r.rowid}: ` + r.values.map((v, i) => `${cols[i]}=${axqlite.renderValue(v)}`).join(", ");
          const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.None);
          item.contextValue = "axiomRow";
          item.iconPath = new vscode.ThemeIcon("symbol-field");
          return item;
        });
      }
    } catch (e) {
      vscode.window.showErrorMessage(`Axiom: could not read database: ${e.message}`);
    }
    return [];
  }
}

const dbProvider = new DbTreeProvider();

async function browseDatabase(arg) {
  const file = arg && arg.fsPath ? arg.fsPath : arg && arg.dbFile ? arg.dbFile
    : vscode.window.activeTextEditor ? vscode.window.activeTextEditor.document.fileName : undefined;
  if (!file || (!isDbFile(file) && !file.endsWith(".axql"))) {
    vscode.window.showInformationMessage("Open an AXQLite database (.db, .axqlite) or an .axql file to browse it");
    return;
  }
  if (file.endsWith(".axql")) {
    await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(file));
    return;
  }
  dbProvider.refresh();
  await openDatabaseSummary({ dbFile: file });
}

async function openDatabaseSummary(node) {
  const file = node && node.dbFile ? node.dbFile
    : vscode.window.activeTextEditor ? vscode.window.activeTextEditor.document.fileName : undefined;
  if (!file) return;
  try {
    const doc = await vscode.workspace.openTextDocument({
      language: "axql", content: dbSummaryText(file),
    });
    await vscode.window.showTextDocument(doc, { preview: true });
  } catch (e) {
    vscode.window.showErrorMessage(`Axiom: could not read database: ${e.message}`);
  }
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

class AxqliteEditorProvider {
  async resolveCustomEditor(document, panel) {
    panel.webview.options = {};
    const file = document.uri.fsPath;
    let body;
    try {
      const { header, tables } = axqlite.readDatabase(file);
      const cards = tables.map((t) => {
        const cols = t.columns.length ? t.columns : [];
        let rowsHtml = "";
        if (t.kind === "table") {
          const rows = axqlite.readTableRows(file, t.rootPage, 20);
          const names = cols.length ? cols : (rows[0] ? rows[0].values.map((_, i) => `col${i}`) : []);
          const head = names.map((c) => `<th>${escapeHtml(c)}</th>`).join("");
          const lines = rows.map((r) =>
            `<tr><td class="rowid">${r.rowid ?? ""}</td>${r.values.map((v) => `<td>${escapeHtml(axqlite.renderValue(v))}</td>`).join("")}</tr>`).join("");
          rowsHtml = rows.length
            ? `<table><thead><tr><th class="rowid">#</th>${head}</tr></thead><tbody>${lines}</tbody></table>`
            : `<p class="muted">No rows yet.</p>`;
        }
        return `<section class="card"><h2>${escapeHtml(t.name)} <span class="kind">${escapeHtml(t.kind)}</span></h2>`
          + (t.sql ? `<p class="sql">${escapeHtml(t.sql)}</p>` : "")
          + rowsHtml + `</section>`;
      }).join("");
      body = `<header><h1>${escapeHtml(file.split("/").pop())}</h1>`
        + `<p class="muted">AXQLite format ${header.version} · ${header.pageCount} page(s) · schema ${header.schemaVersion} · change ${header.changeCounter}</p></header>`
        + (cards || `<p class="muted">No tables.</p>`);
    } catch (e) {
      body = `<p class="error">Not an AXQLite database: ${escapeHtml(e.message)}</p>`;
    }
    panel.webview.html = `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
      body { background: var(--vscode-editor-background); color: var(--vscode-editor-foreground);
        font-family: var(--vscode-font-family); padding: 20px 24px; }
      h1 { font-size: 1.3em; margin: 0 0 4px; } h2 { font-size: 1.05em; margin: 0 0 6px; }
      .muted { color: var(--vscode-descriptionForeground); }
      .error { color: var(--vscode-errorForeground); }
      .card { border: 1px solid var(--vscode-panel-border); border-radius: 6px;
        padding: 12px 16px; margin: 14px 0; background: var(--vscode-editorWidget-background); }
      .kind { color: var(--vscode-descriptionForeground); font-weight: normal; font-size: .85em;
        text-transform: uppercase; letter-spacing: .04em; }
      .sql { font-family: var(--vscode-editor-font-family); font-size: .9em;
        color: var(--vscode-descriptionForeground); white-space: pre-wrap; }
      table { border-collapse: collapse; width: 100%; font-family: var(--vscode-editor-font-family); font-size: .9em; }
      th, td { border-bottom: 1px solid var(--vscode-panel-border); padding: 4px 8px; text-align: left; }
      th { color: var(--vscode-descriptionForeground); font-weight: 600; }
      td.rowid, th.rowid { color: var(--vscode-descriptionForeground); width: 3em; }
      </style></head><body>${body}</body></html>`;
  }
}

async function restartServer() {
  await stopClient();
  await startClient();
  vscode.window.setStatusBarMessage("Axiom: language server restarted", 3000);
}

function activate(context) {
  // The language client negotiates ranged edits, saves and semantic-token
  // deltas. Workspace watchers cover dependencies changed outside an editor.
  fileEvents = [
    vscode.workspace.createFileSystemWatcher("**/*.ax"),
    vscode.workspace.createFileSystemWatcher("**/axiom.pkg"),
  ];
  output = vscode.window.createOutputChannel("Axiom");
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBar.command = "axiom.restartServer";
  setStatus(State.Starting);
  context.subscriptions.push(
    ...fileEvents,
    output,
    statusBar,
    vscode.commands.registerCommand("axiom.run", runFile),
    vscode.commands.registerCommand("axiom.expandMacro", expandMacro),
    vscode.commands.registerCommand("axiom.doc", () => runInTerminal("axiom doc", shellQuote(serverPath()) + " doc")),
    vscode.commands.registerCommand("axiom.restartServer", restartServer),
    vscode.commands.registerCommand("axiom.browseDatabase", browseDatabase),
    vscode.commands.registerCommand("axiom.refreshDatabases", () => dbProvider.refresh()),
    vscode.commands.registerCommand("axiom.openDatabaseSummary", openDatabaseSummary),
    vscode.window.registerTreeDataProvider("axiomDatabases", dbProvider),
    vscode.window.registerCustomEditorProvider("axiom.axqliteViewer", new AxqliteEditorProvider(), { supportsMultipleEditorsPerDocument: false }),
    vscode.window.onDidCloseTerminal((t) => {
      for (const [name, term] of terminals) if (term === t) terminals.delete(name);
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("axiom.serverPath")) restartServer();
    })
  );
  return startClient();
}

function deactivate() {
  return stopClient();
}

module.exports = { activate, deactivate };
