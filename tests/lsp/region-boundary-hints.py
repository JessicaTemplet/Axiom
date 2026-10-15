#!/usr/bin/env python3
"""Require the compiler's memory-boundary remedies and spans in LSP diagnostics."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CODES = {"AX3059", "AX3060", "AX3062"}
REMEDIES = {
    "AX3059": "outside the region",
    "AX3060": "reclamation boundary",
    "AX3062": "captures' region",
}


def frame(message):
    body = json.dumps({"jsonrpc": "2.0", **message}).encode()
    return b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


def messages(data):
    while data:
        header, data = data.split(b"\r\n\r\n", 1)
        length = int(header.split(b":", 1)[1].strip())
        yield json.loads(data[:length])
        data = data[length:]


def lsp_range(span, lines):
    def position(point):
        line, col = point["line"] - 1, point["col"] - 1
        return {"line": line, "character": len(lines[line][:col].encode("utf-16-le")) // 2}
    return {"start": position(span["start"]), "end": position(span["end"])}


def check(compiler, name):
    path = ROOT / "tests" / "diagnostics" / (name + ".ax")
    text, uri = path.read_text(), path.as_uri()
    result = subprocess.run([compiler, "--diagnostic-format=json", "check", path.name],
                            cwd=path.parent, env=os.environ, capture_output=True, text=True, timeout=30)
    expected = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
    expected = [item for item in expected if item["code"] in CODES]
    assert result.returncode == 1 and expected, (name, result.stderr)
    wire = b"".join([
        frame({"id": 1, "method": "initialize", "params": {"processId": None, "rootUri": None, "capabilities": {}}}),
        frame({"method": "initialized", "params": {}}),
        frame({"method": "textDocument/didOpen", "params": {"textDocument": {
            "uri": uri, "languageId": "axiom", "version": 1, "text": text}}}),
        frame({"id": 9, "method": "shutdown", "params": {}}),
        frame({"method": "exit", "params": {}}),
    ])
    # Shutdown cancels background work. Wait for didOpen's publication
    # before sending it, using the shared protocol driver.
    session = runpy.run_path(str(Path(__file__).with_name("workspace-overlays.py")))["run_session"]
    server = session(compiler, wire, cwd=path.parent, timeout=30)
    assert server.returncode == 0, (name, server.stderr)
    actual = [d for m in messages(server.stdout) if m.get("method") == "textDocument/publishDiagnostics"
              for d in m["params"]["diagnostics"] if d.get("code") in CODES]
    assert len(actual) == len(expected), (name, expected, actual)
    lines = text.splitlines()
    for item in expected:
        matches = [d for d in actual if d["code"] == item["code"]
                   and d["message"].startswith(item["message"])
                   and d["range"] == lsp_range(item["span"], lines)]
        assert len(matches) == 1, (name, item, actual)
        published = matches[0]
        assert item["help"] and REMEDIES[item["code"]] in " ".join(item["help"]), item
        for help_text in item["help"]:
            assert "help: " + help_text in published["message"], (name, published)
        assert item["related"], (name, "missing reclamation boundary", item)
        for related in item["related"]:
            assert {"location": {"uri": uri, "range": lsp_range(related["span"], lines)},
                    "message": related["label"]} in published.get("relatedInformation", []), (name, published)
    return len(expected)


if __name__ == "__main__":
    total = sum(check(sys.argv[1], name) for name in ["631-region-escape", "645-region-escape-store",
                                                     "646-region-escape-return", "647-region-capture"])
    assert total >= 8, total
    print(f"region boundary hints: {total} diagnostics preserve remedies and related spans through LSP")
