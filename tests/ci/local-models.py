#!/usr/bin/env python3
"""Exercise lexical scope in the two independent migration oracles."""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DDC = load('axiom_ddc', 'scripts/lib/ddc-interp.py')
REPL = load('axiom_repl_model', 'tests/repl/verify-repl.py')


def ddc(expression):
    forms = DDC.parse(DDC.tokenize('(fn (main) ' + expression + ')'))
    return DDC.Interp(forms).run()


def repl(expression):
    return REPL.ev(REPL.read_line(expression), {}, {}, REPL.Budget())


class ScopeModels(unittest.TestCase):
    def test_shadow_initializer_sees_previous_binding(self):
        expression = '{ (let x 40) (let x (+ x 2)) x }'
        self.assertEqual(ddc(expression), 42)
        self.assertEqual(repl(expression).v, 42)

    def test_nested_scope_does_not_overwrite_parent(self):
        expression = '{ (let x 2) (let y { (let x (+ x 1)) x }) (+ x y) }'
        self.assertEqual(ddc(expression), 5)
        self.assertEqual(repl(expression).v, 5)

    def test_initializer_cannot_read_its_own_new_binding(self):
        expression = '{ (let x (+ x 1)) x }'
        with self.assertRaises(DDC.InterpError):
            ddc(expression)
        with self.assertRaises(REPL.Undefined):
            repl(expression)

    def test_nested_name_cannot_escape(self):
        expression = '{ { (let hidden 42) hidden } hidden }'
        with self.assertRaises(DDC.InterpError):
            ddc(expression)
        with self.assertRaises(REPL.Undefined):
            repl(expression)

    def test_parenthesised_let_is_not_an_expression(self):
        expression = '(let ((x 42)) x)'
        with self.assertRaises(DDC.InterpError):
            ddc(expression)
        with self.assertRaises(REPL.Unmodelled):
            repl(expression)

    def test_mismatched_delimiters_are_refused(self):
        for expression in ('{ (+ 40 2) )', '({ 42 )}', '{ 42', '}'):
            with self.subTest(expression=expression):
                with self.assertRaises(DDC.InterpError):
                    ddc(expression)
                with self.assertRaises(REPL.Unmodelled):
                    repl(expression)

    def test_braces_in_literals_keep_their_contents(self):
        self.assertEqual(repl('{ (let text "{value}") text }').v, '{value}')
        self.assertEqual(repl("{ (let c '{') c }").v, '{')

    def test_unit_expressions_and_declaration_tails(self):
        for expression in ('()', '{ (let x 42) }'):
            with self.subTest(expression=expression):
                value = repl(expression)
                self.assertEqual(value.kind, '()')
                self.assertEqual(value.render(), '()')
                with self.assertRaises(DDC.InterpError):
                    ddc(expression)

    def test_numeric_forms_outside_the_model_are_explicit(self):
        for expression in ('1.25', '(as 255 UInt8)'):
            with self.assertRaises(REPL.Unmodelled):
                repl(expression)


if __name__ == '__main__':
    unittest.main()
