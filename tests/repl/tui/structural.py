#!/usr/bin/env python3
"""Find terminal predicate calls without counting imports or quoted text."""
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('axiom_fuzz_reader', ROOT / 'scripts/lib/fuzz.py')
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def call_lines(source):
    top, _ = reader.scan(source)
    result = []

    def visit(node):
        if node.kind != 'form' or not node.children:
            return
        head = node.children[0]
        name = source[head.start:head.end] if head.kind == 'atom' else ''
        if name == 'import':
            return
        if name == 'isTerminal' and source[node.start] == '(':
            result.append(source.count('\n', 0, head.start) + 1)
        # Function headers are binders, including a function whose name
        # happens to be the predicate. Their bodies still contain calls.
        children = node.children[2:] if name in ('fn', 'lambda') else node.children
        for child in children:
            visit(child)

    for node in top:
        visit(node)
    return result


class Structural(unittest.TestCase):
    def test_import_layout_does_not_create_a_call(self):
        for declaration in ('(import IO (isTerminal stdin))',
                            '(import IO\n  (isTerminal stdin))'):
            self.assertEqual(call_lines(declaration), [])

    def test_comments_and_strings_are_not_calls(self):
        source = '; (isTerminal stdin)\n#| (isTerminal stdin) |#\n' \
                 '(fn (f) "(isTerminal stdin)")'
        self.assertEqual(call_lines(source), [])

    def test_every_real_call_is_counted(self):
        source = '(import IO\n (isTerminal stdin))\n' \
                 '(fn (f) { (isTerminal stdin) (isTerminal stdout) })'
        self.assertEqual(call_lines(source), [3, 3])

    def test_binders_are_not_calls(self):
        self.assertEqual(call_lines('(fn (isTerminal fd) (other fd))'), [])
        self.assertEqual(call_lines('(fn (f) (isTerminal stdin))'), [1])


if __name__ == '__main__':
    if sys.argv[1:] == ['--selftest']:
        unittest.main(argv=[sys.argv[0]])
    else:
        root = Path(sys.argv[1])
        for path in sorted((root / 'self_host').glob('*.ax')):
            # The gate counts decision lines: stdin and stdout may be
            # tested together in the same expression on one line.
            for line in sorted(set(call_lines(path.read_text()))):
                print(f'{path}:{line}:isTerminal')
