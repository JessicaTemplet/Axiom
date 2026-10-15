#!/usr/bin/env python3
"""Check layout and behaviour of block locals and named construction."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

compiler = str(Path(sys.argv[1]).resolve())
fields = [f"fieldWithLongMeaningfulName{index}" for index in range(8)]
cases = [
    ("""(:: main Int)
(fn (main) {(let input 20) (let mut total input) (set total (+ total 2))
{(let input 1) (set total (+ total input))} (+ total 19)})
""", b""),
    ("""(import IO)
(struct Point (x : Int) (y : Int))
(:: mark (-> Int Int))
;@axiom:effect(io)
(fn (mark value) {(println value) value})
(:: main Int)
;@axiom:effect(io)
(fn (main) {(let point (Point :y (mark 2) :x (mark 1))) (+ 39 (+ point.x point.y))})
""", b"2\n1\n"),
    ("(struct State " + " ".join(f"({name} : Int)" for name in fields) + ")\n"
     "(:: main Int)\n(fn (main) {(let state (State "
     + " ".join(f":{name} {42 if index == 0 else index}" for index, name in enumerate(fields))
     + ")) state." + fields[0] + "})\n", b""),
    ("""(struct Point (x : Int) (y : Int))
(:: main Int)
(fn (main) {(let first ; first local
20) (let point (Point :y ; second field
22 :x first ; first field
)) (+ point.x point.y)})
""", b""),
]

with tempfile.TemporaryDirectory(prefix="axiom-route-b-layout-") as directory:
    path = Path(directory, "Main.ax")
    executable = Path(directory, "program")
    for index, (source, output) in enumerate(cases):
        first = subprocess.run([compiler, "fmt", "-"], input=source,
            capture_output=True, text=True, timeout=30)
        assert first.returncode == 0, (index, first.stdout, first.stderr)
        second = subprocess.run([compiler, "fmt", "-"], input=first.stdout,
            capture_output=True, text=True, timeout=30)
        assert second.returncode == 0 and second.stdout == first.stdout, (index, "unstable layout")
        assert all(len(line) <= 100 for line in first.stdout.splitlines()), (index, first.stdout)
        assert re.findall(r";[^\n]*", source) == re.findall(r";[^\n]*", first.stdout), (index, "comments changed")
        labels = r":([A-Za-z_][A-Za-z0-9_']*)"
        assert re.findall(labels, source) == re.findall(labels, first.stdout), (index, "fields reordered")
        if index == 2:
            rows = [line.strip() for line in first.stdout.splitlines() if line.lstrip().startswith(":" + fields[0][:-1])]
            assert len(rows) == len(fields), first.stdout
        for text in (source, first.stdout):
            path.write_text(text)
            built = subprocess.run([compiler, "--diagnostic-format=ai", "build", str(path),
                "-o", str(executable), "--opt", "0"], capture_output=True, timeout=60)
            assert built.returncode == 0, (index, built.stdout.decode(), built.stderr.decode())
            ran = subprocess.run([str(executable)], capture_output=True, timeout=30)
            assert ran.returncode == 42 and ran.stdout == output, (index, ran.returncode, ran.stdout)

for expression in ("(Point :x 1 2)", "(Point :x)", "(Point : 1 2)",
                   "{(let value)}", "{(let mut value)}", "{(let 1 2)}",
                   "(let value 1)", "(let ((value 1)) value)",
                   "{(let ((value 1)) value)}"):
    result = subprocess.run([compiler, "fmt", "-"],
        input=f"(:: main Int)\n(fn (main) {expression})\n",
        capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and not result.stdout, (expression, result.stdout)
print("four Route B layouts preserve field order, comments, scope and native behaviour; nine malformed or retired forms refuse")
