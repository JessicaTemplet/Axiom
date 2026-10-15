#!/usr/bin/env python3
"""Check range encoding and reconstruct token deltas against known tokens."""
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import tempfile
import time


class Server:
    def __init__(self, compiler, directory):
        self.process = subprocess.Popen([compiler, "lsp"], cwd=directory,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
        self.pending = b""
        self.serial = 0
        self.publications = []

    def send(self, method, params, identifier=None):
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if identifier is not None:
            message["id"] = identifier
        body = json.dumps(message, ensure_ascii=False).encode()
        self.process.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.process.stdin.flush()

    def read(self):
        deadline = time.monotonic() + 10
        while True:
            header, separator, body = self.pending.partition(b"\r\n\r\n")
            if separator:
                length = int(header.removeprefix(b"Content-Length: "))
                if len(body) >= length:
                    self.pending = body[length:]
                    return json.loads(body[:length])
            remaining = deadline - time.monotonic()
            assert remaining > 0, "server response timed out"
            ready, _, _ = select.select([self.process.stdout], [], [], remaining)
            assert ready, "server response timed out"
            chunk = os.read(self.process.stdout.fileno(), 65536)
            assert chunk, "server closed its output"
            self.pending += chunk

    def request(self, method, params):
        self.serial += 1
        identifier = 'token "request" λ-%d' % self.serial
        self.send(method, params, identifier)
        while True:
            response = self.read()
            if "id" in response:
                assert response["id"] == identifier, response
                assert "error" not in response, response
                return response["result"]
            assert response.get("method") == "textDocument/publishDiagnostics", response
            self.publications.append(response["params"])

    def close(self):
        assert self.request("shutdown", None) is None
        self.send("exit", None)
        self.process.stdin.close()
        assert self.process.wait(timeout=10) == 0
        assert self.process.stderr.read() == b""
        assert self.pending == b""


def position(source, offset):
    endings = list(re.finditer(r"\r\n|\r|\n", source[:offset]))
    start = endings[-1].end() if endings else 0
    return {"line": len(endings),
            "character": len(source[start:offset].encode("utf-16-le")) // 2}


def encode(tokens):
    data = []
    previous_line = previous_column = 0
    for line, column, length, kind, modifiers in tokens:
        data.extend([line - previous_line,
                     column - previous_column if line == previous_line else column,
                     length, kind, modifiers])
        previous_line, previous_column = line, column
    return data


def decode(data):
    assert len(data) % 5 == 0, data
    line = column = 0
    tokens = []
    for index in range(0, len(data), 5):
        delta_line, delta_column, length, kind, modifiers = data[index:index + 5]
        assert min(delta_line, delta_column, length, kind, modifiers) >= 0
        line += delta_line
        column = column + delta_column if delta_line == 0 else delta_column
        tokens.append((line, column, length, kind, modifiers))
    return tokens


def apply_delta(data, response):
    assert "edits" in response and "data" not in response, response
    result = data[:]
    edits = sorted(response["edits"], key=lambda edit: edit["start"], reverse=True)
    last = len(data)
    for edit in edits:
        start, count = edit["start"], edit["deleteCount"]
        assert 0 <= start <= start + count <= last, edit
        result[start:start + count] = edit.get("data", [])
        last = start
    return result


def structured_tokens(compiler):
    """Bucket lookups keep their owners across semantic requests and edits."""
    source = """(struct Point (x : UInt8) (y : UInt8))
(type Count = UInt8)
(:: main Int)
(fn (main)
  {
    (let point (Point :y (as 2 UInt8) :x (as 1 UInt8)))
    (let mut count (as point.x Count))
    (set count (+ count (as 1 Count)))
    (as count Int)
  })
"""
    with tempfile.TemporaryDirectory(prefix="axiom-lsp-buckets-") as directory:
        server = Server(compiler, directory)
        try:
            legend = server.request("initialize", {})["capabilities"]["semanticTokensProvider"]["legend"]
            types = {name: index for index, name in enumerate(legend["tokenTypes"])}
            bits = {name: 1 << index for index, name in enumerate(legend["tokenModifiers"])}
            uri = (Path(directory) / "main.ax").as_uri()
            document = {"textDocument": {"uri": uri}}
            server.send("textDocument/didOpen", {"textDocument": {
                "uri": uri, "languageId": "axiom", "version": 1, "text": source}})

            def expect_roles(text, data):
                tokens = decode(data)
                actual = {(line, column, length): (kind, modifiers)
                          for line, column, length, kind, modifiers in tokens}
                wanted = [
                    ("(type Count", 6, "Count", "type", bits["declaration"]),
                    ("point (Point", 0, "point", "variable", bits["declaration"] | bits["readonly"]),
                    ("(Point :y", 1, "Point", "enumMember", 0),
                    (":y", 1, "y", "property", 0),
                    (":x", 1, "x", "property", 0),
                    ("mut count", 0, "mut", "keyword", 0),
                    ("mut count", 4, "count", "variable", bits["declaration"]),
                    ("point.x", 6, "x", "property", 0),
                    ("point.x Count", 8, "Count", "type", 0),
                    ("(set count", 5, "count", "variable", bits["modification"]),
                    ("(as 1 Count", 6, "Count", "type", 0),
                ]
                for needle, skip, word, kind, modifiers in wanted:
                    offset = text.index(needle) + skip
                    assert text[offset:offset + len(word)] == word
                    pos = position(text, offset)
                    key = (pos["line"], pos["character"], len(word))
                    assert actual.get(key) == (types[kind], modifiers), (word, key, actual.get(key))
                return tokens

            first = server.request("textDocument/semanticTokens/full", document)
            expect_roles(source, first["data"])
            changed = "\n" + source.replace(":y (as 2 UInt8)", ":y (as 22 UInt8)")
            server.send("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2},
                                                   "contentChanges": [{"text": changed}]})
            delta = server.request("textDocument/semanticTokens/full/delta",
                                   {**document, "previousResultId": first["resultId"]})
            rebuilt = apply_delta(first["data"], delta)
            decoded = expect_roles(changed, rebuilt)
            full = server.request("textDocument/semanticTokens/full", document)
            assert full["data"] == rebuilt
            start = position(changed, changed.index("(let mut"))
            end = position(changed, changed.index("    (as count Int)"))
            ranged = server.request("textDocument/semanticTokens/range",
                                    {**document, "range": {"start": start, "end": end}})
            expected = [token for token in decoded
                        if (token[0], token[1] + token[2]) > (start["line"], start["character"])
                        and (token[0], token[1]) < (end["line"], end["character"])]
            assert ranged == {"data": encode(expected)}, (ranged, expected)
            server.close()
            assert {p["version"] for p in server.publications} == {1, 2}, server.publications
            assert all(not [d for d in p["diagnostics"] if d["severity"] < 4]
                       for p in server.publications), server.publications
        finally:
            if server.process.poll() is None:
                server.process.kill()
                server.process.wait()
    print("ok   structured semantic tokens (named fields, UInt8 alias, mutable locals, "
          "field reads, exact roles and revised full/delta/range)")


def run(compiler):
    structured_tokens(compiler)
    with tempfile.TemporaryDirectory(prefix="axiom-lsp-tokens-") as directory:
        server = Server(compiler, directory)
        capabilities = server.request("initialize", {})["capabilities"]["semanticTokensProvider"]
        assert capabilities["range"] is True and capabilities["full"] == {"delta": True}
        types = {name: index for index, name in enumerate(capabilities["legend"]["tokenTypes"])}
        bits = {name: 1 << index for index, name in enumerate(capabilities["legend"]["tokenModifiers"])}
        uri = (Path(directory) / "main.ax").as_uri()
        second = (Path(directory) / "other.ax").as_uri()
        unopened = (Path(directory) / "unopened.ax").as_uri()
        source = "; 😀\r\n(:: main Int)\r\n(fn (main)\r\n  7)\r\n"
        tokens = [
            (0, 0, 4, types["comment"], 0),
            (1, 1, 2, types["operator"], 0),
            (1, 4, 4, types["function"], bits["declaration"]),
            (1, 9, 3, types["type"], bits["defaultLibrary"]),
            (2, 1, 2, types["keyword"], 0),
            (2, 5, 4, types["function"], bits["declaration"] | bits["definition"]),
            (3, 2, 1, types["number"], 0),
        ]
        identifiers = set()
        checks = 0

        def document(target):
            return {"textDocument": {"uri": target}}

        def opening(target, text, version=1):
            server.send("textDocument/didOpen", {"textDocument": {
                "uri": target, "languageId": "axiom", "version": version, "text": text}})

        def change(text, version):
            server.send("textDocument/didChange", {"textDocument": {"uri": uri, "version": version},
                                                   "contentChanges": [{"text": text}]})

        def check_id(response):
            identifier = response["resultId"]
            assert isinstance(identifier, str) and identifier and identifier not in identifiers, response
            identifiers.add(identifier)
            return identifier

        def full(target, expected):
            nonlocal checks
            response = server.request("textDocument/semanticTokens/full", document(target))
            assert response["data"] == encode(expected), (response, expected)
            checks += 1
            return check_id(response), response["data"]

        def delta(previous, data, expected, exact=None):
            nonlocal checks
            response = server.request("textDocument/semanticTokens/full/delta",
                                      {**document(uri), "previousResultId": previous})
            next_data = apply_delta(data, response)
            assert next_data == encode(expected), (response, decode(next_data), expected)
            if exact is not None:
                assert response["edits"] == exact, response
            checks += 1
            return check_id(response), next_data

        assert server.request("textDocument/semanticTokens/full", document(unopened)) == {"data": []}
        assert server.request("textDocument/semanticTokens/full/delta",
                              {**document(unopened), "previousResultId": "unknown"}) == {"data": []}
        opening(uri, source)
        original, data = full(uri, tokens)

        # A range's first token is relative to document origin, and a partial
        # overlap includes the whole token. Empty and invalid ranges stay empty.
        ranges = [((1, 5), (1, 6)), ((1, 4), (1, 9)), ((0, 0), (0, 4)),
                  ((1, 0), (3, 0)), ((3, 2), (3, 3)), ((0, 0), (5, 0)),
                  ((1, 4), (1, 4)), ((1, 8), (1, 4)), ((0, 3), (0, 4))]
        lines = re.split(r"\r\n|\r|\n", source)
        for start, end in ranges:
            range_value = {"start": dict(zip(("line", "character"), start)),
                           "end": dict(zip(("line", "character"), end))}
            response = server.request("textDocument/semanticTokens/range",
                                      {**document(uri), "range": range_value})
            valid = start < end and end[0] < len(lines) and start != (0, 3)
            expected = [token for token in tokens
                        if (token[0], token[1] + token[2]) > start
                        and (token[0], token[1]) < end] if valid else []
            assert response == {"data": encode(expected)}, (range_value, response, expected)
            checks += 1
        current, data = delta(original, data, tokens, exact=[])

        source = "\n" + source
        tokens = [(line + 1, *token) for line, *token in tokens]
        change(source, 2)
        current, data = delta(current, data, tokens,
                              exact=[{"start": 0, "deleteCount": 1, "data": [1]}])
        source = source.replace("  7)", "  42)")
        tokens[-1] = (*tokens[-1][:2], 2, *tokens[-1][3:])
        change(source, 3)
        current, data = delta(current, data, tokens,
                              exact=[{"start": 32, "deleteCount": 1, "data": [2]}])

        # Deletion and insertion include both empty and nonempty arrays.
        change("", 4)
        current, data = delta(current, data, [],
                              exact=[{"start": 0, "deleteCount": len(data), "data": []}])
        current, data = delta(current, data, [], exact=[])
        change(source, 5)
        current, data = delta(current, data, tokens,
                              exact=[{"start": 0, "deleteCount": 0, "data": encode(tokens)}])

        # Missing, stale and other-document bases fall back to a complete result.
        for previous in ["unknown", original]:
            response = server.request("textDocument/semanticTokens/full/delta",
                                      {**document(uri), "previousResultId": previous})
            assert response["data"] == data and "edits" not in response, response
            current = check_id(response)
            checks += 1
        opening(second, source)
        other_id, _ = full(second, tokens)
        response = server.request("textDocument/semanticTokens/full/delta",
                                  {**document(uri), "previousResultId": other_id})
        assert response["data"] == data and "edits" not in response, response
        current = check_id(response)
        checks += 1

        # Closing releases the cached array. A lower reopened document version
        # does not make an earlier token identifier valid again.
        server.send("textDocument/didClose", document(uri))
        response = server.request("textDocument/semanticTokens/full/delta",
                                  {**document(uri), "previousResultId": current})
        assert response["data"] == [] and "edits" not in response, response
        closed_id = check_id(response)
        opening(uri, source, -5)
        response = server.request("textDocument/semanticTokens/full/delta",
                                  {**document(uri), "previousResultId": current})
        assert response["data"] == data and "edits" not in response, response
        current = check_id(response)
        assert current != closed_id
        checks += 2

        # Many deltas survive intervening diagnostic checks and arena resets.
        for version in range(50):
            value = 100 + version
            changed = source.replace("  42)", "  %d)" % value)
            next_tokens = tokens[:-1] + [(*tokens[-1][:2], len(str(value)), *tokens[-1][3:])]
            change(changed, version)
            current, data = delta(current, data, next_tokens)

        # Multi-line comments and strings split at CR, LF and CRLF. Their
        # UTF-16 lengths never cross an editor line boundary.
        multiline = '#| one\r\ntwo 😀\rthree |#\n(:: main String)\n(fn (main) "a\r\nb😀\rc")\n'
        change(multiline, 100)
        response = server.request("textDocument/semanticTokens/full/delta",
                                  {**document(uri), "previousResultId": current})
        rebuilt = apply_delta(data, response)
        check_id(response)
        decoded = decode(rebuilt)
        lines = re.split(r"\r\n|\r|\n", multiline)
        for line, column, length, _, _ in decoded:
            assert length > 0 and column + length <= len(lines[line].encode("utf-16-le")) // 2
        comments = [(line, column, length) for line, column, length, kind, _ in decoded
                    if kind == types["comment"]]
        strings = [(line, column, length) for line, column, length, kind, _ in decoded
                   if kind == types["string"]]
        assert comments == [(0, 0, 6), (1, 0, 6), (2, 0, 8)], comments
        assert strings == [(4, 11, 2), (5, 0, 3), (6, 0, 2)], strings
        checks += 1
        server.close()
        assert all(not [d for d in p["diagnostics"] if d["severity"] < 4]
                   for p in server.publications), server.publications
        print("ok   semantic-token-updates (%d independent full/range/delta checks, "
              "exact integer edits, UTF-16 boundaries and cache lifecycle)" % checks)


if __name__ == "__main__":
    run(str(Path(sys.argv[1]).resolve()))
