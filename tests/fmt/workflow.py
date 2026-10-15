#!/usr/bin/env python3
"""Exercise formatting as a user workflow, including non-writing modes."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def apply_diff(original, diff):
    """Apply a unified hunk independently, checking its positions and counts."""
    lines = diff.splitlines(keepends=True)
    assert lines[0].startswith('--- ') and lines[1].startswith('+++ ')
    match = re.fullmatch(r'@@ -(\d+),(\d+) \+(\d+),(\d+) @@\n', lines[2])
    assert match, lines[2]
    old_start, old_count, new_start, new_count = map(int, match.groups())
    old = original.splitlines(keepends=True)
    start = old_start - (old_count != 0)
    assert start == new_start - (new_count != 0)
    operations = []
    for line in lines[3:]:
        if line == '\\ No newline at end of file\n':
            marker, text = operations[-1]
            assert text.endswith('\n')
            operations[-1] = marker, text[:-1]
        else:
            assert line[0] in ' +-', line
            operations.append((line[0], line[1:]))
    removed = [text for marker, text in operations if marker != '+']
    added = [text for marker, text in operations if marker != '-']
    assert len(removed) == old_count and len(added) == new_count
    assert old[start:start + old_count] == removed
    return ''.join(old[:start] + added + old[start + old_count:])


def main():
    compiler = str(Path(sys.argv[1]).resolve())
    checks = 0
    with tempfile.TemporaryDirectory(prefix='axiom-fmt-workflow-') as directory:
        root = Path(directory)

        def run(*args, stdin=None, code=0, global_args=()):
            nonlocal checks
            result = subprocess.run([compiler, *global_args, 'fmt', *args], cwd=root,
                                    input=stdin, text=True, capture_output=True,
                                    timeout=30)
            assert result.returncode == code, (args, result.returncode,
                                               result.stdout, result.stderr)
            checks += 1
            return result

        source = '(:: main Int)\n(fn (main)   (+  40   2))\n'
        expected = '(:: main Int)\n(fn (main)\n  (+ 40 2))\n'
        path = root / 'input.ax'
        path.write_text(source)
        run('--check', 'input.ax', code=1)
        assert path.read_text() == source
        diff = run('input.ax', '--diff', code=1).stdout
        assert apply_diff(source, diff) == expected
        assert path.read_text() == source
        output = run('--emit', 'stdout', 'input.ax').stdout
        assert output == expected and path.read_text() == source
        assert run('-', stdin=source).stdout == expected
        assert apply_diff(source, run('-', '--diff', stdin=source, code=1).stdout) == expected
        run('input.ax')
        assert path.read_text() == expected
        before = path.stat().st_mtime_ns
        run('input.ax')
        assert path.stat().st_mtime_ns == before
        run('--check', 'input.ax')
        assert run('--diff', 'input.ax').stdout == ''

        # A missing final newline is represented, not silently discarded by diff.
        path.write_text(source.rstrip('\n'))
        diff = run('--diff', 'input.ax', code=1).stdout
        assert '\\ No newline at end of file\n' in diff
        assert apply_diff(source.rstrip('\n'), diff) == expected

        other = root / 'other.ax'
        other.write_text(source)
        path.write_text(source)
        run('--check', 'input.ax', 'other.ax', code=1)
        assert path.read_text() == other.read_text() == source
        run('input.ax', 'other.ax')
        assert path.read_text() == other.read_text() == expected

        # Every input is prepared before the first write.
        path.write_text(source)
        other.write_text('(fn (main) (as 42))\n')
        run('input.ax', 'other.ax', code=1)
        assert path.read_text() == source
        run('input.ax', 'missing.ax', code=1)
        assert path.read_text() == source
        run('--emit=stdout', 'input.ax', 'other.ax', code=2)
        run('--check', '--diff', 'input.ax', code=2)
        run('--emit=other', 'input.ax', code=2)
        run('--diff=true', 'input.ax', code=2)
        run('--emit=stdout', '--check', 'input.ax', code=2)
        run('-', 'input.ax', stdin=source, code=2)
        run('-', '--emit=files', stdin=source, code=2)
        assert path.read_text() == source
        assert other.read_text() == '(fn (main) (as 42))\n'
        run('--input=input.ax')
        assert path.read_text() == expected
        unusual = root / '-input.ax'
        unusual.write_text(source)
        run('--', '-input.ax')
        assert unusual.read_text() == expected
        for name in ['--emit=stdout', '--emit=other', '--emit', '--check']:
            unusual = root / name
            unusual.write_text(source)
            run('--', name)
            assert unusual.read_text() == expected
        unusual = root / '--emit=other'
        unusual.write_text(source)
        run('--input', '--emit=other')
        assert unusual.read_text() == expected
        path.write_text(source)
        run(global_args=['--input=input.ax'])
        assert path.read_text() == expected
        run(code=1)

        # A successful pipe must have delivered its bytes.
        destination = root / 'unwritable-stdout'
        destination.write_bytes(b'')
        for args, stdin in [(['-'], source), (['--emit=stdout', 'input.ax'], None)]:
            with destination.open('rb') as output:
                result = subprocess.run([compiler, 'fmt', *args], cwd=root,
                                        input=stdin, text=True, stdout=output,
                                        stderr=subprocess.PIPE, timeout=30)
            checks += 1
            assert result.returncode != 0 and 'could not write' in result.stderr, result
            assert destination.read_bytes() == b''

        path.write_text(source)
        with destination.open('rb') as output:
            result = subprocess.run([compiler, 'fmt', '--diff', 'input.ax'], cwd=root,
                                    text=True, stdout=output, stderr=subprocess.PIPE,
                                    timeout=30)
        checks += 1
        assert result.returncode == 1 and result.stderr.count('could not write') == 1

        # Diff preserves context outside a changed middle section.
        source = ''.join(f'(fn (f{i}) {i})\n\n' for i in range(12))
        path.write_text(source)
        expected = run('--emit=stdout', 'input.ax').stdout
        diff = run('--diff', 'input.ax', code=1).stdout
        assert apply_diff(source, diff) == expected
        assert path.read_text() == source
    print(f'formatter workflow: {checks} executions, independent diff application and batch preflight passed')


if __name__ == '__main__':
    main()
