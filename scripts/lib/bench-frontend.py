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


def verify(probe, widths, transfer=False):
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
        checks = ([
            re.fullmatch(r"ok 1 \d+ -?\d+", expected[0]),
            re.fullmatch(r"ok 0 \d+ -?\d+", expected[1]),
            *[row == "fallback" for row in expected[2:]],
        ] if transfer else [
            re.fullmatch(r"ok 8 1 -?\d+", expected[0]),
            re.fullmatch(r"ok 1 0 -?\d+", expected[1]),
            expected[2].startswith("parse-error "),
            expected[3].startswith("lex-error "),
            expected[4] == "read-error 2",
        ])
        if not all(checks):
            raise RuntimeError(f"source-derived frontend probes failed: {expected}")
        for width in widths:
            _, actual = run(probe, width, files, "verify")
            if actual != expected:
                raise RuntimeError(f"{width} workers changed ordered parse results")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--transfer-probe", type=Path)
    parser.add_argument("--ast-transfer", action="store_true", help="include syntax packets and parent reconstruction")
    parser.add_argument("--phase-profile", action="store_true", help="measure encoding and reconstruction separately")
    parser.add_argument("--jobs", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--input", type=Path, nargs="+")
    args = parser.parse_args()
    if args.ast_transfer:
        if not args.transfer_probe:
            parser.error("--ast-transfer requires --transfer-probe")
        args.probe = args.transfer_probe
    if args.reps < 1 or any(width < 1 for width in args.jobs):
        parser.error("jobs and reps must be positive")
    widths = list(dict.fromkeys([1, *args.jobs]))
    files = args.input or sorted(Path("self_host").glob("*.ax"))
    if not files or any(not file.is_file() for file in files):
        parser.error("every input must be an existing source file")
    verify(args.probe, widths, args.ast_transfer)
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
    if args.ast_transfer:
        packet_bytes = sum(int(row.split()[2]) for row in fingerprints)
        print(f"Syntax packets: {packet_bytes:,} bytes ({packet_bytes / byte_count:.2f}x source)")
    print("workers    milliseconds    speedup")
    for result in results:
        result["speedup"] = serial / result["best_seconds"]
        print(f"{result['workers']:7d}    {1000 * result['best_seconds']:12.1f}    {result['speedup']:6.2f}x")
    print("Ordered results match; read, lex and parse failures retain their positions.")
    scope = (
        "Serial reads and parses directly. Workers include syntax packet encoding, transfer and parent reconstruction. Fingerprints checked outside timed runs."
        if args.ast_transfer else
        "Independent read, lex, annotation scan and parse; count packets returned over Task. Fingerprints checked outside timed runs."
    )
    print("Serial has no packet work; worker times include AST transfer and reconstruction." if args.ast_transfer else "Timings exclude AST transfer.")
    print("Dependency scheduling and later compiler phases are outside this measurement.")
    phases = {}
    if args.phase_profile:
        if not args.ast_transfer:
            parser.error("--phase-profile requires --ast-transfer")
        for mode in ("encode", "roundtrip"):
            samples = [run(args.probe, 1, files, mode)[0] for _ in range(args.reps)]
            phases[mode] = {"best_seconds": max(min(samples) - startup, 1e-9), "samples": samples}
        encode = phases["encode"]["best_seconds"] - serial
        decode = phases["roundtrip"]["best_seconds"] - phases["encode"]["best_seconds"]
        print(f"Serial phase differences: encoding {1000 * encode:.1f} ms; reconstruction {1000 * decode:.1f} ms")
    if args.json:
        args.json.write_text(json.dumps({
            "files": [str(file) for file in files], "source_bytes": byte_count,
            "repetitions": args.reps, "startup_seconds": startup,
            "startup_samples": startup_samples, "results": results,
            "scope": scope,
            "packet_bytes": packet_bytes if args.ast_transfer else None,
            "phase_profile": phases,
        }, indent=2) + "\n")


if __name__ == "__main__":
    main()
