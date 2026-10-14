// The Axiom extension: a language client over stdio to `axiom lsp`,
// and the four commands the client owns. Colour comes from
// syntaxes/axiom.tmLanguage.json before the server answers, and from
// the server's semantic tokens once it has.
const vscode = require("vscode");
const { LanguageClient, State } = require("vscode-languageclient/node");

let client;
let output;
let statusBar;
const terminals = new Map();

function serverPath() {
  return vscode.workspace.getConfiguration("axiom").get("serverPath", "axiom");
}

// One quoting rule for every terminal we drive: single quotes work in
// sh, zsh, bash and PowerShell, and a quote inside is written doubled.
function shellQuote(value) {
  return "'" + String(value).replace(/'/g, "''") + "'";
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
  if (doc && doc.isDirty) await doc.save();
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
    r = await client.sendRequest("axiom/expandMacro", { textDocument: { uri }, position });
  } catch (e) {
    vscode.window.showErrorMessage("Axiom: could not expand the macro: " + (e && e.message ? e.message : e));
    return;
  }
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

async function restartServer() {
  await stopClient();
  await startClient();
  vscode.window.setStatusBarMessage("Axiom: language server restarted", 3000);
}

function activate(context) {
  output = vscode.window.createOutputChannel("Axiom");
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBar.command = "axiom.restartServer";
  setStatus(State.Starting);
  context.subscriptions.push(
    output,
    statusBar,
    vscode.commands.registerCommand("axiom.run", runFile),
    vscode.commands.registerCommand("axiom.expandMacro", expandMacro),
    vscode.commands.registerCommand("axiom.doc", () => runInTerminal("axiom doc", shellQuote(serverPath()) + " doc")),
    vscode.commands.registerCommand("axiom.restartServer", restartServer),
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
