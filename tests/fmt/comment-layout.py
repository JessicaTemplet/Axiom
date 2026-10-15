#!/usr/bin/env python3
"""Verify call-comment ownership and fixed points without blessing a snapshot."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def main():
    compiler = str(Path(sys.argv[1]).resolve())
    fixtures = Path(__file__).resolve().parent / 'parity'
    definitions = '''(:: f (-> Int Int Int))
(fn (f x y) (+ x y))
(:: g (-> Int Int Int))
(fn (g x y) (+ x y))
(:: h (-> Int Int Int))
(fn (h x y) (+ x y))
'''
    outputs = {}
    with tempfile.TemporaryDirectory(prefix='axiom-fmt-comments-') as directory:
        root = Path(directory)
        for number in (254, 255, 256):
            paths = list(fixtures.glob(f'{number}-*.axp'))
            assert len(paths) == 1, paths
            source = definitions + paths[0].read_text()
            # A formatter refusal must not be excused by a malformed example.
            original = root / f'{number}.ax'
            original.write_text(source)
            checked = subprocess.run([compiler, '--diagnostic-format=ai', 'check', str(original)],
                                     capture_output=True, text=True, timeout=30)
            assert checked.returncode == 0, checked.stderr
            formatted = subprocess.run([compiler, 'fmt', '-'], input=source,
                                       capture_output=True, text=True, timeout=30)
            assert formatted.returncode == 0, (number, formatted.stderr)
            second = subprocess.run([compiler, 'fmt', '-'], input=formatted.stdout,
                                    capture_output=True, text=True, timeout=30)
            assert second.returncode == 0 and second.stdout == formatted.stdout, (
                number, 'formatting is not a fixed point', second.stderr)
            assert re.findall(r';[^\n]*', source) == re.findall(r';[^\n]*', formatted.stdout), (
                number, 'comments changed or moved out of source order')
            output = root / f'{number}-formatted.ax'
            output.write_text(formatted.stdout)
            checked = subprocess.run([compiler, '--diagnostic-format=ai', 'check', str(output)],
                                     capture_output=True, text=True, timeout=30)
            assert checked.returncode == 0, checked.stderr
            outputs[number] = formatted.stdout

        # Read attachment from the formatted source, rather than a golden.
        first, second = '; first argument', '; second argument'
        lines = outputs[254].splitlines()
        assert next(line for line in lines if first in line).strip() == '(g 1 2)  ' + first
        assert next(line for line in lines if second in line).strip() == '(h 3 4)  ' + second
        following = outputs[255]
        main = following.index('(fn (main)')
        assert following.index('(g 1 2)', main) < following.index('(h\n', main) < following.index('; h explanation'), (
            'a comment inside h was attached to g')
        enclosing = outputs[256]
        main = enclosing.index('(fn (main)')
        assert enclosing.index('(f\n', main) < enclosing.index('; head explanation') < enclosing.index('(g 1 2)', main)
        assert enclosing.index('(g 1 2)', main) < enclosing.index('; closing explanation') < enclosing.rindex('))'), (
            'a closing comment left its call')

        # Every token is short; an over-width row here is a layout defect.
        width_case = fixtures / '257-enclosing-width-budget.axp'
        formatted = subprocess.run([compiler, 'fmt', '-'], input=width_case.read_text(),
                                   capture_output=True, text=True, timeout=30)
        assert formatted.returncode == 0, formatted.stderr
        assert all(len(line) <= 100 for line in formatted.stdout.splitlines()), formatted.stdout
        second = subprocess.run([compiler, 'fmt', '-'], input=formatted.stdout,
                                capture_output=True, text=True, timeout=30)
        assert second.returncode == 0 and second.stdout == formatted.stdout, second.stderr

    print('formatter layout: sibling attachments, nested/head/closing comments, valid source, enclosing width, fixed points and preservation passed')


if __name__ == '__main__':
    main()
