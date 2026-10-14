# The language server's semantic tokens for a list of files, as JSON:
#   {file: [[line, column, length, type, [modifiers]], ...]}
# Positions are the protocol's: 0-based, UTF-16 code units, and every
# file in the corpus is ASCII, so a code unit is a byte.
#
# Usage: semantic-tokens.py AXC OUT.json FILE...
import json, os, pathlib, subprocess, sys

axc, out, files = sys.argv[1], sys.argv[2], sys.argv[3:]
server = subprocess.Popen([axc, "lsp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, env=os.environ)

def send(message):
    body = json.dumps(message).encode()
    server.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    server.stdin.flush()

def answer(want):
    while True:
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            byte = server.stdout.read(1)
            if not byte:
                sys.exit("the server closed its output before answering request %d" % want)
            head += byte
        length = next(int(l.split(b":")[1]) for l in head.split(b"\r\n") if l.lower().startswith(b"content-length"))
        message = json.loads(server.stdout.read(length))
        if message.get("id") == want:
            return message

send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"processId": None, "rootUri": pathlib.Path(".").resolve().as_uri(), "capabilities": {}}})
legend = answer(1)["result"]["capabilities"]["semanticTokensProvider"]["legend"]
types, modifiers = legend["tokenTypes"], legend["tokenModifiers"]
send({"jsonrpc": "2.0", "method": "initialized", "params": {}})

result = {}
for i, name in enumerate(files):
    uri = pathlib.Path(name).resolve().as_uri()
    send({"jsonrpc": "2.0", "method": "textDocument/didOpen",
          "params": {"textDocument": {"uri": uri, "languageId": "axiom", "version": 1,
                                      "text": open(name, encoding="utf-8").read()}}})
    send({"jsonrpc": "2.0", "id": 100 + i, "method": "textDocument/semanticTokens/full",
          "params": {"textDocument": {"uri": uri}}})
    data = answer(100 + i)["result"]["data"]
    line = column = 0
    tokens = []
    for k in range(0, len(data), 5):
        dline, dcol, length, kind, bits = data[k:k + 5]
        line += dline
        column = column + dcol if dline == 0 else dcol
        tokens.append([line, column, length, types[kind],
                       [modifiers[b] for b in range(len(modifiers)) if bits >> b & 1]])
    result[name] = tokens
    send({"jsonrpc": "2.0", "method": "textDocument/didClose", "params": {"textDocument": {"uri": uri}}})

send({"jsonrpc": "2.0", "id": 9, "method": "shutdown"})
answer(9)
send({"jsonrpc": "2.0", "method": "exit"})
json.dump(result, open(out, "w"))
print(f"{sum(len(t) for t in result.values())} semantic tokens from {len(result)} files")
