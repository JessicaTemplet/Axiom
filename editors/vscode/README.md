# Axiom for VS Code

The extension gives VS Code everything the language server answers,
and colour for `.ax` files from the moment you open one.

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

Changing `axiom.serverPath` restarts the server.

## Commands

All four are in the command palette under *Axiom*.

| Command | What it does |
|---|---|
| Axiom: Run file | Runs `axiom run` on the current file in a terminal. The code lens above `main` runs the same command. |
| Axiom: Expand macro at cursor | Opens the expansion of the macro invocation at the cursor in a new tab. The code lens above an invocation does the same. |
| Axiom: Open the documentation | Runs `axiom doc` in a terminal. |
| Axiom: Restart the language server | Stops and restarts `axiom lsp`. |

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
