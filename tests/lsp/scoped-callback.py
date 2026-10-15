#!/usr/bin/env python3
"""Check scoped-callback diagnostics and their reclamation boundaries."""
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
    with tempfile.TemporaryDirectory(prefix="axiom-scoped-callback-") as directory:
        path = Path(directory) / "Scoped.ax"
        source = """(import Task)
(import Vec)
(:: main Int)
;@axiom:effect(io)
;@axiom:effect(spawn)
;@axiom:effect(block)
(fn (main)
  { (let saved (vecWithCapacity 4))
    (taskFold (lambda (i) \"answer\") 1 (taskOpts 1 32) 0
      (lambda (acc i result)
        (match result
          ((Ok text) { (vecPush saved text) acc })
          ((Err error) acc))))})
"""
        path.write_text(source)
        checked = boundaries.terminal(compiler, path)
        assert len(checked) == 2 and all(d["code"] == "AX3060" for d in checked), checked
        for diagnostic in checked:
            assert "this scoped callback invocation" in diagnostic["message"], diagnostic
            assert diagnostic["related"][0]["span"]["char_start"] == source.index("taskFold"), diagnostic
            assert "keeping another reference cannot postpone the reset" in " ".join(diagnostic["help"]), diagnostic
        published, _ = boundaries.editor(compiler, path, source)
        assert [d["code"] for d in published] == [d["code"] for d in checked], published
        for terminal, editor in zip(checked, published):
            for help_text in terminal["help"]:
                assert help_text in editor["message"], editor
            assert editor["relatedInformation"] == [{
                "location": {"uri": path.as_uri(),
                             "range": boundaries.source_range(source, "taskFold")},
                "message": terminal["related"][0]["label"]}], editor

        source = """(:: invoke (-> (-> String Int) Int))
;@axiom:scoped(step)
(fn (invoke step) (step "input"))
(:: opaque (-> (-> String Int) Int))
(fn (opaque step) (invoke step))
"""
        path.write_text(source)
        checked = boundaries.terminal(compiler, path)
        assert [d["code"] for d in checked] == ["AX3130"], checked
        published, _ = boundaries.editor(compiler, path, source)
        assert [d["code"] for d in published] == ["AX3130"], published
        for help_text in checked[0]["help"]:
            assert help_text in published[0]["message"], published
        explain = subprocess.run([compiler, "explain", "AX3130"],
                                 capture_output=True, check=True, timeout=60).stdout.decode()
        for clause in ("scalar result", "same scoped contract", "unsafe implementation",
                       "Aliasing or partially applying"):
            assert clause in explain, (clause, explain)
        tag = subprocess.run([compiler, "explain", "@scoped"],
                             capture_output=True, check=True, timeout=60).stdout.decode()
        assert "AX3130" in tag and "unsafe obligation" in tag, tag
    print("ok   scoped-callback (retention refusal, exact reclamation boundary, terminal/editor guidance and checked forwarding help)")


if __name__ == "__main__":
    try:
        run(sys.argv[1])
    except (AssertionError, subprocess.SubprocessError) as error:
        sys.exit("FAIL scoped-callback: " + str(error))
