#!/usr/bin/env python3
"""Check long declarations, typed lists and patterns without golden output."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def main():
    compiler = str(Path(sys.argv[1]).resolve())
    fixtures = Path(__file__).resolve().parent / 'parity'
    with tempfile.TemporaryDirectory(prefix='axiom-fmt-readability-') as directory:
        path = Path(directory) / 'Main.ax'
        for number in range(258, 267):
            matches = list(fixtures.glob(f'{number}-*.axp'))
            assert len(matches) == 1, matches
            source = matches[0].read_text()
            formatted = subprocess.run([compiler, 'fmt', '-'], input=source,
                capture_output=True, text=True, timeout=30)
            assert formatted.returncode == 0, (number, formatted.stderr)
            second = subprocess.run([compiler, 'fmt', '-'], input=formatted.stdout,
                capture_output=True, text=True, timeout=30)
            assert second.returncode == 0 and second.stdout == formatted.stdout, (number, 'unstable layout')
            assert all(len(line) <= 100 for line in formatted.stdout.splitlines()), (number, formatted.stdout)
            assert re.findall(r';[^\n]*', source) == re.findall(r';[^\n]*', formatted.stdout), (number, 'comments changed')
            if number == 265:
                first_field = next(line for line in formatted.stdout.splitlines() if '; first field stays here' in line)
                assert 'first = firstPayloadValue,' in first_field, first_field
            # Run both sources. Source-location tables legitimately change
            # with layout, while each program's result must stay the same.
            executable = Path(directory) / 'program'
            expected = 8 if number == 260 else 42
            for text in (source, formatted.stdout):
                path.write_text(text)
                built = subprocess.run([compiler, '--diagnostic-format=ai', 'build',
                    '--input', str(path), '--output', str(executable), '--opt', '0'],
                    capture_output=True, timeout=30)
                assert built.returncode == 0, (number, built.stderr.decode())
                ran = subprocess.run([str(executable)], capture_output=True, timeout=30)
                assert ran.returncode == expected, (number, ran.returncode, ran.stderr.decode())
    # Reader nodes for signed literals must fuse without accepting
    # overflowing literals, missing operands or the removed spelling.
    for expression in ('(as 9223372036854775808 Int)', '(as -9223372036854775809 Int)',
                       '(as -1)', '(as 1 Int 2)', '(as 1 -1)', '(as - 1 Int)', '(cast Int 1)'):
        refused = subprocess.run([compiler, 'fmt', '-'],
            input=f'(:: main Int)\n(fn (main) {expression})\n',
            capture_output=True, text=True, timeout=30)
        assert refused.returncode != 0 and not refused.stdout, (expression, refused.stdout)
    print('formatter readability: nine long-list and conversion programs fit 100 columns, preserve comments, are fixed points and keep native results')


if __name__ == '__main__':
    main()
