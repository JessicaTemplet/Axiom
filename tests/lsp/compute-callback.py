#!/usr/bin/env python3
"""Check compute contracts and terminal/editor guidance from shared facts."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile

spec = importlib.util.spec_from_file_location(
    "memory_boundaries", Path(__file__).with_name("memory-boundaries.py"))
boundaries = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boundaries)


def run(compiler):
    compiler = str(Path(compiler).resolve())
    os.environ.setdefault("AXIOM_STDLIB", str(Path(__file__).resolve().parents[2] / "stdlib"))
    with tempfile.TemporaryDirectory(prefix="axiom-compute-callback-") as directory:
        path = Path(directory) / "Compute.ax"
        source = """(import Mem)
(:: applyKernel (-> (-> Int Int) Int Int))
;@axiom:compute(worker)
(fn (applyKernel worker value) (worker value))
(:: hiddenRead (-> Int Int))
;@axiom:effect(pure)
(fn (hiddenRead address) (memGetWord address 0))
(:: main Int)
(fn (main) (applyKernel (lambda (i) (hiddenRead i)) 0))
"""
        path.write_text(source)
        checked = boundaries.terminal(compiler, path)
        assert [d["code"] for d in checked] == ["AX3131"], checked
        assert "__load64" in checked[0]["message"], checked
        assert "raw/runtime observers" in " ".join(checked[0]["help"]), checked
        published, answers = boundaries.editor(
            compiler, path, source, (("compute", source.index("compute") + 2),))
        assert [d["code"] for d in published] == ["AX3131"], published
        for help_text in checked[0]["help"]:
            assert help_text in published[0]["message"], published
        span = checked[0]["span"]
        assert published[0]["range"] == {
            "start": boundaries.position(source, span["char_start"]),
            "end": boundaries.position(source, span["char_end"])}, published
        hover = answers[2]["contents"]["value"]
        assert "AX3131" in hover and "local accumulators" in hover, hover
        explain = subprocess.run([compiler, "explain", "AX3131"],
                                 capture_output=True, check=True, timeout=60).stdout.decode()
        for clause in ("same contract", "Mut effects", "empty effect row",
                       "extend a region"):
            assert clause in explain, (clause, explain)
        tag = subprocess.run([compiler, "explain", "@compute"],
                             capture_output=True, check=True, timeout=60).stdout.decode()
        assert "AX3131" in tag and "transitively" in tag, tag
    print("ok   compute-callback (transitive raw observer refusal, terminal/editor range and help, hover and checked forwarding guidance)")


if __name__ == "__main__":
    try:
        run(sys.argv[1])
    except (AssertionError, subprocess.SubprocessError) as error:
        sys.exit("FAIL compute-callback: " + str(error))
