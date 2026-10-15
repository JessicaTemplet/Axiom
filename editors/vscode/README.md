# Axiom for VS Code

![The Axiom mark: a lime split A on graphite](icon.png)

The extension gives VS Code everything the language server answers,
and colour for `.ax` files from the moment you open one. The Axiom
mark is the extension's icon, and the same tile marks every `.ax`
file in the explorer and in tabs.

## Install from source

You need the `axiom` binary on your `PATH` (see the repository's
[README](../../README.md)) and Node.

```bash
cd editors/vscode
npm install
npx @vscode/vsce package
code --install-extension axiom-language-*.vsix
```

The package is named after the version in `package.json`, which is the
repository's `VERSION`.

To try it without packaging, open `editors/vscode` in VS Code and press
F5. That starts an Extension Development Host with the extension
loaded. Open a `.ax` file there and hover a function name: a tooltip
quoting its `(:: f T)` signature means the server connected. If it
didn't, the *Output* panel's *Axiom* channel shows the server's stderr.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `axiom.serverPath` | `axiom` | The executable the extension runs as `axiom lsp`. Give an absolute path when `axiom` isn't on VS Code's `PATH`. |
| `axiom.trace.server` | `off` | Log the messages between VS Code and the server in the *Axiom* channel: `messages` or `verbose`. |

Changing `axiom.serverPath` restarts the server. Trace output includes
transport messages and the server's cache and worker events.

## Commands

All four are in the command palette under *Axiom*. Run and Expand
macro are also in the editor title bar and the editor menu on an
Axiom file, Run is in the explorer menu on a `.ax` file, and the
status bar shows whether the server is running. Select it to restart
the server.

| Command | What it does |
|---|---|
| Axiom: Run file | Runs `axiom run` on the current file in a terminal, saving it first. The code lens above `main` runs the same command. |
| Axiom: Expand macro at cursor | Opens the expansion at the cursor in a new tab. The progress notification lets you cancel a long expansion. |
| Axiom: Open the documentation | Runs `axiom doc` in a terminal. |
| Axiom: Restart the language server | Stops and restarts `axiom lsp`. |

Snippets cover the shapes you write most: type `fn`, `main`, `let`,
`match`, `data` or `import` in a `.ax` file and press Tab.

## Format a document

Use VS Code's **Format Document** command. It formats the unsaved document
through the language server, using the same formatter as `axiom fmt`.
To format Axiom files when you save, add this to your settings:

```json
{
  "[axiom]": {
    "editor.defaultFormatter": "axiom-lang.axiom-language",
    "editor.formatOnSave": true
  }
}
```

## Work with live dependencies

Unsaved imported modules feed diagnostics, hover and navigation. The client
sends ranged UTF-16 edits and save notifications. It also watches `.ax`
files and `axiom.pkg` within your workspace, so external edits, created
modules and dependency search changes refresh open documents.

With a server that supports background analysis, edits cancel obsolete
work and only the current revision publishes results. Macro expansion has
a cancel button; cancelled or outdated expansions leave the editor unchanged.
Restarting the server starts a fresh session.

The client negotiates full, range and delta semantic tokens with the server.
VS Code uses those results to refine highlighting as you edit.
[The LSP guide](../../docs/lsp.md) describes the supported requests and limits.

## Where each feature comes from

The language server supplies diagnostics, hover, completion, go to
definition, references, rename, the outline, inlay hints, code lenses,
code actions, formatting and semantic colours. [docs/lsp.md](../../docs/lsp.md)
describes each one.

The TextMate grammar in `syntaxes/` colours a file before the server
answers, and in a file that doesn't parse yet. It colours by role, the
way the tree-sitter queries do. Keywords are coloured in head position,
a declared name by what it declares, a capitalised head as a
constructor and any other head as a call. Builtin types and effects,
AXTAGs, `;;` doc comments and the `{holes}` in a string each get a
colour of their own.
When the server's semantic tokens arrive they refine the grammar's
answer, so a `let` binding or a parameter named like a function keeps
the colour it should have.

Editors with tree-sitter colour each token the same way: the queries in
`tree-sitter-axiom/` give it the role the server and this grammar give
it. Tested by `scripts/check-highlight-parity.sh`.

The grammar's keyword list is written from the compiler's parser by
`scripts/sync-keywords.mjs`. Run it after the parser learns a keyword.

Tested by `scripts/check-vscode.sh`.

## Not yet

Not yet: published to the marketplace. Install from source as above.
See the [roadmap](../../docs/roadmap.md).
