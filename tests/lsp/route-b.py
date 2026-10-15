#!/usr/bin/env python3
"""Check local scopes, constructor labels and generated source over LSP."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time

compiler = str(Path(sys.argv[1]).resolve())
source = """(struct Point (x : Int) (y : Int))
(:: main Int)
(fn (main)
  {
    (let seed 7)
    (let mut total seed)
    (let point (Point :y total :x seed))
    {
      (let seed total)
      (set total (+ total seed))
    }
    (set total (+ total point.x))
    total
  })

(:: getX (-> Int Int))
(macro definePoint
  ((definePoint name)
    (fn (name input)
      {
        (let point (Point :y 2 :x input))
        point.x
      })))
(definePoint getX)
"""


def offset(text, needle, word=None):
    start = text.index(needle)
    return start + (needle.index(word) if word else 0)


def position(text, index):
    prefix = text[:index]
    return {"line": prefix.count("\n"),
            "character": len(prefix.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2}


def absolute(text, pos):
    lines = text.splitlines(keepends=True)
    return sum(len(line) for line in lines[:pos["line"]]) + pos["character"]


with tempfile.TemporaryDirectory(prefix="axiom-route-b-lsp-") as directory:
    path = Path(directory, "locals.ax")
    path.write_text(source)
    uri = path.as_uri()
    server = subprocess.Popen([compiler, "lsp"], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    request_id = 0

    def send(method, params=None, request=True):
        global request_id
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if request:
            request_id += 1
            message["id"] = request_id
        body = json.dumps(message).encode()
        server.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        server.stdin.flush()
        return request_id

    def read_bytes(count, deadline):
        result = b""
        while len(result) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([server.stdout], [], [], remaining)[0]:
                raise AssertionError("language server timed out")
            chunk = os.read(server.stdout.fileno(), count - len(result))
            if not chunk:
                raise AssertionError("language server closed its output")
            result += chunk
        return result

    def answer(wanted):
        deadline = time.monotonic() + 60
        while True:
            header = b""
            while not header.endswith(b"\r\n\r\n"):
                header += read_bytes(1, deadline)
            length = next(int(line.split(b":", 1)[1]) for line in header.split(b"\r\n")
                          if line.lower().startswith(b"content-length:"))
            message = json.loads(read_bytes(length, deadline))
            if message.get("id") == wanted:
                assert "error" not in message, message
                return message.get("result")

    try:
        initial = answer(send("initialize", {"processId": None,
            "rootUri": Path(directory).as_uri(), "capabilities": {}}))
        legend = initial["capabilities"]["semanticTokensProvider"]["legend"]
        send("initialized", {}, False)
        send("textDocument/didOpen", {"textDocument": {"uri": uri,
            "languageId": "axiom", "version": 1, "text": source}}, False)
        document = {"textDocument": {"uri": uri}}
        tokens = answer(send("textDocument/semanticTokens/full", document))["data"]
        classes = {}
        line = column = 0
        for i in range(0, len(tokens), 5):
            dl, dc, length, kind, bits = tokens[i:i + 5]
            line += dl
            column = column + dc if dl == 0 else dc
            classes[(line, column)] = (legend["tokenTypes"][kind],
                {name for bit, name in enumerate(legend["tokenModifiers"]) if bits & (1 << bit)})

        checks = 0
        for needle, word, kind, readonly in [
            ("(let seed 7)", "seed", "variable", True),
            ("(let mut total seed)", "mut", "keyword", False),
            ("(let mut total seed)", "total", "variable", False),
            (":y total", "y", "property", False),
            (":x seed", "x", "property", False),
        ]:
            at = position(source, offset(source, needle, word))
            actual, modifiers = classes[(at["line"], at["character"])]
            assert actual == kind, (needle, word, actual, kind)
            assert ("readonly" in modifiers) == readonly, (needle, modifiers)
            checks += 1

        for use, expected in [(":x seed", "(let seed 7)"),
                              ("(+ total seed)", "(let seed total)")]:
            at = position(source, offset(source, use, "seed"))
            result = answer(send("textDocument/definition", dict(document, position=at)))
            location = result[0] if isinstance(result, list) else result
            want = position(source, offset(source, expected, "seed"))
            assert location["range"]["start"] == want, (use, location, want)
            checks += 1

        selected = "(+ total point.x)"
        start = source.index(selected)
        selected_range = {"start": position(source, start),
                          "end": position(source, start + len(selected))}
        actions = answer(send("textDocument/codeAction", dict(document,
            range=selected_range, context={"diagnostics": []})))
        extraction = next(item for item in actions if item["title"] == "Extract to `let`")
        edit = extraction["edit"]["changes"][uri][0]
        replacement = edit["newText"]
        assert replacement.startswith("{ (let ") and replacement.endswith(" }"), replacement
        assert "(let ((" not in replacement, replacement
        begin, end = (absolute(source, edit["range"][side]) for side in ("start", "end"))
        path.write_text(source[:begin] + replacement + source[end:])
        checked = subprocess.run([compiler, "check", str(path), "--diagnostic-format=ai"],
                                 capture_output=True, text=True)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        checks += 1

        macro_at = position(source, source.rindex("(definePoint getX)") + 1)
        expanded = answer(send("axiom/expandMacro", dict(document, position=macro_at)))
        preview = expanded["expansion"]
        assert ":x " in preview and ":y " in preview and "(let " in preview, preview
        assert "(let ((" not in preview, preview
        path.write_text("(struct Point (x : Int) (y : Int))\n(:: getX (-> Int Int))\n" + preview)
        checked = subprocess.run([compiler, "check", str(path), "--diagnostic-format=ai"],
                                 capture_output=True, text=True)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        checks += 1
        print(f"{checks} Route B LSP scope, token and generated-source checks passed")
        answer(send("shutdown"))
        send("exit", request=False)
        server.wait(timeout=10)
        assert server.returncode == 0, server.returncode
    finally:
        if server.poll() is None:
            server.kill()
        server.wait()
