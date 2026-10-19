// The database preview stays independent of the language server.
const { randomBytes } = require("node:crypto");
const axqlite = require("./axqlite");

const escape = (value) => String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;")
  .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const previewLimit = 100;

function renderDatabase(file) {
  const nonce = randomBytes(16).toString("hex");
  let content;
  try {
    const { header, tables } = axqlite.readDatabase(file);
    const navigation = tables.map((table, index) => `<a href="#object-${index}"><span>${escape(table.name)}</span><small>${escape(table.kind)}</small></a>`).join("");
    const cards = tables.map((table, index) => {
      const rows = table.kind === "table" ? axqlite.readTableRows(file, table.rootPage, previewLimit) : [];
      const names = table.columns.length ? table.columns : (rows[0]?.values.map((_, i) => `column${i + 1}`) || []);
      const grid = rows.length ? `<div class="grid"><table><thead><tr><th scope="col" class="rowid">Row ID</th>${names.map((name) => `<th scope="col">${escape(name)}</th>`).join("")}</tr></thead>
        <tbody>${rows.map((row) => `<tr><td class="rowid">${escape(row.rowid ?? "")}</td>${row.values.map((value) => `<td>${escape(axqlite.renderValue(value))}</td>`).join("")}</tr>`).join("")}</tbody></table></div><p class="no-results muted" hidden>No matching rows in this preview.</p>`
        : (table.kind === "table" ? `<p class="empty muted">This table has no rows.</p>` : "");
      return `<section class="card" id="object-${index}"><div class="card-heading"><h2>${escape(table.name)}</h2><span class="badge">${escape(table.kind)}</span>${table.kind === "table" ? `<span class="row-count muted">${rows.length} rows shown</span>` : ""}</div>
        ${grid}${table.sql ? `<details><summary>Schema</summary><pre><code>${escape(table.sql)}</code></pre></details>` : ""}</section>`;
    }).join("");
    content = `<div class="metrics"><span><strong>${tables.filter((table) => table.kind === "table").length}</strong> tables</span><span><strong>${header.pageCount}</strong> pages</span><span>Format <strong>${header.version}</strong></span><span>Schema <strong>${header.schemaVersion}</strong></span></div>
      ${tables.length ? `<div class="workspace"><aside aria-label="Database objects"><h2>Objects <span class="muted">${tables.length}</span></h2><nav>${navigation}</nav></aside><main>
      <div class="toolbar"><input id="filter" type="search" placeholder="Filter preview rows…" aria-label="Filter preview rows"><p id="preview-status" class="muted" role="status">Up to ${previewLimit} rows per table</p></div>${cards}</main></div>`
      : `<div class="empty-state"><h2>No tables yet</h2><p class="muted">Create a table in your Axiom program, then refresh this preview.</p></div>`}`;
  } catch (error) {
    content = `<div class="empty-state"><h2>Could not open this database</h2><p class="error">${escape(error.message)}</p><p class="muted">Choose an AXQLite database file, or refresh after the file is available.</p></div>`;
  }
  return `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
    <style nonce="${nonce}">
    * { box-sizing: border-box; } [hidden] { display: none !important; }
    body { margin: 0; padding: 24px; color: var(--vscode-editor-foreground, #d5d8d2); background: var(--vscode-editor-background, #111512); font: 13px var(--vscode-font-family, system-ui); }
    h1, h2, p { margin: 0; } h1 { font-size: 22px; font-weight: 600; overflow-wrap: anywhere; } h2 { font-size: 14px; font-weight: 600; }
    .topbar, .actions, .card-heading, .toolbar { display: flex; align-items: center; gap: 12px; } .topbar { justify-content: space-between; flex-wrap: wrap; margin-bottom: 22px; }
    .eyebrow { color: var(--vscode-descriptionForeground, #9aa596); font-size: 12px; margin-bottom: 6px; } .muted, .rowid { color: var(--vscode-descriptionForeground, #9aa596); }
    .badge { border: 1px solid var(--vscode-panel-border, #364033); border-radius: 12px; padding: 3px 9px; white-space: nowrap; font-size: 11px; font-weight: 500; }
    button { color: var(--vscode-button-foreground, #14200c); background: var(--vscode-button-background, #b4df7e); border: 1px solid transparent; border-radius: 4px; padding: 6px 12px; font: inherit; cursor: pointer; }
    button:hover { background: var(--vscode-button-hoverBackground, #c4ee90); } button:disabled { opacity: .5; cursor: default; }
    button:focus-visible, input:focus, a:focus-visible, summary:focus-visible { outline: 1px solid var(--vscode-focusBorder, #b4df7e); outline-offset: 2px; }
    .metrics { display: flex; flex-wrap: wrap; gap: 10px 24px; padding: 12px 0 20px; margin-bottom: 20px; border-bottom: 1px solid var(--vscode-panel-border, #364033); color: var(--vscode-descriptionForeground, #9aa596); } .metrics strong { color: var(--vscode-editor-foreground, #d5d8d2); font-weight: 500; }
    .workspace { display: grid; grid-template-columns: 180px minmax(0, 1fr); gap: 24px; } aside h2 { margin: 7px 8px 12px; } aside h2 span { margin-left: 6px; font-weight: 400; }
    nav { display: grid; gap: 4px; } nav a { display: flex; justify-content: space-between; align-items: center; gap: 8px; border-radius: 4px; padding: 8px; text-decoration: none; color: inherit; } nav a span { overflow-wrap: anywhere; } nav a:hover { background: var(--vscode-list-hoverBackground, #20281d); } nav small { color: var(--vscode-descriptionForeground, #9aa596); }
    .toolbar { justify-content: space-between; flex-wrap: wrap; margin-bottom: 16px; } input { min-width: 180px; flex: 1; max-width: 360px; border: 1px solid var(--vscode-input-border, #364033); border-radius: 4px; padding: 7px 10px; font: inherit; background: var(--vscode-input-background, #20281d); color: var(--vscode-input-foreground, #d5d8d2); } input::placeholder { color: var(--vscode-input-placeholderForeground, #9aa596); }
    .card { scroll-margin-top: 16px; border: 1px solid var(--vscode-panel-border, #364033); border-radius: 6px; margin-bottom: 16px; overflow: hidden; } .card-heading { padding: 13px 16px; background: var(--vscode-editorWidget-background, #192016); flex-wrap: wrap; } .row-count { margin-left: auto; font-size: 12px; }
    .grid { overflow: auto; max-height: 380px; } table { border-collapse: separate; border-spacing: 0; width: 100%; font: 12px var(--vscode-editor-font-family, monospace); } th, td { border-bottom: 1px solid var(--vscode-panel-border, #364033); padding: 9px 12px; text-align: left; white-space: pre; } th { position: sticky; top: 0; background: var(--vscode-editorWidget-background, #192016); font-weight: 500; } tbody tr:last-child td { border-bottom: 0; } tbody tr:hover { background: var(--vscode-list-hoverBackground, #20281d); } .rowid { width: 65px; }
    details { border-top: 1px solid var(--vscode-panel-border, #364033); padding: 10px 16px; } summary { cursor: pointer; color: var(--vscode-descriptionForeground, #9aa596); font-size: 12px; } pre { overflow: auto; margin-bottom: 0; line-height: 1.6; } .empty, .no-results { padding: 20px 16px; } .empty-state { border: 1px dashed var(--vscode-panel-border, #364033); border-radius: 6px; padding: 32px; } .empty-state p { margin-top: 10px; line-height: 1.6; } .error { color: var(--vscode-errorForeground, #f59898); }
    @media (max-width: 700px) { body { padding: 16px; } .workspace { grid-template-columns: minmax(0, 1fr); gap: 16px; } nav { display: flex; flex-wrap: wrap; } aside h2 { margin-left: 0; } .actions { margin-left: auto; } }
    </style></head><body><header class="topbar"><div><p class="eyebrow">Axiom · AXQLite browser</p><h1>${escape(file.split(/[\\/]/).pop())}</h1></div><div class="actions"><span class="badge">Read only</span><button id="refresh" type="button">Refresh</button></div></header>${content}
    <script nonce="${nonce}">
    const host = typeof acquireVsCodeApi === 'function' ? acquireVsCodeApi() : undefined;
    const refresh = document.getElementById('refresh');
    refresh.disabled = !host;
    refresh.addEventListener('click', () => host?.postMessage({ type: 'refresh' }));
    document.getElementById('filter')?.addEventListener('input', (event) => {
      const query = event.target.value.toLocaleLowerCase();
      let visible = 0, total = 0;
      for (const card of document.querySelectorAll('.card')) {
        const rows = [...card.querySelectorAll('tbody tr')];
        if (!rows.length) continue;
        for (const row of rows) row.hidden = !row.textContent.toLocaleLowerCase().includes(query);
        const count = rows.filter((row) => !row.hidden).length;
        card.querySelector('.row-count').textContent = count + ' of ' + rows.length + ' rows shown';
        card.querySelector('.no-results').hidden = count > 0;
        visible += count; total += rows.length;
      }
      document.getElementById('preview-status').textContent = query ? visible + ' of ' + total + ' preview rows match' : 'Up to ${previewLimit} rows per table';
    });
    </script></body></html>`;
}

module.exports = { renderDatabase };
