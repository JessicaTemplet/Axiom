#!/usr/bin/env python3
"""Exercise source preservation, scope resolution and migration refusals."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "migrate-route-b.py"
SPEC = importlib.util.spec_from_file_location("route_b_migration", SCRIPT)
MIGRATION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MIGRATION
SPEC.loader.exec_module(MIGRATION)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="axiom-route-b-")
        self.root = Path(self.scratch.name)
        self.repository = MIGRATION.Repository(self.root)
        self.path = self.root / "main.ax"

    def tearDown(self):
        self.scratch.cleanup()

    def migrate(self, source, **options):
        return MIGRATION.migrate_source(source, self.path, self.repository, **options)

    def module(self, name, source, directory="stdlib"):
        path = self.root / directory / (name.replace(".", "/") + ".ax")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        return path

    def test_sequential_locals_and_mutability(self):
        before = "(fn (f n) (let ((a n) (mut b (+ a 1)) (a (+ b 1))) (set b a) b))\n"
        after = "(fn (f n) { (let a n) (let mut b (+ a 1)) (let a (+ b 1)) (set b a) b})\n"
        self.assertEqual(self.migrate(before), after)
        self.assertEqual(self.migrate(after), after)

    def test_comments_strings_chars_and_quotes_preserved(self):
        before = '''; (let ((fake 1)) fake)
(fn (f)
  (let ; keyword comment
    ( ; binding-list comment
      (x "(let ((fake 2)) fake) \\\" hi") ; after value
      #| (let ((ignored 3)) #| nested |# ignored) |#
      (mut y ')') ; character
    ) ; after bindings
    '(let ((quoted 4)) quoted)
    y))
'''
        after = self.migrate(before)
        self.assertIn('; keyword comment', after)
        self.assertIn('; binding-list comment', after)
        self.assertIn('#| (let ((ignored 3)) #| nested |# ignored) |#', after)
        self.assertIn('"(let ((fake 2)) fake) \\\" hi"', after)
        self.assertIn("(let mut y ')')", after)
        self.assertIn("'(let ((quoted 4)) quoted)", after)
        self.assertEqual(before.count(";"), after.count(";"))
        MIGRATION.read(after, self.path)

    def test_nested_resource_scope_is_not_flattened(self):
        before = "(fn (f) { (let ((owner (open))) (region r (let ((x (use owner))) x))) (closed) })"
        after = self.migrate(before)
        self.assertEqual(after, "(fn (f) { { (let owner (open)) (region r { (let x (use owner)) x})} (closed) })")
        outer = MIGRATION.read(after, self.path)[0].children[2]
        self.assertEqual(outer.kind, "{")
        self.assertEqual(outer.children[0].kind, "{")
        self.assertEqual(outer.children[1].head, "closed")

    def test_empty_binding_scope_and_multiple_body_values(self):
        self.assertEqual(self.migrate("(fn (f) (let () (first) (last)))"), "(fn (f) {  (first) (last)})")

    def test_nested_generic_sealed_word_and_shared_records(self):
        before = '''(struct Point (x : Int) (mut y : Int))
(struct Box (a) (item : a))
(struct Ticket word shared (slot : Int))
(struct Secret sealed (value : Int))
(fn (f) (Box (Point (tick) (next))))
(fn (g) (Ticket 7))
(fn (h) (Secret 9))
'''
        after = self.migrate(before)
        self.assertIn("(Box :item (Point :x (tick) :y (next)))", after)
        self.assertIn("(Ticket :slot 7)", after)
        self.assertIn("(Secret :value 9)", after)
        self.assertEqual(after[:after.index("(fn")], before[:before.index("(fn")])
        self.assertEqual(self.migrate(after), after)

    def test_non_records_types_adts_and_patterns_untouched(self):
        before = '''(struct Point (x : Int) (y : Int))
(data Sum (Pair { x : Int, y : Int }))
(:: f (-> Point (Vec Point) Sum))
(type Points (Vec Point))
(fn (f p xs) (match p ((Point x y) (Pair x y))))
'''
        self.assertEqual(self.migrate(before), before)

    def test_function_and_lambda_shadow_constructor_names(self):
        before = '''(struct Point (x : Int) (y : Int))
(fn (apply Point) (Point 1 2))
(fn (f) (lambda (Point) (Point 1 2)))
(fn (g) (let ((Point (lambda (x y) x))) (Point 1 2)))
'''
        after = self.migrate(before)
        self.assertNotIn(":x", after)
        self.assertIn("{ (let Point (lambda (x y) x)) (Point 1 2)}", after)

    def test_initializer_uses_outer_scope_then_shadows(self):
        before = "(struct Point (x : Int)) (fn (f) (let ((Point (Point 7))) (Point 8)))"
        after = self.migrate(before)
        self.assertIn("(let Point (Point :x 7)) (Point 8)", after)

    def test_block_local_shadow_ends_at_brace(self):
        before = "(struct Point (x : Int)) (fn (f) { { (let Point (lambda (x) x)) (Point 1) } (Point 2) })"
        self.assertIn("(Point 1) } (Point :x 2)", self.migrate(before))

    def test_selective_and_qualified_imports(self):
        self.module("Geo", "(pub struct Point (x : Int) (y : Int)) (pub fn (Other x) x)")
        before = "(import Geo (Point)) (fn (f) (Geo::Point (Point 1 2) 3))"
        self.assertIn("(Geo::Point :x (Point :x 1 :y 2) :y 3)", self.migrate(before))
        excluded = "(import Geo (Other)) (fn (f) (Point 1 2))"
        self.assertEqual(self.migrate(excluded), excluded)

    def test_entry_directory_precedes_standard_library(self):
        self.module("Geo", "(pub struct Point (wrong : Int))")
        self.module("Geo", "(pub struct Point (right : Int))", directory=".")
        self.assertIn("(Point :right 1)", self.migrate("(import Geo) (fn (f) (Point 1))"))

    def test_nested_module_qualified_constructor_spellings(self):
        self.module("Geo.Inner", "(pub struct Point (x : Int))")
        before = "(import Geo.Inner) (fn (f) (Geo::Inner::Point (Geo.Inner::Point 1)))"
        self.assertIn("(Geo::Inner::Point :x (Geo.Inner::Point :x 1))", self.migrate(before))

    def test_transitive_records_keep_defining_module_qualification(self):
        self.module("Leaf", "(pub struct Point (x : Int))")
        self.module("Mid", "(import Leaf) (pub fn (mid) 1)")
        source = "(import Mid) (fn (f) (Point (Leaf::Point 1)))"
        self.assertIn("(Point :x (Leaf::Point :x 1))", self.migrate(source))
        wrong = "(import Mid) (fn (f) (Mid::Point 1))"
        self.assertEqual(self.migrate(wrong), wrong)

    def test_selective_import_filters_its_own_module_only(self):
        self.module("Leaf", "(pub struct Point (x : Int)) (pub fn (id x) x)")
        self.module("Mid", "(import Leaf) (pub fn (mid) 1)")
        for selection in ("(mid)", "()"):
            source = f"(import Mid {selection}) (fn (f) (Point 1))"
            self.assertIn("(Point :x 1)", self.migrate(source))
        self.module("Selected", "(import Leaf (id)) (pub fn (selected) 1)")
        for spelling in ("Point", "Leaf::Point", "Selected::Point"):
            source = f"(import Selected) (fn (f) ({spelling} 1))"
            self.assertEqual(self.migrate(source), source)

    def test_transitive_diamond_deduplicates_defining_symbols(self):
        self.module("Leaf", "(pub struct Point (x : Int))")
        self.module("Left", "(import Leaf)")
        self.module("Right", "(import Leaf)")
        source = "(import Left) (import Right) (fn (f) (Point 1))"
        self.assertIn("(Point :x 1)", self.migrate(source))
        module = MIGRATION.Module(self.path, source, MIGRATION.read(source, self.path))
        imported, errors = self.repository.imported_symbols(module)
        self.assertEqual(len(imported["Point"]), 1)
        self.assertEqual(errors, [])

    def test_transitive_cycles_terminate_and_preserve_qualification(self):
        self.module("A", "(import B) (pub struct First (a : Int))")
        self.module("B", "(import A) (pub struct Second (b : Int))")
        source = "(import A) (fn (f) (First (B::Second 1)))"
        self.assertIn("(First :a (B::Second :b 1))", self.migrate(source))

    def test_distinct_transitive_selection_paths_union_visibility(self):
        self.module("Leaf", "(pub struct Point (x : Int)) (pub fn (id x) x)")
        self.module("Left", "(import Leaf (Point))")
        self.module("Right", "(import Leaf (id))")
        for imports in ("(import Left) (import Right)", "(import Right) (import Left)",
                        "(import Leaf (id)) (import Left)"):
            self.assertIn("(Point :x 1)", self.migrate(imports + " (fn (f) (Point 1))"))

    def test_entry_declarations_take_precedence_without_hiding_exported_collisions(self):
        self.module("Leaf", "(pub struct Point (leaf : Int))")
        source = "(import Leaf) (struct Point (own : Int)) (fn (f) (Point 1))"
        self.assertIn("(Point :own 1)", self.migrate(source))
        function = "(import Leaf) (fn (Point x) x) (fn (f) (Point 1))"
        self.assertEqual(self.migrate(function), function)
        self.module("Outer", "(import Leaf) (pub struct Point (outer : Int))")
        with self.assertRaisesRegex(MIGRATION.MigrationError, "ambiguous constructor"):
            self.migrate("(import Outer) (fn (f) (Point 1))")
        explicit = "(import Outer) (fn (f) (Outer::Point (Leaf::Point 1)))"
        self.assertIn("(Outer::Point :outer (Leaf::Point :leaf 1))", self.migrate(explicit))

    def test_transitive_private_declarations_are_invisible(self):
        self.module("Leaf", "(pub struct Point (leaf : Int)) (struct Secret (hidden : Int))")
        self.module("Mid", "(import Leaf) (struct Point (private : Int))")
        self.assertIn("(Point :leaf 1)", self.migrate("(import Mid) (fn (f) (Point 1))"))
        source = "(import Mid) (fn (f) (Secret 1))"
        self.assertEqual(self.migrate(source), source)

    def test_transitive_search_keeps_entry_root_precedence(self):
        self.module("Leaf", "(pub struct Point (root : Int))", directory=".")
        self.module("Nested.Leaf", "(pub struct Point (wrong : Int))", directory=".")
        self.module("Nested.Mid", "(import Leaf)", directory=".")
        source = "(import Nested.Mid) (fn (f) (Point 1))"
        self.assertIn("(Point :root 1)", self.migrate(source))

    def test_transitive_unresolved_imports_refuse_known_constructors(self):
        self.module("Leaf", "(pub struct Point (x : Int))")
        self.module("Mid", "(import Leaf) (import Missing)")
        with self.assertRaisesRegex(MIGRATION.MigrationError, "cannot prove constructor.*Missing"):
            self.migrate("(import Mid) (fn (f) (Point 1))")

    def test_ambiguous_imports_refuse_with_location(self):
        self.module("A", "(pub struct Point (x : Int))")
        self.module("B", "(pub struct Point (x : Int))")
        with self.assertRaisesRegex(MIGRATION.MigrationError, r"main.ax:3:.*ambiguous constructor"):
            self.migrate("(import A)\n(import B)\n(fn (f) (Point 1))")
        explicit = "(import A) (import B) (fn (f) (A::Point 1))"
        self.assertIn("(A::Point :x 1)", self.migrate(explicit))

    def test_same_name_function_and_record_refuse(self):
        self.module("A", "(pub struct Point (x : Int))")
        self.module("B", "(pub fn (Point x) x)")
        with self.assertRaisesRegex(MIGRATION.MigrationError, "ambiguous constructor"):
            self.migrate("(import A) (import B) (fn (f) (Point 1))")

    def test_platform_variants_must_agree(self):
        self.module("Geo", "(pub struct Point (x : Int))").rename(self.root / "stdlib" / "Geo.linux.ax")
        self.module("Geo", "(pub struct Point (x : Int))").rename(self.root / "stdlib" / "Geo.darwin.ax")
        source = "(import Geo) (fn (f) (Point 1))"
        self.assertIn("(Point :x 1)", self.migrate(source))
        self.repository.cache.clear()
        (self.root / "stdlib" / "Geo.darwin.ax").write_text("(pub struct Point (y : Int))")
        with self.assertRaisesRegex(MIGRATION.MigrationError, "ambiguous constructor"):
            self.migrate(source)

    def test_private_sealed_import_refused(self):
        self.module("Handles", "(pub struct Handle sealed (raw : Int))")
        with self.assertRaisesRegex(MIGRATION.MigrationError, "private outside"):
            self.migrate("(import Handles) (fn (f) (Handle 1))")

    def test_unresolved_import_cannot_hide_constructor_collision(self):
        self.module("A", "(pub struct Point (x : Int))")
        with self.assertRaisesRegex(MIGRATION.MigrationError, "cannot prove constructor.*Missing"):
            self.migrate("(import A) (import Missing) (fn (f) (Point 1))")

    def test_named_records_validate_without_reordering(self):
        before = "(struct Point (x : Int) (y : Int)) (fn (f) (Point :y (second) :x (first)))"
        self.assertEqual(self.migrate(before), before)
        spaced = "(struct Point (x : Int) (y : Int)) (fn (f) (Point : y (second) : x (first)))"
        self.assertEqual(self.migrate(spaced), spaced)
        for call in ["(Point :x 1 :x 2)", "(Point :x 1)", "(Point :z 1 :y 2)", "(Point 1)"]:
            with self.subTest(call=call), self.assertRaises(MIGRATION.MigrationError):
                self.migrate("(struct Point (x : Int) (y : Int)) (fn (f) " + call + ")")

    def test_higher_order_constructor_requires_explicit_review(self):
        with self.assertRaisesRegex(MIGRATION.MigrationError, "used as a value"):
            self.migrate("(struct Point (x : Int)) (fn (f) (map Point xs))")

    def test_range_and_container_loop_binding_scope(self):
        before = '''(struct Point (x : Int))
(fn (f) {
  (for Point in 0..(end) by 2 (Point 1))
  (for (Point i) in [(Point 1)] (Point i))
  (Point 9)
})'''
        after = self.migrate(before)
        self.assertIn("(for Point in 0..(end) by 2 (Point 1))", after)
        self.assertIn("in [(Point :x 1)] (Point i))", after)
        self.assertIn("(Point :x 9)", after)

    def test_parallel_initializers_do_not_see_new_bindings(self):
        before = "(struct Point (x : Int)) (fn (f) (parallel p ((Point (Point 1)) (n (Point 2))) (Point n)))"
        after = self.migrate(before)
        self.assertIn("((Point (Point :x 1)) (n (Point :x 2))) (Point n)", after)

    def test_simple_and_rule_macro_templates_migrate_patterns_do_not(self):
        before = '''(struct Point (x : Int))
(macro (simple x) (let ((n x)) (Point n)))
(macro rule (literals tag)
  ((rule (let x) tag) (let ((n x)) (Point n))))
'''
        after = self.migrate(before)
        self.assertIn("(macro (simple x) { (let n x) (Point :x n)})", after)
        self.assertIn("((rule (let x) tag) { (let n x) (Point :x n)})", after)

    def test_macro_function_templates_and_match_splices(self):
        before = '''(macro derive ((derive T)
  (:: (syntax/join show T) (-> T Int))
  (fn ((syntax/join show T) x)
    (match x (syntax/for (C (syntax/constructors T))
      ((C n) (let ((v n)) v)))))))'''
        self.assertIn("((C n) { (let v n) v})", self.migrate(before))

    def test_macro_type_arguments_and_dynamic_templates_stay_syntax(self):
        before = '''(struct Point (x : Int))
(macro build ((build T v) (fn (made) (T v))))
(macro fields ((fields T) (syntax/fields T)))
(build Point 7)
(fields Point)
'''
        self.assertEqual(self.migrate(before), before)

    def test_macro_expression_arguments_can_be_migrated(self):
        before = '''(struct Point (x : Int))
(macro (report e) (writeOut (syntax/formatln e)))
(fn (f) (report (let ((p (Point 7))) p)))'''
        self.assertIn("(report { (let p (Point :x 7)) p})", self.migrate(before))

    def test_macro_pattern_arguments_refuse_without_guessing(self):
        before = '''(struct Point (x : Int))
(macro capture ((capture pattern) (match value (pattern 7))))
(capture (Point 1))'''
        with self.assertRaisesRegex(MIGRATION.MigrationError, "opaque argument to macro"):
            self.migrate(before)

    def test_question_suffix_and_crlf_preserved(self):
        before = "(fn (f)\r\n  (let ((x (parse input)?)) x))\r\n"
        after = self.migrate(before)
        self.assertEqual(after, "(fn (f)\r\n  { (let x (parse input)?) x})\r\n")

    def test_markdown_fences_are_independent_and_prose_untouched(self):
        before = '''Example `(let ((x 1)) x)`.
```scheme
(struct Point (x : Int))
(fn (f) (let ((p (Point 1))) p))
```
```python
"(let ((not_axiom 1)) not_axiom)"
```
```scheme fragment
(fn (g) (Point 2))
```
'''
        after = MIGRATION.migrate_markdown(before, self.root / "page.md", self.repository)
        self.assertIn("Example `(let ((x 1)) x)`.", after)
        self.assertIn("(let p (Point :x 1))", after)
        self.assertIn('"(let ((not_axiom 1)) not_axiom)"', after)
        self.assertIn("(fn (g) (Point 2))", after)

    def test_markdown_refusal_reports_original_line(self):
        source = "Text.\n\n```scheme\n(fn (f) (let ((x 1))\n```\n"
        with self.assertRaisesRegex(MIGRATION.MigrationError, r"page.md:4:"):
            MIGRATION.migrate_markdown(source, self.root / "page.md", self.repository)

    def test_malformed_source_and_malformed_binding_refuse(self):
        for source in ["(let ((x 1))", '(f "unterminated)', "(let ((x 1 2)) x)", "(let ((x 1)))"]:
            with self.subTest(source=source), self.assertRaises(MIGRATION.MigrationError):
                self.migrate(source)

    def test_cli_review_check_write_and_atomic_preflight(self):
        self.path.write_text("(fn (f) (let ((x 1)) x))\n")
        command = [sys.executable, str(SCRIPT), "--root", str(self.root)]
        reviewed = subprocess.run(command + ["--review", str(self.path)], capture_output=True, text=True)
        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        self.assertIn("+(fn (f) { (let x 1) x})", reviewed.stdout)
        self.assertIn("(let ((x 1))", self.path.read_text())
        checked = subprocess.run(command + ["--check", str(self.path)], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 1)
        invalid = self.root / "invalid.axbad"
        invalid.write_text("(let ((x 1))")
        failed = subprocess.run(command + ["--write", str(self.path), str(invalid)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("(let ((x 1))", self.path.read_text())
        skipped = subprocess.run(command + ["--write", "--skip-invalid", str(self.path), str(invalid)], capture_output=True, text=True)
        self.assertEqual(skipped.returncode, 2)
        self.assertEqual(self.path.read_text(), "(fn (f) { (let x 1) x})\n")
        self.assertEqual(invalid.read_text(), "(let ((x 1))")
        checked = subprocess.run(command + ["--check", str(self.path)], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_modes_can_migrate_independently(self):
        before = "(struct Point (x : Int)) (fn (f) (let ((p (Point 1))) p))"
        self.assertEqual(self.migrate(before, records=False), "(struct Point (x : Int)) (fn (f) { (let p (Point 1)) p})")
        self.assertEqual(self.migrate(before, locals=False), "(struct Point (x : Int)) (fn (f) (let ((p (Point :x 1))) p))")

    def test_python_source_literal_round_trips_escaping_and_unicode(self):
        # Python's repr supplies an independent encoder; verify the runtime
        # literal rather than matching the tool's choice of escape spelling.
        axiom = '(fn (f) (let ((x "hello \\\"🌍\\\"")) x))\n'
        container = "source = " + repr(axiom) + "\n"
        changed = MIGRATION.migrate_python(container, self.root / "source.py", self.repository)
        import ast
        literal = ast.literal_eval(ast.parse(changed).body[0].value)
        self.assertEqual(literal, self.migrate(axiom))
        self.assertIn("🌍", literal)

    def test_python_raw_and_triple_literals_keep_their_style(self):
        container = 'source = r"""(fn (f) (let ((x "a\\nb")) x))\n"""\n'
        changed = MIGRATION.migrate_python(container, self.root / "source.py", self.repository)
        self.assertIn('r"""', changed)
        self.assertIn('(let x "a\\nb")', changed)
        self.assertEqual(MIGRATION.migrate_python(changed, self.root / "source.py", self.repository), changed)

    def test_python_unicode_separators_do_not_shift_source_offsets(self):
        import ast
        axiom = '(fn (f) (let ((x 1)) x))'
        for separator in ("\u2028", "\u0085", "\u2029"):
            for newline in ("\n", "\r\n"):
                with self.subTest(separator=repr(separator), newline=repr(newline)):
                    prefix = 'oracle = "before' + separator + 'after"' + newline
                    container = prefix + 'sentinel = 42; source = ' + repr(axiom) + newline
                    changed = MIGRATION.migrate_python(container, self.root / "source.py", self.repository)
                    self.assertTrue(changed.startswith(prefix + 'sentinel = 42; source = '))
                    tree = ast.parse(changed)
                    self.assertEqual(ast.literal_eval(tree.body[0].value), 'before' + separator + 'after')
                    self.assertEqual(ast.literal_eval(tree.body[1].value), 42)
                    self.assertEqual(ast.literal_eval(tree.body[2].value), self.migrate(axiom))
                    self.assertEqual(MIGRATION.migrate_python(changed, self.root / "source.py", self.repository), changed)

    def test_python_expected_and_computed_source_refuse(self):
        for container in ['expected_source = "(fn (f) (let ((x 1)) x))"\n',
                          'source = "(let ((x " + str(1) + ")) x)"\n',
                          'source = f"(let ((x {value})) x)"\n']:
            with self.subTest(container=container), self.assertRaises(MIGRATION.MigrationError):
                MIGRATION.migrate_python(container, self.root / "source.py", self.repository)

    def test_shell_literal_axiom_heredoc_and_other_text(self):
        container = '''#!/bin/sh
# cat >ignored.ax <<AX
cat >"$work/probe.ax" <<'AX'
(fn (f) (let ((x "$literal")) x))
AX
echo "keep me"
'''
        changed = MIGRATION.migrate_shell(container, self.root / "probe.sh", self.repository)
        self.assertIn('(fn (f) { (let x "$literal") x})', changed)
        self.assertIn('echo "keep me"', changed)
        self.assertIn('# cat >ignored.ax <<AX', changed)

    def test_shell_expanded_or_unclassified_heredoc_refuses(self):
        for container in ['cat >probe.ax <<AX\n(fn (f) (let ((x $value)) x))\nAX\n',
                          "cat >data.txt <<'AX'\n(fn (f) (let ((x 1)) x))\nAX\n"]:
            with self.subTest(container=container), self.assertRaises(MIGRATION.MigrationError):
                MIGRATION.migrate_shell(container, self.root / "probe.sh", self.repository)

    def test_web_code_templates_preserve_backticks_and_escape_interpolation(self):
        container = 'const sample = {code: `(fn (f) (let ((x "\\`🌍\\`")) x))`};\n'
        changed = MIGRATION.migrate_code_container(container, self.root / "samples.ts", self.repository)
        self.assertIn('(fn (f) { (let x "\\`🌍\\`") x})', changed)
        self.assertEqual(MIGRATION.migrate_code_container(changed, self.root / "samples.ts", self.repository), changed)
        with self.assertRaisesRegex(MIGRATION.MigrationError, "interpolated"):
            MIGRATION.migrate_code_container('const source = `(let ((x ${value})) x)`;', self.root / "samples.ts", self.repository)

    def test_web_expected_output_and_comments_are_not_blessed(self):
        container = '// code: `(let ((fake 1)) fake)`\nconst expected = `(let ((x 1)) x)`;'
        with self.assertRaisesRegex(MIGRATION.MigrationError, "regenerate expected output"):
            MIGRATION.migrate_code_container(container, self.root / "samples.ts", self.repository)

    def test_rust_raw_source_and_escaped_source_round_trip(self):
        for literal in ['r##"(fn (f) (let ((x 1)) x))"##', '"(fn (f) (let ((x 1)) x))\\n"']:
            container = "let source = " + literal + ";\n"
            changed = MIGRATION.migrate_code_container(container, self.root / "test.rs", self.repository, rust=True)
            self.assertIn("{ (let x 1) x}", changed)
            self.assertEqual(MIGRATION.migrate_code_container(changed, self.root / "test.rs", self.repository, rust=True), changed)
        with_chars = '''out.push('"'); /* nested /* comment */ end */
let source = r#"(fn (f) (let ((x 1)) x))"#;'''
        changed = MIGRATION.migrate_code_container(with_chars, self.root / "test.rs", self.repository, rust=True)
        self.assertIn("out.push('\"');", changed)
        self.assertIn("{ (let x 1) x}", changed)

    def test_rust_printer_fragments_require_generator_level_migration(self):
        with self.assertRaisesRegex(MIGRATION.MigrationError, "update the generator"):
            MIGRATION.migrate_code_container('out.push_str("(let (");', self.root / "print.rs", self.repository, rust=True)

    def test_changelog_history_is_never_migrated(self):
        with self.assertRaisesRegex(ValueError, "append-only"):
            MIGRATION.migrate_markdown("```scheme\n(let ((x 1)) x)\n```\n", self.root / "CHANGELOG.md", self.repository)

    @unittest.skipUnless(os.environ.get("AXIOM_ROUTE_B_BRIDGE"), "set AXIOM_ROUTE_B_BRIDGE for the IR equivalence check")
    def test_compiler_keeps_scoped_local_lowering_identical(self):
        before = '''(:: main Int)
(fn (main)
  (let ((mut n 40))
    (let ((n (+ n 1))) n)
    (set n (+ n 2))
    n))
'''
        command = [os.environ["AXIOM_ROUTE_B_BRIDGE"], "emit-llvm", str(self.path)]
        self.path.write_text(before)
        old = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(old.returncode, 0, old.stderr)
        self.path.write_text(self.migrate(before, records=False))
        new = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(new.returncode, 0, new.stderr)
        self.assertEqual(new.stdout, old.stdout)

    @unittest.skipUnless(os.environ.get("AXIOM_ROUTE_B_BRIDGE"), "set AXIOM_ROUTE_B_BRIDGE for import visibility probes")
    def test_compiler_agrees_on_transitive_import_visibility(self):
        self.module("Leaf", "(pub struct Point (leaf : Int)) (struct Secret (hidden : Int)) (pub fn (id x) x)")
        self.module("Mid", "(import Leaf) (pub fn (mid) 1)")
        self.module("Selected", "(import Leaf (id))")
        self.module("Left", "(import Leaf (Point))")
        self.module("Right", "(import Leaf)")
        self.module("Outer", "(import Leaf) (pub struct Point (own : Int))")
        self.module("A", "(import B) (pub struct First (a : Int))")
        self.module("B", "(import A) (pub struct Second (b : Int))")
        env = os.environ.copy()
        env["AXIOM_PATH"] = str(self.root / "stdlib")
        probes = [
            ("(import Mid) (fn (f) (Point 1))", None),
            ("(import Mid) (fn (f) (Leaf::Point 1))", None),
            ("(import Mid) (fn (f) (Mid::Point 1))", "AX3001"),
            ("(import Mid (mid)) (fn (f) (Point 1))", None),
            ("(import Mid ()) (fn (f) (Point 1))", None),
            ("(import Mid (Point)) (fn (f) (Point 1))", "AX3023"),
            ("(import Mid) (fn (f) (Secret 1))", "AX3001"),
            ("(import Selected) (fn (f) (Point 1))", "AX3001"),
            ("(import Selected) (fn (f) (Leaf::Point 1))", "AX3001"),
            ("(import Left) (import Right) (fn (f) (Point 1))", None),
            ("(import Leaf) (import Left) (fn (f) (Point 1))", None),
            ("(import A) (fn (f) (B::Second 1))", None),
            ("(import Outer) (fn (f) (Point 1))", "AX3044"),
            ("(import Outer) (fn (f) (Outer::Point 1))", None),
            ("(import Leaf) (struct Point (own : Int)) (fn (f) (Point 1))", None),
            ("(import Leaf) (fn (Point x) x) (fn (f) (Point 1))", None),
        ]
        command = [os.environ["AXIOM_ROUTE_B_BRIDGE"], "--diagnostic-format=ai", "check", str(self.path)]
        for source, error_code in probes:
            with self.subTest(source=source):
                self.path.write_text(source + "\n")
                old = subprocess.run(command, env=env, capture_output=True, text=True)
                self.assertEqual(old.returncode, 1 if error_code else 0, old.stdout + old.stderr)
                if error_code:
                    self.assertIn(error_code, old.stdout + old.stderr)
                else:
                    self.path.write_text(self.migrate(source) + "\n")
                    new = subprocess.run(command, env=env, capture_output=True, text=True)
                    self.assertEqual(new.returncode, 0, new.stdout + new.stderr)


if __name__ == "__main__":
    unittest.main()
