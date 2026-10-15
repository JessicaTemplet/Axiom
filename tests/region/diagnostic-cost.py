#!/usr/bin/env python3
"""Region analysis must not resolve unused diagnostic names per argument."""
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time


compiler = str(Path(sys.argv[1]).resolve())
sizes = (8000, 16000)
bound = 3.0

with tempfile.TemporaryDirectory(prefix="axiom-region-diagnostic-cost-") as directory:
    sources = {}
    for size in sizes:
        # All callers name the last definition. Its scalar parameters
        # store nothing, so region analysis needs no parameter labels.
        # The region in main ensures the complete analysis runs.
        text = "(:: leaf (-> Int Int))\n"
        text += "".join(
            f"(:: caller{i} (-> Int Int))\n"
            f"(fn (caller{i} value) (leaf value))\n"
            for i in range(size)
        )
        text += "(fn (leaf value) (+ value 1))\n"
        text += "(:: main Int)\n(fn (main) (region local (caller0 1)))\n"
        path = Path(directory, f"callers-{size}.ax")
        path.write_text(text)
        sources[size] = path

    times = {size: [] for size in sizes}
    for repetition in range(3):
        for size in sizes:
            start = time.monotonic()
            try:
                result = subprocess.run(
                    [compiler, "--diagnostic-format=ai", "check", str(sources[size])],
                    capture_output=True, text=True, timeout=120,
                )
            except subprocess.TimeoutExpired:
                raise SystemExit(f"FAIL region analysis timed out at {size} callers")
            elapsed = time.monotonic() - start
            if result.returncode:
                raise SystemExit(
                    f"FAIL valid region program at {size} callers: "
                    f"exit {result.returncode}\n{result.stdout}{result.stderr}"
                )
            times[size].append(elapsed)

    small, large = (statistics.median(times[size]) for size in sizes)
    if small < 0.05:
        raise SystemExit("FAIL region timing is below 0.05s; the growth ratio is noise")
    ratio = large / small
    print(
        f"region diagnostic cost: {sizes[0]} callers {small:.3f}s, "
        f"{sizes[1]} callers {large:.3f}s, growth {ratio:.2f} (bound {bound:.2f})"
    )
    if ratio >= bound:
        raise SystemExit("FAIL region analysis scans the declaration list per argument")
    print("ok   doubling callers stays below the region-analysis growth bound")
