// The Axiom extension: a language client over stdio to `axiom lsp`,
// and the four commands the client owns. Colour comes from
// syntaxes/axiom.tmLanguage.json before the server answers, and from
// the server's semantic tokens once it has.
const vscode = require("vscode");
const { LanguageClient } = require("vscode-languageclient/node");

let client;
let output;

function serverPath() {
  return vscode.workspace.getConfiguration("axiom").get("serverPath", "axiom");
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
  return client.start();
}

async function stopClient() {
  if (!client) return;
  const c = client;
  client = undefined;
  await c.stop();
}

// A terminal running an axiom command, shown so its output is visible.
function runInTerminal(name, command) {
  const t = vscode.window.createTerminal(name);
  t.show();
  t.sendText(command);
}

// Code lenses pass the file path; the palette passes nothing, so the
// active editor's file is the subject then.
function runFile(path) {
  const file = path || (vscode.window.activeTextEditor && vscode.window.activeTextEditor.document.fileName);
  if (!file) {
    vscode.window.showInformationMessage("Open an Axiom file to run it");
    return;
  }
  runInTerminal("axiom run", serverPath() + " run " + JSON.stringify(file));
}

// `axiom/expandMacro` answers the macro's name and its expansion, or
// null when the position is not inside an invocation.
async function expandMacro(uri, position) {
  const editor = vscode.window.activeTextEditor;
  if (!uri && editor) {
    uri = editor.document.uri.toString();
    position = editor.selection.active;
  }
  if (!client || !uri) return;
  const r = await client.sendRequest("axiom/expandMacro", { textDocument: { uri }, position });
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
  context.subscriptions.push(
    output,
    vscode.commands.registerCommand("axiom.run", runFile),
    vscode.commands.registerCommand("axiom.expandMacro", expandMacro),
    vscode.commands.registerCommand("axiom.doc", () => runInTerminal("axiom doc", serverPath() + " doc")),
    vscode.commands.registerCommand("axiom.restartServer", restartServer),
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
