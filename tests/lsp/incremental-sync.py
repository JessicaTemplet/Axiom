#!/usr/bin/env python3
"""Assert document transactions over stdio without blessing protocol goldens."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def frame(message):
    body = json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False).encode()
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def unframe(data):
    messages = []
    while data:
        header, separator, rest = data.partition(b"\r\n\r\n")
        assert separator, "truncated response header"
        length = int(header.removeprefix(b"Content-Length: "))
        assert len(rest) >= length, "truncated response body"
        messages.append(json.loads(rest[:length]))
        data = rest[length:]
    return messages


def position(source, offset):
    lines = list(re.finditer(r"\r\n|\r|\n", source[:offset]))
    start = lines[-1].end() if lines else 0
    return {"line": len(lines),
            "character": len(source[start:offset].encode("utf-16-le")) // 2}


def canonical(source):
    """Expected layout for the small, single-line function fixtures below."""
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"(\(import [^\n]+\)\n)(?=\S)", r"\1\n", text)
    return re.sub(r"\((pub )?fn \((\w+)\) ([\w]+)\)",
                  lambda m: "(" + (m[1] or "") + "fn (" + m[2] + ")\n  " + m[3] + ")", text)


def run(compiler):
    with tempfile.TemporaryDirectory(prefix="axiom-lsp-sync-") as directory:
        root = Path(directory) / "space in path"
        root.mkdir()
        helper = root / "Helper.ax"
        helper.write_text("(pub :: answer Int)\n(pub fn (answer) 42)\n")
        uri = (root / "main.ax").as_uri()
        other = helper.as_uri()
        unknown = (root / "unopened.ax").as_uri()
        source = "; π 😀\r\n(import Helper)\r\n(:: alpha Int)\r\n(fn (alpha) 7)  \r\n"
        state = {uri: source, other: helper.read_text(), unknown: ""}
        messages = [{"id": "init", "method": "initialize", "params": {}}]
        expected_publications = []
        snapshots = {}

        def change(target, version, edits, accepted=None, diagnostics=()):
            messages.append({"method": "textDocument/didChange", "params": {
                "textDocument": {"uri": target, "version": version},
                "contentChanges": edits}})
            if accepted is not None:
                state[target] = accepted
                expected_publications.append((target, version, list(diagnostics)))

        def opening(target, version, text, accept=True):
            messages.append({"method": "textDocument/didOpen", "params": {
                "textDocument": {"uri": target, "languageId": "axiom",
                                 "version": version, "text": text}}})
            if accept:
                state[target] = text
                expected_publications.append((target, version, []))

        def snapshot(label, target=uri):
            snapshots[label] = (target, state[target])
            for method, suffix in [("formatting", "format"), ("documentSymbol", "outline")]:
                messages.append({"id": label + "/" + suffix,
                                 "method": "textDocument/" + method,
                                 "params": {"textDocument": {"uri": target}}})

        def edit_at(text, start, end, replacement):
            return {"range": {"start": position(text, start), "end": position(text, end)},
                    "text": replacement}

        # Unopened updates and duplicate opens do not acquire document ownership.
        change(unknown, 1, [{"text": source}])
        snapshot("unopened", unknown)
        opening(uri, 10, source)
        snapshot("open")
        opening(uri, 11, "(:: intruder Int)\n(fn (intruder) 0)\n", accept=False)
        snapshot("duplicate-open")

        # Each successive endpoint is computed on the preceding edit's result.
        edits = []
        text = state[uri]
        for old, new in [("π", "pi-extra"), ("😀", "🚀🛰️"),
                         ("alpha", "longName"), ("alpha", "longName")]:
            start = text.index(old)
            edits.append(edit_at(text, start, start + len(old), new))
            text = text[:start] + new + text[start + len(old):]
        change(uri, 11, edits, accepted=text)
        snapshot("sequential-unicode")
        for version in (9, 10, 11, -1):
            change(uri, version, [{"text": ""}])
        snapshot("stale")

        # A valid first edit followed by an endpoint inside a surrogate pair
        # leaves both the previous text and version intact.
        text = state[uri]
        start = text.index("longName")
        first = edit_at(text, start, start + len("longName"), "changed")
        speculative = text[:start] + "changed" + text[start + len("longName"):]
        emoji = position(speculative, speculative.index("🚀"))
        split = {**emoji, "character": emoji["character"] + 1}
        change(uri, 12, [first, {"range": {"start": split, "end": split}, "text": "X"}])
        snapshot("atomic-surrogate-refusal")
        change(uri, 12, [], accepted=text)
        snapshot("same-version-after-refusal")

        p0 = {"line": 0, "character": 0}
        p1 = {"line": 0, "character": 1}
        invalid_ranges = [
            {"start": p1, "end": p0},
            {"start": {"line": -1, "character": 0}, "end": p0},
            {"start": {"line": 1000, "character": 0}, "end": p0},
            {"start": {"line": 0, "character": 1.0}, "end": p0},
            {"start": {"line": 0}, "end": p0},
            {"start": None, "end": p0},
            None,
        ]
        for number, invalid in enumerate(invalid_ranges):
            change(uri, 20, [{"text": ""}, {"range": invalid, "text": ""}])
            snapshot("invalid-range-%d" % number)
        for number, invalid in enumerate([None, {}, "not an array", [{"text": False}], [{}]]):
            change(uri, 20, invalid)
            snapshot("invalid-change-%d" % number)
        for number, invalid in enumerate([None, "20", 20.0, 2147483648, -2147483649]):
            change(uri, invalid, [{"text": ""}])
            snapshot("invalid-version-%d" % number)

        # A column past a line's content clamps before CRLF, preserving both bytes.
        text = state[uri]
        end = {"line": 0, "character": 100000}
        updated = text.replace("\r\n", " tail\r\n", 1)
        change(uri, 20, [{"range": {"start": end, "end": end}, "text": " tail"}],
               accepted=updated)
        snapshot("clamped-column")

        # Replace across a CRLF boundary with CR, then edit a later line using
        # its new line number. The outline must agree with the Python positions.
        text = state[uri]
        end = text.index("(::")
        replacement = "; renewed 🛰️\n(import Helper)\r"
        edit = edit_at(text, 0, end, replacement)
        updated = replacement + text[end:]
        change(uri, 21, [edit], accepted=updated)
        snapshot("mixed-line-endings")

        # A full replacement followed by a ranged edit uses the new full text.
        full = "; reset\n(import Helper)\n(:: fresh Int)\n(fn (fresh) missing)  \n"
        start = full.index("fresh")
        edit = edit_at(full, start, start + 5, "fresh")
        change(uri, 22, [{"text": "discarded"}, {"text": full}, edit],
               accepted=full, diagnostics=["AX3001"])
        snapshot("full-then-range")
        offset = full.index("missing")
        fixed = full[:offset] + "answer" + full[offset + len("missing"):]
        change(uri, 23, [edit_at(full, offset, offset + len("missing"), "answer")], accepted=fixed)
        snapshot("imported-call")
        messages.append({"id": "imported-definition", "method": "textDocument/definition",
                         "params": {"textDocument": {"uri": uri},
                                    "position": position(fixed, fixed.index("answer"))}})

        # Metadata remains aligned across several documents and request resets.
        # Opening an imported document also refreshes its existing importer.
        expected_publications.append((uri, 23, []))
        opening(other, -5, state[other])
        snapshot("second-document", other)
        messages.append({"method": "textDocument/didClose",
                         "params": {"textDocument": {"uri": uri}}})
        expected_publications.append((uri, None, []))
        state[uri] = ""
        change(uri, 99, [{"text": source}])
        snapshot("closed")
        replacement = "(pub :: answer Int)\r(pub fn (answer) 42)\r"
        change(other, -4, [{"text": replacement}], accepted=replacement)
        snapshot("second-document-edited", other)
        messages.append({"method": "textDocument/didClose",
                         "params": {"textDocument": {"uri": uri}}})
        reopened = "; reopened 😀\n(:: again Int)\n(fn (again) 9)  \n"
        opening(uri, -3, reopened)
        snapshot("reopened")
        for number in range(25):
            old = state[uri]
            at = old.index("😀")
            updated = old[:at] + "😀" + old[at + 1:]
            change(uri, number, [edit_at(old, at, at + 1, "😀")], accepted=updated)
        snapshot("many-incremental-edits")
        messages.extend([{"id": "shutdown", "method": "shutdown", "params": None},
                         {"method": "exit", "params": None}])
        result = subprocess.run([compiler, "lsp"], input=b"".join(map(frame, messages)),
                                capture_output=True, cwd=root, timeout=60)
        assert result.returncode == 0, (result.returncode, result.stderr.decode())
        assert not result.stderr, result.stderr.decode()
        replies = unframe(result.stdout)
        answers = {m["id"]: m for m in replies if "id" in m}
        assert all("error" not in answer for answer in answers.values()), answers
        capabilities = answers["init"]["result"]["capabilities"]
        assert capabilities["textDocumentSync"] == {"openClose": True, "change": 2, "save": True}
        assert capabilities["positionEncoding"] == "utf-16"
        published = [m["params"] for m in replies
                     if m.get("method") == "textDocument/publishDiagnostics"]
        actual = [(p["uri"], p.get("version"),
                   [d["code"] for d in p["diagnostics"] if d["severity"] != 4])
                  for p in published]
        assert actual == expected_publications, (actual, expected_publications)
        for label, (target, text) in snapshots.items():
            edits = answers[label + "/format"]["result"]
            expected = canonical(text)
            assert edits is not None, (label, "formatting refused valid source")
            if edits:
                assert len(edits) == 1, (label, edits)
                assert edits[0]["newText"] == expected, (label, edits[0]["newText"], expected)
                assert edits[0]["range"] == {"start": position(text, 0),
                                             "end": position(text, len(text))}, label
            else:
                assert expected == text, (label, "lost or modified document", text)
            symbols = answers[label + "/outline"]["result"]
            names = list(re.finditer(r"\((?:pub )?fn \((\w+)\)", text))
            assert [s["name"] for s in symbols] == [m[1] for m in names], (label, symbols)
            for symbol, match in zip(symbols, names):
                assert symbol["selectionRange"] == {
                    "start": position(text, match.start(1)),
                    "end": position(text, match.end(1))}, (label, symbol)
        definition = answers["imported-definition"]["result"]
        assert definition and definition["uri"] == other, definition
        assert answers["shutdown"]["result"] is None
        assert len(answers) == 2 * len(snapshots) + 3
        print("ok   incremental-sync (%d document snapshots, %d versioned checks, "
              "UTF-16 edits, atomic refusals, imports and close/reopen)" %
              (len(snapshots), len(expected_publications) - 1))


if __name__ == "__main__":
    run(str(Path(sys.argv[1]).resolve()))
