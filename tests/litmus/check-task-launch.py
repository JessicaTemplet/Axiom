#!/usr/bin/env python3
"""Pin cancellation between launches with one controlled legal interleaving.

A copied Task module sets its real token immediately after a successful
spawn. The production loop must launch one task; removing only its new
between-spawn poll must launch the entire window. Both variants join and
reap their children, retain submit order and deliver every result once.
No scheduling or wall-clock threshold decides the verdict.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

SOURCE = """(import IO)
(import Task)
(import Vec)
(import Fmt)
(import Str)
(import Err)
(import Sys)
(:: main Int)
;@axiom:effect(io)
;@axiom:effect(spawn)
;@axiom:effect(block)
(fn (main)
  (match taskTokenNew
    ((Err error) (sysExitWith 1))
    ((Ok token)
      { (let results (taskMapWith (lambda (i) (fmtInt i)) 64
                      (taskWithToken (taskOpts 32 32) token)))
        { (let started (taskTestStartCount token))
          {
            (if (!= (vecLen results) 64) (sysExitWith 2))
            (for i in 0..64
              (match (vecGet results i)
                ((Ok text)
                  (if (|| (>= i started) (! (strEq text (fmtInt i)))) (sysExitWith 3)))
                ((Err error)
                  (if (|| (< i started) (!= error.code taskCancelledCode)) (sysExitWith 4)))))
            (for i in 0..started
              (match (sysKill (taskTestPid token i) 0)
                ((Ok value) (sysExitWith 5))
                ((Err error) (if (!= error.code 3) (sysExitWith 6)))))
            (println \"{started} ordered results, all children reaped\")
            0
          }}}
    )))
"""
HELPERS = """
; Test-only scheduling seam. This is added to a copied module.
(:: taskTestAfterStart (-> Int Int (Vec Spawn) Int Int))
;@axiom:effect(io)
(fn (taskTestAfterStart token index handles width)
  {
    (taskStoreAt (+ token 16) (+ index 1))
    (taskStoreAt (+ token (* 8 (+ index 3)))
      (taskHandlePid (vecGet handles (% index width))))
    (taskCancelAt token)
  })
(pub :: taskTestStartCount (-> CancelToken Int))
(pub fn (taskTestStartCount token)
  (taskLoadAt (+ (taskTokenAt token) 16)))
(pub :: taskTestPid (-> CancelToken Int Int))
(pub fn (taskTestPid token index)
  (taskLoadAt (+ (taskTokenAt token) (* 8 (+ index 3)))))
"""


def run(compiler, root):
    compiler = str(Path(compiler).resolve())
    root = Path(root).resolve()
    source = (root / "stdlib/Task.ax").read_text()
    hook = "                (set next (+ next 1))\n                (set progress 1)"
    poll = "(&& (! (taskCancelledAt tok)) (&& (< next n) (< (- next head) w)))"
    assert source.count(hook) == 1, "launch interleaving seam moved or became ambiguous"
    assert source.count(poll) == 1, "between-spawn cancellation poll moved or became ambiguous"
    controlled = source.replace(hook, "                (taskTestAfterStart tok next hs w)\n" + hook) + HELPERS
    ablated = controlled.replace(poll, "(&& (< next n) (< (- next head) w))")
    checks = 0
    with tempfile.TemporaryDirectory(prefix="axiom-task-launch-") as directory:
        scratch = Path(directory)
        program = scratch / "Launch.ax"
        program.write_text(SOURCE)
        for name, text, expected in (("checked", controlled, 1), ("ablated", ablated, 32)):
            library = scratch / name
            shutil.copytree(root / "stdlib", library)
            (library / "Task.ax").write_text(text)
            environment = dict(os.environ, AXIOM_STDLIB=str(library))
            for optimisation in (0, 2):
                for threads in (False, True):
                    binary = scratch / f"{name}-O{optimisation}-{'threads' if threads else 'processes'}"
                    command = [compiler, "--diagnostic-format=ai", "build", str(program),
                               "--opt", str(optimisation), "--output", str(binary)]
                    if threads:
                        command.append("--threads")
                    build = subprocess.run(command, env=environment, capture_output=True, timeout=90)
                    assert build.returncode == 0, (command, build.stderr.decode())
                    result = subprocess.run([str(binary)], env=environment, capture_output=True, timeout=30)
                    wanted = f"{expected} ordered results, all children reaped\n".encode()
                    assert result.returncode == 0 and result.stdout == wanted, (
                        binary.name, result.returncode, result.stdout, result.stderr)
                    print(f"ok   {binary.name}: {expected} launches, 64 ordered slots, every child reaped", flush=True)
                    checks += 1
    print(f"task launch cancellation: {checks} native checks passed")


if __name__ == "__main__":
    try:
        run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else Path(__file__).resolve().parents[2])
    except (AssertionError, subprocess.SubprocessError) as error:
        sys.exit("FAIL task launch cancellation: " + str(error))
