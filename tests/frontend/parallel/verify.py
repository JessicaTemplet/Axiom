#!/usr/bin/env python3
"""Hold worker syntax transfer and CLI results to the serial compiler."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]


def run(command, jobs=1):
    env = dict(os.environ, AXIOM_FRONTEND_JOBS=str(jobs))
    return subprocess.run(command, env=env, capture_output=True, timeout=180)


def identical(command, widths=(2, 4)):
    expected = run(command)
    for width in widths:
        actual = run(command, width)
        if (actual.returncode, actual.stdout, actual.stderr) != (
            expected.returncode, expected.stdout, expected.stderr
        ):
            raise AssertionError(
                f"{width} workers changed {command}: "
                f"serial exit {expected.returncode}, parallel exit {actual.returncode}\n"
                f"{actual.stderr.decode(errors='replace')[:2000]}"
            )
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--transfer", type=Path, required=True)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    compiler = str(args.compiler.resolve())
    transfer = str(args.transfer.resolve())
    packet_check = run([str(args.packets.resolve())])
    if packet_check.returncode or packet_check.stderr:
        raise AssertionError(f"packet checks failed: {packet_check}")
    # Full packet hashes cover every parsed field, including labels,
    # annotations and rule metadata, after each child has been reaped.
    files = sorted((ROOT / "self_host").glob("*.ax"))
    files += sorted((ROOT / "stdlib").glob("*.ax"))
    files += sorted((ROOT / "tests/selfhost").glob("*.ax"))
    expected = run([transfer, "1", "verify", *map(str, files)])
    if expected.returncode or expected.stderr:
        raise AssertionError("serial syntax round trip failed")
    rows = expected.stdout.splitlines()
    if len(rows) != len(files) or any(not row.startswith(b"ok ") for row in rows):
        raise AssertionError("the syntax packet corpus was not fully transferred")
    for width in (2, 4):
        actual = run([transfer, str(width), "verify", *map(str, files)])
        if (actual.returncode, actual.stdout, actual.stderr) != (0, expected.stdout, b""):
            raise AssertionError(f"{width} workers changed the syntax corpus")

    with tempfile.TemporaryDirectory(prefix="axiom-parallel-") as directory:
        root = Path(directory)
        sources = {
            "Leaf.ax": "(pub fn (value) 21)\n",
            "Left.ax": "(import Leaf)\n(pub macro (twice x) (+ x x))\n(pub fn (left) (Leaf::value))\n",
            "Right.ax": "(import Leaf)\n(pub fn (right) (Leaf::value))\n",
            # A cycle and diamond are merged exactly once, in DFS order.
            "Cycle.ax": "(import Left)\n(import Cycle)\n(pub fn (cycle) 0)\n",
            "Main.ax": "(import Left (twice))\n(import Right)\n(import Left (left))\n(import Cycle)\n(:: main Int)\n(fn (main) (Left::twice (+ (Left::left) (Right::right))))\n",
            "Broken.ax": '(fn (bad) "\\q")\n',
            "Nested.ax": "(import Broken)\n(pub fn (nested) 1)\n",
            "OtherBad.ax": "(fn (bad)\n",
            "Refused.ax": "(import Nested)\n(import OtherBad)\n(fn (main) 0)\n",
            "Missing.ax": "(import Absent)\n(import OtherBad)\n(fn (main) 0)\n",
            "Overlay.ax": "(pub fn (overlay) 1)\n",
            "Large.ax": " " * 5_000_000 + "(pub fn (large) 1)\n",
            "Variant.ax": "(pub fn (variant) 1)\n",
            "Variant.linux-x86_64.ax": "(pub fn (variant) 7)\n",
            "VariantMain.ax": "(import Variant)\n(import Left)\n(fn (main) (Variant::variant))\n",
            # This parses within the language's nesting limit but exceeds
            # the packet walk budget. The parent must parse it normally.
            "Deep.ax": "(pub fn (deep) " + "(+ 1 " * 600 + "0" + ")" * 600 + ")\n",
            "DeepMain.ax": "(import Deep)\n(import Right)\n(fn (main) (Deep::deep))\n",
        }
        for name, source in sources.items():
            (root / name).write_text(source)
        cached = run([str(args.cache.resolve()), str(root)])
        if cached.returncode or cached.stderr:
            raise AssertionError(f"worker cache or source overlay failed: {cached}")
        for name in ("Main.ax", "Refused.ax", "Missing.ax"):
            for format_name in ("ai", "json", "human"):
                result = identical([compiler, f"--diagnostic-format={format_name}", "check", str(root / name)])
                expected_status = 0 if name == "Main.ax" else 1
                if result.returncode != expected_status:
                    raise AssertionError(f"{name}: expected exit {expected_status}: {result.stderr!r}")
        for command in ("symbols", "emit-llvm"):
            result = identical([compiler, "--diagnostic-format=ai", command, str(root / "Main.ax")])
            if result.returncode:
                raise AssertionError(f"{command} failed: {result.stderr!r}")
        fallback = run([transfer, "2", "verify", str(root / "Deep.ax")])
        if fallback.returncode or fallback.stdout != b"fallback\n":
            raise AssertionError("the depth probe did not reach packet fallback")
        result = identical([compiler, "--diagnostic-format=ai", "emit-llvm", str(root / "DeepMain.ax")])
        if result.returncode:
            raise AssertionError(f"serial fallback failed: {result.stderr!r}")
        variant = identical([
            compiler, "--diagnostic-format=ai", "--target=linux-x86_64", "emit-llvm",
            str(root / "VariantMain.ax"),
        ])
        if variant.returncode or b"ret i64 7" not in variant.stdout:
            raise AssertionError("worker module lookup did not preserve the target suffix ladder")
        result = identical([compiler, "--diagnostic-format=ai", "run", str(root / "Main.ax")])
        if result.returncode != 84:
            raise AssertionError(f"macro graph returned {result.returncode}, expected 84")
        # Invalid experimental widths must keep serial semantics.
        for value in ("", "0", "-2", "9", "2x", "999999999999999999999999"):
            result = run([compiler, "--diagnostic-format=ai", "check", str(root / "Main.ax")], value)
            if result.returncode or result.stdout != b"OK\n" or result.stderr:
                raise AssertionError(f"invalid width {value!r} changed compilation")

    for name in (
        "1025-macro-literal-scopes.ax", "1026-macro-repeated-binders.ax",
        "1027-macro-declaration-bindings.ax", "1028-macro-syntax-identity.ax",
        "1029-macro-imported-literals.ax",
    ):
        result = identical([compiler, "--diagnostic-format=ai", "emit-llvm", str(ROOT / "tests/selfhost" / name)])
        if result.returncode:
            raise AssertionError(f"macro hygiene failed: {name}: {result.stderr!r}")
    print(f"parallel frontend: {len(files)} complete syntax packets; ordered diagnostics, symbols, IR and execution agree")


if __name__ == "__main__":
    main()
