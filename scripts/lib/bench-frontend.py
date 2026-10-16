#!/usr/bin/env python3
"""Compare native serial parsing with bounded process worker parsing."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time


def run(probe, jobs, files, mode="measure"):
    start = time.perf_counter()
    result = subprocess.run(
        [str(probe), str(jobs), mode, *map(str, files)],
        capture_output=True, text=True, timeout=120,
    )
    elapsed = time.perf_counter() - start
    if result.returncode or result.stderr:
        raise RuntimeError(f"{jobs} workers failed: {result.stderr or result.stdout}")
    rows = result.stdout.splitlines()
    if len(rows) != len(files):
        raise RuntimeError(f"{jobs} workers returned {len(rows)} rows for {len(files)} files")
    return elapsed, rows


def verify(probe, widths):
    # These expectations come from source bytes, independently of the
    # worker's fingerprint. Delays and worker count cannot change order.
    with tempfile.TemporaryDirectory(prefix="axiom-frontend-") as root:
        root = Path(root)
        fixtures = {
            "valid.ax": "(fn (main) 7)\n",
            "empty.ax": "",
            "parse.ax": "(fn (main) 7\n",
            "lex.ax": '"\\q"\n',
        }
        for name, source in fixtures.items():
            (root / name).write_text(source)
        files = [root / name for name in fixtures] + [root / "missing.ax"]
        _, expected = run(probe, 1, files, "verify")
        checks = [
            re.fullmatch(r"ok 8 1 -?\d+", expected[0]),
            re.fullmatch(r"ok 1 0 -?\d+", expected[1]),
            expected[2].startswith("parse-error "),
            expected[3].startswith("lex-error "),
            expected[4] == "read-error 2",
        ]
        if not all(checks):
            raise RuntimeError(f"source-derived frontend probes failed: {expected}")
        for width in widths:
            _, actual = run(probe, width, files, "verify")
            if actual != expected:
                raise RuntimeError(f"{width} workers changed ordered parse results")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--jobs", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--input", type=Path, nargs="+")
    args = parser.parse_args()
    if args.reps < 1 or any(width < 1 for width in args.jobs):
        parser.error("jobs and reps must be positive")
    widths = list(dict.fromkeys([1, *args.jobs]))
    files = args.input or sorted(Path("self_host").glob("*.ax"))
    if not files or any(not file.is_file() for file in files):
        parser.error("every input must be an existing source file")
    verify(args.probe, widths)
    _, fingerprints = run(args.probe, 1, files, "verify")
    for width in widths:
        _, actual = run(args.probe, width, files, "verify")
        if actual != fingerprints:
            raise RuntimeError(f"{width} workers changed ordered source and token fingerprints")
    _, expected = run(args.probe, 1, files)
    for file, row in zip(files, expected):
        if not row.startswith("ok "):
            raise RuntimeError(f"{file}: {row}")
    startup_samples = [run(args.probe, 1, [])[0] for _ in range(args.reps)]
    startup = min(startup_samples)
    results = []
    for width in widths:
        samples = []
        for _ in range(args.reps):
            elapsed, actual = run(args.probe, width, files)
            if actual != expected:
                raise RuntimeError(f"{width} workers changed ordered frontend summaries")
            samples.append(elapsed)
        results.append({"workers": width, "raw_best_seconds": min(samples),
                        "best_seconds": max(min(samples) - startup, 1e-9), "samples": samples})
    serial = results[0]["best_seconds"]
    byte_count = sum(file.stat().st_size for file in files)
    print(f"{len(files)} files, {byte_count:,} bytes; best of {args.reps} runs")
    print(f"Process startup: {1000 * startup:.1f} ms, subtracted from each best time")
    print("workers    milliseconds    speedup")
    for result in results:
        result["speedup"] = serial / result["best_seconds"]
        print(f"{result['workers']:7d}    {1000 * result['best_seconds']:12.1f}    {result['speedup']:6.2f}x")
    print("Ordered summaries match; read, lex and parse failures retain their positions.")
    print("Timings exclude AST transfer, dependency scheduling and later compiler phases.")
    if args.json:
        args.json.write_text(json.dumps({
            "files": [str(file) for file in files], "source_bytes": byte_count,
            "repetitions": args.reps, "startup_seconds": startup,
            "startup_samples": startup_samples, "results": results,
            "scope": "Independent read, lex, annotation scan and parse; count packets returned over Task. Fingerprints checked outside timed runs.",
        }, indent=2) + "\n")


if __name__ == "__main__":
    main()
