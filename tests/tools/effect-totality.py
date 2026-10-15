#!/usr/bin/env python3
"""Explain local constructor allocation combined with a field mutation."""
import importlib.util
import ast
from pathlib import Path
import re
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('axiom_fuzz_reader', ROOT / 'scripts/lib/fuzz.py')
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def head(source, node):
    if node.kind == 'form' and node.children and node.children[0].kind == 'atom':
        first = node.children[0]
        return source[first.start:first.end]
    return ''


def io_primitives(source):
    """Read complete registrations, including either slot of regFnEff2."""
    _, nodes = reader.scan(source)
    result = set()
    for node in nodes:
        kind = head(source, node)
        if kind not in ('regFnEff', 'regFnEff2') or len(node.children) < 5:
            continue
        name = node.children[2]
        if name.kind != 'str':
            continue
        for effect in node.children[4:]:
            if head(source, effect) != 'builtinEff' or len(effect.children) != 2:
                continue
            value = effect.children[1]
            if value.kind == 'str' and ast.literal_eval(source[value.start:value.end]) == 'IO':
                result.add(ast.literal_eval(source[name.start:name.end]))
    return sorted(result)


def constructor_store(path, name, constructors):
    """Prove both seeds from a direct, unshadowed constructor field store.

    Unknown shapes remain unexplained. In particular, a constructor-like
    local, a store of a pre-existing value and a local-slot assignment do
    not establish allocation plus field mutation.
    """
    try:
        source = Path(path).read_text()
    except OSError:
        return False
    top, _ = reader.scan(source)
    for declaration in top:
        parts = declaration.children
        if (head(source, declaration) == 'pub' and len(parts) == 4
                and source[parts[1].start:parts[1].end] == 'fn'):
            header, body = parts[2:]
        elif head(source, declaration) == 'fn' and len(parts) == 3:
            header, body = parts[1:]
        else:
            continue
        if head(source, header) != name:
            continue
        bound = {source[n.start:n.end] for n in header.children[1:]}
        nodes = []

        def visit(node):
            nodes.append(node)
            for child in node.children:
                visit(child)

        visit(body)
        for node in nodes:
            children = node.children
            kind = head(source, node)
            if kind == 'let' and len(children) >= 3:
                binder = children[2] if source[children[1].start:children[1].end] == 'mut' else children[1]
                bound.add(source[binder.start:binder.end])
            elif kind in ('fn', 'lambda', 'for') and len(children) >= 2:
                binder = children[1]
                bound.update(source[n.start:n.end] for n in (binder.children or [binder]))
            elif kind == 'match':
                for arm in children[2:]:
                    if arm.children:
                        # Conservative: any name in a pattern might bind.
                        stack = [arm.children[0]]
                        while stack:
                            item = stack.pop()
                            if item.kind == 'atom':
                                bound.add(source[item.start:item.end])
                            stack.extend(item.children)
        for node in nodes:
            if head(source, node) != 'set' or len(node.children) != 3:
                continue
            target, value = node.children[1:]
            field = source[target.start:target.end]
            constructor = head(source, value)
            if (target.kind == 'atom' and re.fullmatch(r'[^.\s]+\.[^.\s]+', field)
                    and constructor in constructors and constructor not in bound
                    and len(value.children) > 1):
                return True
    return False


def missing_rows(text, prefix):
    rows = [line.split(None, 3) for line in text.splitlines() if line.strip()]
    constructors = {row[1] for row in rows if row[0] == 'C'}
    # An imported function with the same spelling makes the source-only
    # proof ambiguous. Keep that case a finding rather than guessing.
    constructors -= {row[1] for row in rows if row[0] == 'F'}
    missing = []
    for row in rows:
        if len(row) != 4 or row[0] != 'F' or not row[2].startswith(prefix):
            continue
        _, name, location, metadata = row
        effects = re.search(r'(?:^| )#effects=([^ ]+)', metadata)
        if not effects or re.search(r'(?:^| )#calls=', metadata):
            continue
        effect_set = set(effects[1].split(','))
        if effect_set in ({'Mut'}, {'Alloc'}) or location.startswith(prefix + 'Ffi.ax:'):
            continue
        if re.search(r'(?:^| )#syscall=', metadata) and effect_set in ({'Block'}, {'Spawn'}, {'Entropy'}):
            continue
        path = re.sub(r':\d+:\d+(?:-\d+(?::\d+)?)?$', '', location)
        if effect_set == {'Alloc', 'Mut'} and constructor_store(path, name, constructors):
            continue
        missing.append((name, location))
    return missing


class Totality(unittest.TestCase):
    def test_io_registration_layout_and_effect_slots(self):
        source = '''
; (regFnEff fns "comment" ty (builtinEff "IO"))
(fn (example) "(regFnEff fns \\\"string\\\" ty (builtinEff \\\"IO\\\"))")
(regFnEff fns "one" ty (builtinEff "IO"))
(regFnEff2 fns "two"
  (mkTArr mkIntTy mkIntTy)
  (builtinEff "Alloc")
  (builtinEff "IO"))
(regFnEff2 fns "three" ty (builtinEff "IO") (builtinEff "Spawn"))
(regFnEff fns "pure" ty (builtinEff "Mut"))
'''
        self.assertEqual(io_primitives(source), ['one', 'three', 'two'])
        self.assertEqual(io_primitives(source.replace('(builtinEff "IO")', '(builtinEff "Mut")')), [])

    def test_only_a_proven_constructor_store_explains_both_effects(self):
        examples = [
            ('(fn (store box value) (set box.row (Some value)))', True),
            ('(pub fn (store box value) (set box.row (Some value)))', True),
            ('(fn (store box value Some) (set box.row (Some value)))', False),
            ('(fn (store box value) { (let Some other) (set box.row (Some value)) })', False),
            ('(fn (store box value) (set box.row value))', False),
            ('(fn (store box value) (set box (Some value)))', False),
            ('(fn (store box value) "(set box.row (Some value))")', False),
            ('(fn (store box value) (set box.row None))', False),
        ]
        with tempfile.TemporaryDirectory(prefix='axiom-totality-') as directory:
            path = Path(directory) / 'Rows.ax'
            prefix = directory + '/'
            for source, justified in examples:
                with self.subTest(source=source):
                    path.write_text(source)
                    text = f'C Some - "(a -> Option a)" @c\nF store {path}:1:6-11 "T" @f #effects=Alloc,Mut\n'
                    self.assertEqual(not missing_rows(text, prefix), justified)
                    self.assertTrue(missing_rows(text.replace('Alloc,Mut', 'Alloc,IO,Mut'), prefix))
            path.write_text(examples[0][0])
            text = f'C Some - "T" @c\nF Some - "T" @s\nF store {path}:1:6-11 "T" @f #effects=Alloc,Mut\n'
            self.assertTrue(missing_rows(text, prefix))

    def test_unreadable_source_does_not_justify_a_row(self):
        self.assertEqual(missing_rows('F eta /missing/D.ax:2:1-2 "T" @g #effects=Alloc,Mut', '/missing/'),
                         [('eta', '/missing/D.ax:2:1-2')])


if __name__ == '__main__':
    if sys.argv[1:] == ['--selftest']:
        unittest.main(argv=[sys.argv[0]])
    elif sys.argv[1] == '--io-primitives':
        print('\n'.join(io_primitives(Path(sys.argv[2]).read_text())))
    else:
        for name, location in missing_rows(Path(sys.argv[2]).read_text(), sys.argv[1]):
            print(name, location)
