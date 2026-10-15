#!/usr/bin/env python3
"""Review or apply the scoped-local and named-record source migration.

The reader retains source offsets: comments, literals and unaffected source
are copied byte for byte. It is a migration tool, not a formatter or compiler.
Constructor resolution uses declarations, imports and lexical bindings. An
uncertain constructor use aborts that file rather than guessing field names.

Usage:
  scripts/migrate-route-b.py --review self_host stdlib
  scripts/migrate-route-b.py --check --locals-only path/to/file.ax
  scripts/migrate-route-b.py --write -I module/directory path/to/file.ax

Directories select .ax and .axbad sources. Explicit Markdown files select
scheme/axiom fenced blocks; each block has its own declarations and imports.
Explicit Python, shell, Rust and JS/TS files select proven source literals or
literal .ax heredocs. Expected output and ambiguous generators are refused.
Malformed sources require manual migration, or --skip-invalid to leave the
whole affected file untouched. No mode writes unless --write is supplied.

For embedded source, callers can use migrate_source(text, path, repository).
Shell expansion, interpolated templates and printer fragments need explicit
generator changes. Macro-generated constructor applications remain intact.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
import difflib
from pathlib import Path
import re
import sys
import tokenize
import io
from typing import Iterable


@dataclass(frozen=True)
class Token:
    start: int
    end: int
    text: str
    kind: str = "atom"


@dataclass(frozen=True)
class Node:
    start: int
    end: int
    kind: str
    text: str = ""
    children: tuple[Node, ...] = ()

    @property
    def head(self) -> str:
        return self.children[0].text if self.children and self.children[0].kind == "atom" else ""


class MigrationError(Exception):
    def __init__(self, path: Path, source: str, offset: int, message: str):
        self.path = path
        self.offset = offset
        self.line = source.count("\n", 0, offset) + 1
        self.column = offset - source.rfind("\n", 0, offset)
        self.message = message
        super().__init__(f"{path}:{self.line}:{self.column}: {message}")


def tokens(source: str, path: Path) -> list[Token]:
    """Read Axiom tokens without dropping the source they surround."""
    result: list[Token] = []
    i = 0
    while i < len(source):
        start = i
        ch = source[i]
        if ch.isspace() or ch == "\ufeff":
            i += 1
        elif ch == ";":
            end = source.find("\n", i)
            i = len(source) if end < 0 else end
        elif source.startswith("#|", i):
            i += 2
            depth = 1
            while i < len(source) and depth:
                if source.startswith("#|", i):
                    depth += 1
                    i += 2
                elif source.startswith("|#", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            # The Axiom lexer also accepts a block comment through EOF.
        elif ch == '"':
            i += 1
            while i < len(source) and source[i] != '"':
                i += 2 if source[i] == "\\" else 1
            if i >= len(source):
                raise MigrationError(path, source, start, "unterminated string; source left unchanged")
            i += 1
            result.append(Token(start, i, source[start:i], "literal"))
        elif ch == "'":
            # A leading quote followed by space, ( or [ is quotation.
            if i + 1 == len(source) or source[i + 1] in " ([":
                i += 1
                result.append(Token(start, i, "'", "quote"))
            else:
                i += 1
                if source[i] == "\\":
                    i += 2
                else:
                    i += 1
                if i >= len(source) or source[i] != "'":
                    raise MigrationError(path, source, start, "malformed character literal; source left unchanged")
                i += 1
                result.append(Token(start, i, source[start:i], "literal"))
        elif ch in "(){}[]?":
            i += 1
            result.append(Token(start, i, ch, "delimiter" if ch != "?" else "postfix"))
        elif source.startswith("..", i):
            i += 3 if source.startswith("...", i) else 2
            result.append(Token(start, i, source[start:i]))
        else:
            i += 1
            while i < len(source):
                if source[i].isspace() or source[i] in "(){}[]?;\"" or source.startswith("#|", i) or source.startswith("..", i):
                    break
                i += 1
            result.append(Token(start, i, source[start:i]))
    return result


def read(source: str, path: Path) -> tuple[Node, ...]:
    stream = tokens(source, path)
    i = 0
    pairs = {"(": ")", "{": "}", "[": "]"}

    def expression() -> Node:
        nonlocal i
        if i >= len(stream):
            raise MigrationError(path, source, len(source), "missing quoted expression")
        token = stream[i]
        i += 1
        if token.text in pairs:
            children = []
            while i < len(stream) and stream[i].text != pairs[token.text]:
                if stream[i].text in ")}]":
                    raise MigrationError(path, source, stream[i].start, "mismatched closing delimiter; source left unchanged")
                children.append(expression())
            if i >= len(stream):
                raise MigrationError(path, source, token.start, "unclosed delimiter; source left unchanged")
            close = stream[i]
            i += 1
            node = Node(token.start, close.end, token.text, children=tuple(children))
        elif token.kind == "quote":
            child = expression()
            node = Node(token.start, child.end, "quote", children=(child,))
        elif token.text in ")}]" or token.kind == "postfix":
            raise MigrationError(path, source, token.start, "unexpected delimiter; source left unchanged")
        else:
            node = Node(token.start, token.end, token.kind, token.text)
        while i < len(stream) and stream[i].kind == "postfix":
            node = Node(node.start, stream[i].end, "postfix", children=(node,))
            i += 1
        return node

    roots = []
    try:
        while i < len(stream):
            roots.append(expression())
    except RecursionError:
        raise MigrationError(path, source, stream[min(i, len(stream) - 1)].start,
                             "source nesting exceeds the migration reader limit") from None
    return tuple(roots)


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    path: Path
    fields: tuple[str, ...] = ()
    public: bool = False
    sealed: bool = False
    expression_args: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Import:
    name: str
    selected: frozenset[str] | None
    offset: int


def declaration(node: Node) -> tuple[str, tuple[Node, ...], bool]:
    children = node.children
    public = node.kind == "(" and node.head == "pub"
    index = 1 if public else 0
    if node.kind != "(" or len(children) <= index or children[index].kind != "atom":
        return "", (), False
    return children[index].text, children[index + 1:], public


def names_in(node: Node) -> set[str]:
    if node.kind == "atom":
        return {node.text} if re.fullmatch(r"[^0-9\W][\w/:'!+*<>=.-]*", node.text) else set()
    return set().union(*(names_in(child) for child in node.children)) if node.children else set()


def macro_expression_args(parts: tuple[Node, ...]) -> frozenset[int]:
    """Prove which plain macro parameters are used only as expressions.

    A syntax/type/pattern use makes an argument opaque. Complex or repeated
    rule patterns remain opaque; they need an expander to resolve their roles.
    """
    clauses = []
    if parts[0].kind == "(":
        clauses.append((parts[0], parts[1:]))
    else:
        for clause in parts[1:]:
            if clause.kind == "(" and clause.head != "literals" and clause.children and clause.children[0].kind == "(":
                clauses.append((clause.children[0], clause.children[1:]))
    trusted = None
    for pattern, templates in clauses:
        params = pattern.children[1:]
        if any(p.kind != "atom" or p.text == "..." for p in params):
            return frozenset()
        roles = {p.text: set() for p in params}

        def opaque(node: Node) -> None:
            for name in names_in(node) & roles.keys():
                roles[name].add("syntax")

        def expression(node: Node) -> None:
            if node.kind == "atom":
                if node.text in roles:
                    roles[node.text].add("expression")
            elif node.kind == "quote":
                opaque(node)
            elif node.kind in {"{", "[", "postfix"}:
                for part in node.children:
                    expression(part)
            elif node.kind == "(" and node.children:
                bits = node.children
                head = node.head
                if head == "pub":
                    opaque(node)
                elif head in {"quote", "::", "fn", "macro", "struct", "data", "type", "subtype", "import", "extern", "effect", "syntax/for"}:
                    opaque(node)
                elif head.startswith("syntax/") and head not in {"syntax/format", "syntax/formatln"}:
                    opaque(node)
                elif head == "lambda" and len(bits) == 3:
                    opaque(bits[1])
                    expression(bits[2])
                elif head == "let" and len(bits) > 2 and bits[1].kind == "(":
                    for pair in bits[1].children:
                        if pair.kind != "(" or len(pair.children) < 2:
                            opaque(pair)
                        else:
                            for part in pair.children[:-1]:
                                opaque(part)
                            expression(pair.children[-1])
                    for part in bits[2:]:
                        expression(part)
                elif head == "match" and len(bits) > 2:
                    expression(bits[1])
                    for arm in bits[2:]:
                        if arm.kind == "(" and len(arm.children) > 1:
                            opaque(arm.children[0])
                            for part in arm.children[1:]:
                                expression(part)
                        else:
                            opaque(arm)
                elif head == "as" and len(bits) == 3:
                    expression(bits[1])
                    opaque(bits[2])
                elif head in {"for", "region", "parallel", "set", "asm", "handle"}:
                    opaque(node)
                else:
                    for part in bits:
                        expression(part)

        for template in templates:
            expression(template)
        indices = {i for i, param in enumerate(params) if roles[param.text] == {"expression"}}
        trusted = indices if trusted is None else trusted & indices
    return frozenset(trusted or ())


@dataclass
class Module:
    path: Path
    source: str
    roots: tuple[Node, ...]
    symbols: dict[str, list[Symbol]] = field(default_factory=dict)
    imports: list[Import] = field(default_factory=list)

    def add(self, symbol: Symbol) -> None:
        self.symbols.setdefault(symbol.name, []).append(symbol)

    def __post_init__(self) -> None:
        for node in self.roots:
            head, tail, public = declaration(node)
            if not tail:
                continue
            if head == "import" and tail[0].kind == "atom":
                selected = None
                if len(tail) > 1:
                    if tail[1].kind != "(" or any(n.kind != "atom" for n in tail[1].children):
                        raise MigrationError(self.path, self.source, tail[1].start, "cannot resolve malformed selective import")
                    selected = frozenset(n.text for n in tail[1].children)
                self.imports.append(Import(tail[0].text, selected, node.start))
            elif head == "struct" and tail[0].kind == "atom":
                fields = []
                markers = set()
                for part in tail[1:]:
                    if part.kind == "atom":
                        markers.add(part.text)
                        continue
                    if part.kind != "(":
                        raise MigrationError(self.path, self.source, part.start, "cannot resolve malformed struct declaration")
                    bits = part.children
                    at = 1 if bits and bits[0].text == "mut" else 0
                    if len(bits) > at + 1 and bits[at].kind == "atom" and bits[at + 1].text == ":":
                        fields.append(bits[at].text)
                    elif any(bit.text == ":" for bit in bits):
                        raise MigrationError(self.path, self.source, part.start, "cannot resolve malformed struct field")
                    # A group without a colon declares generic parameters.
                if len(set(fields)) != len(fields):
                    raise MigrationError(self.path, self.source, node.start, "struct has duplicate fields; source left unchanged")
                self.add(Symbol(tail[0].text, "struct", self.path, tuple(fields), public,
                                bool(markers & {"sealed", "word"})))
            elif head in {"fn", "macro"}:
                name = tail[0].text if tail[0].kind == "atom" else tail[0].head
                if name:
                    self.add(Symbol(name, "function" if head == "fn" else "macro", self.path, public=public,
                                    expression_args=macro_expression_args(tail) if head == "macro" else frozenset()))
            elif head == "data":
                for part in tail[1:]:
                    if part.kind == "(" and part.head and not part.head[0].islower():
                        self.add(Symbol(part.head, "data", self.path, public=public))
            elif head == "extern":
                for part in tail[1:]:
                    if part.kind == "(" and part.head == "::" and len(part.children) > 1:
                        self.add(Symbol(part.children[1].text, "function", self.path, public=public))


class Repository:
    """Resolve imports with entry-directory precedence and explicit search paths."""

    def __init__(self, root: Path, import_dirs: Iterable[Path] = ()):
        self.root = root.resolve()
        self.import_dirs = [Path(p).resolve() for p in import_dirs]
        self.cache: dict[Path, Module] = {}

    def module(self, path: Path) -> Module:
        path = path.resolve()
        if path not in self.cache:
            source = path.read_text(encoding="utf-8")
            self.cache[path] = Module(path, source, read(source, path))
        return self.cache[path]

    def resolve(self, name: str, entry: Path) -> tuple[list[Module], str | None]:
        if not name or any(part in {"", ".", ".."} for part in name.split(".")) or "/" in name or "\\" in name:
            return [], f"invalid module path {name!r}"
        relative = Path(*name.split("."))
        directories = [entry.parent, *self.import_dirs, self.root / "stdlib", self.root / "self_host"]
        for directory in dict.fromkeys(directories):
            path = directory / relative.with_suffix(".ax")
            if path.is_file():
                return [self.module(path)], None
            # Target-specific modules are safe only when their visible record
            # shapes and other names agree; resolving below checks each view.
            variants = sorted(path.parent.glob(path.stem + ".*.ax"))
            if variants:
                return [self.module(p) for p in variants], None
        return [], f"cannot find imported module {name!r}; supply -I for its directory"

    def imported_symbols(self, module: Module) -> tuple[dict[str, list[Symbol]], list[str]]:
        """Collect the compiler's transitive public visibility without guessing.

        An import selection filters declarations in that module only. Its own
        imports remain visible, and a qualified spelling names the defining
        module, not the module through which it was reached. Repeated paths
        through a diamond or cycle contribute the same defining symbol once.
        All lookups retain the entry file's search-root precedence.
        """
        imported: dict[str, list[Symbol]] = {}
        unresolved: list[str] = []
        pending = list(module.imports)
        visited: set[tuple[Path, str, frozenset[str] | None]] = set()
        expanded: set[Path] = set()
        at = 0
        while at < len(pending):
            item = pending[at]
            at += 1
            try:
                variants, error = self.resolve(item.name, module.path)
            except (MigrationError, OSError) as exc:
                variants, error = [], str(exc)
            if error:
                if error not in unresolved:
                    unresolved.append(error)
                continue
            for variant in variants:
                view = (variant.path, item.name, item.selected)
                if view in visited:
                    continue
                visited.add(view)
                if variant.path not in expanded:
                    expanded.add(variant.path)
                    pending.extend(variant.imports)
                for name, symbols in variant.symbols.items():
                    if item.selected is not None and name not in item.selected:
                        continue
                    for symbol in symbols:
                        if not symbol.public:
                            continue
                        spellings = {name, f"{item.name}::{name}", f"{item.name.replace('.', '::')}::{name}"}
                        for spelling in spellings:
                            entries = imported.setdefault(spelling, [])
                            if symbol not in entries:
                                entries.append(symbol)
        return imported, unresolved


class Migration:
    def __init__(self, module: Module, repository: Repository, *, locals: bool = True, records: bool = True):
        self.module = module
        self.repository = repository
        self.locals = locals
        self.records = records
        self.edits: list[tuple[int, int, str]] = []
        self.imported: dict[str, list[Symbol]] = {}
        self.unresolved: list[str] = []
        self.local_count = 0
        self.record_count = 0
        if records:
            self.imported, self.unresolved = repository.imported_symbols(module)

    def fail(self, node: Node, message: str) -> None:
        raise MigrationError(self.module.path, self.module.source, node.start, message)

    def edit(self, start: int, end: int, replacement: str) -> None:
        self.edits.append((start, end, replacement))

    def symbol(self, node: Node, bound: set[str]) -> Symbol | None:
        name = node.text
        if not name or name in bound:
            return None
        symbols = self.module.symbols.get(name, self.imported.get(name, []))
        if not any(s.kind == "struct" for s in symbols):
            return None
        # Identical target variants have the same meaning despite their paths.
        shapes = {(s.kind, s.fields, s.sealed) for s in symbols}
        identities = {s.path for s in symbols}
        if len(shapes) != 1 or (len(identities) > 1 and not self.same_variants(identities)):
            origins = ", ".join(str(s.path) for s in symbols)
            self.fail(node, f"ambiguous constructor {name!r} ({origins}); qualify or resolve it before migration")
        symbol = symbols[0]
        if symbol.path != self.module.path and symbol.sealed:
            self.fail(node, f"constructor {name!r} is private outside {symbol.path}; source left unchanged")
        if self.unresolved and name not in self.module.symbols:
            self.fail(node, f"cannot prove constructor {name!r} resolves uniquely: {'; '.join(self.unresolved)}")
        return symbol

    @staticmethod
    def same_variants(paths: set[Path]) -> bool:
        return len({(p.parent, p.name.split(".", 1)[0]) for p in paths}) == 1

    def sequence(self, nodes: Iterable[Node], bound: set[str]) -> None:
        scope = set(bound)
        for node in nodes:
            if node.kind == "(" and node.head == "let" and len(node.children) > 1 and node.children[1].kind == "atom":
                bits = node.children[1:]
                at = 1 if bits[0].text == "mut" else 0
                if len(bits) != at + 2 or bits[at].kind != "atom":
                    self.fail(node, "malformed local declaration; source left unchanged")
                self.expression(bits[at + 1], scope)
                scope.add(bits[at].text)
            else:
                self.expression(node, scope)

    def grouped_let(self, node: Node, bound: set[str]) -> None:
        bits = node.children
        if len(bits) < 3 or bits[1].kind != "(":
            self.fail(node, "grouped let needs a binding list and body; source left unchanged")
        scope = set(bound)
        for pair in bits[1].children:
            parts = pair.children
            at = 1 if parts and parts[0].text == "mut" else 0
            if pair.kind != "(" or len(parts) != at + 2 or parts[at].kind != "atom":
                self.fail(pair, "malformed grouped-let binding; source left unchanged")
            self.expression(parts[at + 1], scope)
            scope.add(parts[at].text)
            if self.locals:
                self.edit(pair.start + 1, pair.start + 1, "let ")
                self.local_count += 1
        self.sequence(bits[2:], scope)
        if self.locals:
            # Retain every intervening source slice, including comments on the
            # keyword, the binding list, each binding and the final body.
            self.edit(node.start, node.start + 1, "{")
            self.edit(bits[0].start, bits[0].end, "")
            self.edit(bits[1].start, bits[1].start + 1, "")
            self.edit(bits[1].end - 1, bits[1].end, "")
            self.edit(node.end - 1, node.end, "}")

    def pattern_names(self, node: Node) -> set[str]:
        if node.kind == "atom":
            if node.text in {"_", "true", "false", "..."} or not names_in(node):
                return set()
            symbols = self.module.symbols.get(node.text, self.imported.get(node.text, []))
            return set() if symbols else {node.text}
        if node.kind == "{":
            result: set[str] = set()
            parts = node.children
            for i, part in enumerate(parts[:-1]):
                if part.text == "=":
                    result.update(self.pattern_names(parts[i + 1]))
            return result
        if node.kind == "(":
            return set().union(*(self.pattern_names(n) for n in node.children[1:])) if len(node.children) > 1 else set()
        return set()

    def match_arms(self, arms: Iterable[Node], bound: set[str]) -> None:
        for arm in arms:
            if arm.kind == "(" and arm.head == "syntax/for":
                parts = arm.children
                if len(parts) < 3 or parts[1].kind != "(" or not parts[1].children:
                    self.fail(arm, "cannot resolve syntax/for match-arm template")
                binder = parts[1].children[0].text
                self.match_arms(parts[2:], bound | {binder})
            elif arm.kind == "(" and len(arm.children) >= 2:
                self.sequence(arm.children[1:], bound | self.pattern_names(arm.children[0]))
            elif arm.kind != "atom" or arm.text != "...":
                self.fail(arm, "malformed match arm; source left unchanged")

    def function(self, node: Node, bound: set[str]) -> None:
        _, parts, _ = declaration(node)
        if not parts or parts[0].kind != "(" or len(parts) < 2:
            self.fail(node, "cannot resolve function declaration")
        params = set().union(*(names_in(p) for p in parts[0].children[1:]))
        self.sequence(parts[1:], bound | params)

    def macro(self, node: Node, bound: set[str]) -> None:
        _, parts, _ = declaration(node)
        if len(parts) < 2:
            self.fail(node, "cannot resolve macro declaration")
        if parts[0].kind == "(":
            params = set().union(*(names_in(p) for p in parts[0].children[1:]))
            self.sequence(parts[1:], bound | params)
            return
        literals = set()
        for clause in parts[1:]:
            if clause.kind == "(" and clause.head == "literals":
                literals.update(n.text for n in clause.children[1:])
        for clause in parts[1:]:
            if clause.kind != "(" or clause.head == "literals":
                continue
            if not clause.children or clause.children[0].kind != "(":
                self.fail(clause, "cannot resolve macro rule template")
            params = names_in(clause.children[0]) - literals - {parts[0].text}
            for template in clause.children[1:]:
                self.top_level(template, bound | params)

    def top_level(self, node: Node, bound: set[str]) -> None:
        head, _, _ = declaration(node)
        if head == "fn":
            self.function(node, bound)
        elif head == "macro":
            self.macro(node, bound)
        elif head in {"::", "struct", "data", "type", "subtype", "import", "extern", "effect"}:
            return
        else:
            self.expression(node, bound)

    def expression(self, node: Node, bound: set[str]) -> None:
        if node.kind in {"literal", "quote"}:
            return
        if node.kind == "atom":
            if self.records:
                symbol = self.symbol(node, bound)
                if symbol and symbol.fields:
                    self.fail(node, f"constructor {node.text!r} is used as a value; write an explicit lambda with named fields before migration")
            return
        if node.kind == "postfix":
            self.expression(node.children[0], bound)
            return
        if node.kind == "{":
            self.sequence(node.children, bound)
            return
        if node.kind == "[":
            for child in node.children:
                self.expression(child, bound)
            return
        if not node.children:
            return
        parts = node.children
        head = node.head
        if head in {"quote", "::", "struct", "data", "type", "subtype", "import", "effect", "extern"}:
            return
        if head == "fn" or (head == "pub" and len(parts) > 1 and parts[1].text in {"fn", "macro", "::", "struct", "data", "type", "effect"}):
            self.top_level(node, bound)
            return
        if head == "macro":
            self.macro(node, bound)
            return
        if head == "let":
            self.grouped_let(node, bound)
            return
        if head == "lambda":
            if len(parts) != 3 or parts[1].kind != "(":
                self.fail(node, "cannot resolve lambda parameters")
            self.expression(parts[2], bound | names_in(parts[1]))
            return
        if head == "match":
            if len(parts) < 3:
                self.fail(node, "malformed match; source left unchanged")
            self.expression(parts[1], bound)
            self.match_arms(parts[2:], bound)
            return
        if head == "parallel":
            if len(parts) < 4 or parts[1].kind != "atom" or parts[2].kind != "(":
                self.fail(node, "cannot resolve parallel bindings")
            names = {parts[1].text}
            for pair in parts[2].children:
                if pair.kind != "(" or len(pair.children) != 2 or pair.children[0].kind != "atom":
                    self.fail(pair, "cannot resolve parallel binding")
                self.expression(pair.children[1], bound)
                names.add(pair.children[0].text)
            self.sequence(parts[3:], bound | names)
            return
        if head == "region":
            if len(parts) != 3 or parts[1].kind != "atom":
                self.fail(node, "cannot resolve region scope")
            self.expression(parts[2], bound | {parts[1].text})
            return
        if head == "for":
            if len(parts) < 5 or parts[2].text != "in":
                self.fail(node, "cannot resolve for scope")
            self.expression(parts[3], bound)
            at = 4
            if parts[at].text == "..":
                if len(parts) <= at + 2:
                    self.fail(node, "cannot resolve range-loop body")
                self.expression(parts[at + 1], bound)
                at += 2
                if parts[at].text == "by":
                    if len(parts) <= at + 2:
                        self.fail(node, "cannot resolve range-loop step")
                    self.expression(parts[at + 1], bound)
                    at += 2
            self.sequence(parts[at:], bound | names_in(parts[1]))
            return
        if head in {"as", "set"}:
            if len(parts) >= 3:
                self.expression(parts[1] if head == "as" else parts[2], bound)
            return
        if head == "handle":
            if len(parts) >= 2:
                self.expression(parts[1], bound)
            if len(parts) >= 4:
                self.expression(parts[3], bound)
            return
        if head == "syntax/for":
            if len(parts) < 3 or parts[1].kind != "(" or not parts[1].children:
                self.fail(node, "cannot resolve syntax/for template")
            scope = bound | {parts[1].children[0].text}
            for template in parts[2:]:
                self.top_level(template, scope)
            return
        if head.startswith("syntax/"):
            # These arguments describe syntax or types, not runtime values.
            # Refuse a hidden grouped let instead of silently leaving it old.
            if self.locals and any(n.kind == "(" and n.head == "let" for n in descendants(node)):
                self.fail(node, "grouped let inside a syntax intrinsic needs manual template migration")
            return

        declarations = self.module.symbols.get(head, self.imported.get(head, []))
        if head not in bound and declarations and all(s.kind == "macro" for s in declarations):
            expression_args = set.intersection(*(set(s.expression_args) for s in declarations))
            for index, argument in enumerate(parts[1:]):
                if index in expression_args:
                    self.expression(argument, bound)
                    continue
                # A bare struct name can be a macro's type argument. For a
                # compound opaque argument, never mistake patterns or quoted
                # syntax for a runtime record application.
                for candidate in (argument, *descendants(argument)):
                    if candidate.kind == "(" and candidate.head == "let" and self.locals:
                        self.fail(candidate, f"grouped let in an opaque argument to macro {head!r} needs manual migration")
                    if self.records and candidate.kind == "(" and candidate.children and candidate.children[0].kind == "atom":
                        constructor = self.symbol(candidate.children[0], bound)
                        if constructor and len(candidate.children) > 1 and not any(
                                part.kind == "atom" and part.text.startswith(":") for part in candidate.children[1:]):
                            self.fail(candidate, f"record syntax in an opaque argument to macro {head!r} needs manual migration")
            return

        symbol = self.symbol(parts[0], bound) if self.records and parts[0].kind == "atom" else None
        args = parts[1:]
        if symbol:
            if any(arg.kind == "atom" and arg.text.startswith(":") for arg in args):
                labels = []
                values = []
                at = 0
                while at < len(args):
                    label = args[at]
                    if label.kind != "atom" or not label.text.startswith(":"):
                        self.fail(label, f"named constructor {parts[0].text!r} mixes named and positional fields")
                    name = label.text[1:]
                    at += 1
                    if not name:
                        if at == len(args) or args[at].kind != "atom":
                            self.fail(label, "missing record field name after ':'")
                        name = args[at].text
                        at += 1
                    if at == len(args):
                        self.fail(label, f"named constructor {parts[0].text!r} has incomplete fields")
                    labels.append(name)
                    values.append(args[at])
                    at += 1
                if len(set(labels)) != len(labels) or set(labels) != set(symbol.fields):
                    self.fail(node, f"named constructor {parts[0].text!r} has unknown, missing or duplicate fields")
                args = tuple(values)
            else:
                if len(args) != len(symbol.fields):
                    self.fail(node, f"constructor {parts[0].text!r} needs {len(symbol.fields)} fields, found {len(args)}; source left unchanged")
                for name, arg in zip(symbol.fields, args):
                    self.edit(arg.start, arg.start, f":{name} ")
                if args:
                    self.record_count += 1
        elif parts[0].kind != "atom":
            self.expression(parts[0], bound)
        for arg in args:
            self.expression(arg, bound)

    def run(self) -> str:
        for root in self.module.roots:
            self.top_level(root, set())
        source = self.module.source
        pieces = []
        last = 0
        for start, end, replacement in sorted(self.edits):
            if start < last:
                raise AssertionError("overlapping migration edits")
            pieces.extend((source[last:start], replacement))
            last = end
        pieces.append(source[last:])
        return "".join(pieces)


def descendants(node: Node) -> Iterable[Node]:
    for child in node.children:
        yield child
        yield from descendants(child)


def migrate_source(source: str, path: Path, repository: Repository, *, locals: bool = True, records: bool = True) -> str:
    """Transform extracted Axiom source; no file is written by this API."""
    path = Path(path).resolve()
    module = Module(path, source, read(source, path))
    return Migration(module, repository, locals=locals, records=records).run()


def migrate_markdown(source: str, path: Path, repository: Repository, *, locals: bool = True, records: bool = True) -> str:
    """Migrate fenced code independently; prose and fence text stay intact."""
    if path.name == "CHANGELOG.md":
        raise ValueError(f"{path}: CHANGELOG.md is append-only; historical examples must remain unchanged")
    lines = source.splitlines(keepends=True)
    position = 0
    edits = []
    i = 0
    while i < len(lines):
        match = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*(scheme|axiom)(?:[ \t].*)?(?:\r?\n)?$", lines[i])
        if not match:
            position += len(lines[i])
            i += 1
            continue
        fence = match[1]
        opening = position + len(lines[i])
        i += 1
        position = opening
        end = i
        while end < len(lines) and not re.match(rf"^[ \t]{{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*(?:\r?\n)?$", lines[end]):
            end += 1
        if end == len(lines):
            raise MigrationError(path, source, opening, "unclosed Axiom code fence; source left unchanged")
        body = "".join(lines[i:end])
        # Padding maps a refusal back to its actual Markdown line.
        padding = "\n" * source.count("\n", 0, opening)
        changed = migrate_source(padding + body, path, repository, locals=locals, records=records)[len(padding):]
        edits.append((opening, opening + len(body), changed))
        position += len(body) + len(lines[end])
        i = end + 1
    for start, end, replacement in reversed(edits):
        source = source[:start] + replacement + source[end:]
    return source


def source_candidate(source: str) -> bool:
    """An audit signal only; this never authorises a rewrite by itself."""
    return bool(re.search(r"\(let\s*\(", source))


def apply_regions(source: str, regions: list[tuple[int, int, str]]) -> str:
    end = 0
    pieces = []
    for start, stop, replacement in sorted(regions):
        if start < end:
            raise ValueError("overlapping embedded-source regions")
        pieces.extend((source[end:start], replacement))
        end = stop
    pieces.append(source[end:])
    return "".join(pieces)


def embedded_source(value: str, path: Path, container: str, offset: int, repository: Repository, **options) -> str:
    try:
        return migrate_source(value, path, repository, **options)
    except MigrationError as exc:
        raise MigrationError(path, container, offset, f"embedded Axiom line {exc.line}: {exc.message}") from None


def source_role(name: str) -> bool:
    words = set(re.split(r"[^a-z]+", name.lower()))
    return not bool(words & {"expected", "golden", "output", "rendered"}) and bool(
        words & {"source", "sources", "src", "program", "programs", "code", "axiom"})


def python_literal(value: str, literal: str) -> str:
    match = re.match(r"([rRuU]*)(\"\"\"|'''|\"|')", literal)
    if not match:
        raise ValueError("unsupported Python source-literal prefix")
    prefix, quote = match.groups()
    if "r" in prefix.lower():
        if quote in value or (len(value) - len(value.rstrip("\\"))) % 2:
            raise ValueError("changed source cannot retain its raw Python delimiter")
        body = value
    else:
        body = value.replace("\\", "\\\\").replace(quote[0], "\\" + quote[0])
        body = body.replace("\r", "\\r").replace("\x00", "\\x00")
        if len(quote) == 1:
            body = body.replace("\n", "\\n").replace("\t", "\\t")
    replacement = prefix + quote + body + quote
    if ast.literal_eval(replacement) != value:
        raise ValueError("Python source-literal encoding did not round-trip")
    return replacement


def migrate_python(source: str, path: Path, repository: Repository, **options) -> str:
    """Migrate confirmed source literals; expected output and generators refuse."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise MigrationError(path, source, 0, f"cannot read Python container: {exc.msg}") from None
    # Python AST/tokenize line numbers follow LF, not Unicode separators
    # that are ordinary characters inside string literals and comments.
    lines = source.split("\n")
    starts = [0]
    for line in lines[:-1]:
        starts.append(starts[-1] + len(line) + 1)

    def location(line: int, column: int, utf8: bool = False) -> int:
        prefix = lines[line - 1].encode("utf-8")[:column].decode("utf-8") if utf8 else lines[line - 1][:column]
        return starts[line - 1] + len(prefix)

    string_tokens = {(location(t.start[0], t.start[1]), location(t.end[0], t.end[1])): t.string
                     for t in tokenize.generate_tokens(io.StringIO(source).readline) if t.type == tokenize.STRING}
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    regions = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        start = location(node.lineno, node.col_offset, True)
        end = location(node.end_lineno, node.end_col_offset, True)
        ancestry = []
        current = node
        while current in parents:
            current = parents[current]
            ancestry.append(current)
        confirmed = False
        for parent in ancestry:
            if isinstance(parent, (ast.Assign, ast.AnnAssign)):
                targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
                confirmed = any(source_role(n.id) for target in targets for n in ast.walk(target) if isinstance(n, ast.Name))
                break
            if isinstance(parent, ast.Dict):
                confirmed = any(value is node and isinstance(key, ast.Constant) and isinstance(key.value, str)
                                and source_role(key.value) for key, value in zip(parent.keys, parent.values))
                if confirmed:
                    break
            if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute) and parent.func.attr == "write_text":
                target = ast.get_source_segment(source, parent.func.value) or ""
                confirmed = bool(re.search(r"\.ax(?:bad)?[\"']", target))
                break
        if not confirmed:
            if source_candidate(node.value):
                raise MigrationError(path, source, start, "Axiom-like Python literal has no proven source role; update its generator or regenerate expected output")
            continue
        # Strings concatenated by Python and f-string fragments do not have
        # an independent source slice that can safely be written back.
        literal = string_tokens.get((start, end))
        if literal is None or any(isinstance(parent, (ast.JoinedStr, ast.BinOp)) for parent in ancestry[:1]):
            if source_candidate(node.value):
                raise MigrationError(path, source, start, "computed Python source needs generator-level migration")
            continue
        try:
            changed = embedded_source(node.value, path, source, start, repository, **options)
            if changed != node.value:
                regions.append((start, end, python_literal(changed, literal)))
        except ValueError as exc:
            raise MigrationError(path, source, start, str(exc)) from None
    return apply_regions(source, regions)


def migrate_shell(source: str, path: Path, repository: Repository, **options) -> str:
    """Migrate literal Axiom heredocs; shell expansion and generators refuse."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    regions = []
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("#"):
            i += 1
            continue
        match = re.search(r"<<(?P<tabs>-?)[ \t]*(?P<quote>['\"]?)(?P<name>[A-Za-z_][A-Za-z_0-9]*)(?P=quote)", lines[i])
        if not match:
            i += 1
            continue
        end = i + 1
        while end < len(lines) and lines[end].rstrip("\r\n").lstrip("\t" if match["tabs"] else "") != match["name"]:
            end += 1
        if end == len(lines):
            raise MigrationError(path, source, starts[i], "unclosed shell heredoc")
        body = "".join(lines[i + 1:end])
        command = lines[i]
        confirmed = bool(re.search(r"\.ax(?:bad)?(?:[^A-Za-z_0-9]|$)", command))
        if confirmed:
            if match["tabs"]:
                raise MigrationError(path, source, starts[i], "tab-stripped Axiom heredoc needs an explicit source-offset mapping")
            if not match["quote"] and any(ch in body for ch in "$`\\"):
                raise MigrationError(path, source, starts[i], "expanded Axiom heredoc needs generator-level migration")
            changed = embedded_source(body, path, source, starts[i + 1], repository, **options)
            if changed != body:
                regions.append((starts[i + 1], starts[end], changed))
        elif source_candidate(body):
            raise MigrationError(path, source, starts[i], "heredoc contains Axiom-like source without a proven .ax destination; update the generator explicitly")
        i = end + 1
    return apply_regions(source, regions)


def code_literals(source: str, path: Path, *, rust: bool = False) -> Iterable[tuple[int, int, str, str]]:
    """Locate JS template/string or Rust raw/string literals outside comments."""
    i = 0
    while i < len(source):
        if source.startswith("//", i):
            end = source.find("\n", i)
            i = len(source) if end < 0 else end + 1
        elif source.startswith("/*", i):
            depth = 1
            i += 2
            while i < len(source) and depth:
                if rust and source.startswith("/*", i):
                    depth += 1
                    i += 2
                elif source.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
        else:
            start = i
            if rust and source[i] == "'":
                character = re.match(r"'(?:\\(?:u\{[0-9A-Fa-f_]+\}|x[0-9A-Fa-f]{2}|.)|[^'\\\r\n])'", source[i:])
                if character:
                    i += len(character[0])
                    continue
            raw = re.match(r'r(#+)?"', source[i:]) if rust and (i == 0 or not source[i - 1].isalnum()) else None
            if raw:
                close = '"' + (raw[1] or "")
                body_start = i + len(raw[0])
                end = source.find(close, body_start)
                if end < 0:
                    raise MigrationError(path, source, i, "unclosed Rust raw string")
                i = end + len(close)
                yield start, i, source[body_start:end], "rust-raw"
            elif (rust and source[i] == '"') or (not rust and source[i] in ('"', "'", "`")):
                quote = source[i]
                i += 1
                while i < len(source) and source[i] != quote:
                    i += 2 if source[i] == "\\" else 1
                if i >= len(source):
                    raise MigrationError(path, source, start, "unclosed container string literal")
                i += 1
                yield start, i, source[start + 1:i - 1], "rust-string" if rust else quote
            else:
                i += 1


def decode_code_string(body: str, *, rust: bool = False) -> str:
    result = []
    i = 0
    escapes = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "b": "\b", "f": "\f", "v": "\v",
               "\\": "\\", '"': '"', "'": "'", "`": "`", "$": "$"}
    while i < len(body):
        if body[i] != "\\":
            if not rust and body.startswith("${", i):
                raise ValueError("interpolated source template needs generator-level migration")
            result.append(body[i])
            i += 1
            continue
        i += 1
        if i == len(body):
            raise ValueError("incomplete container string escape")
        ch = body[i]
        if ch in escapes:
            result.append(escapes[ch])
            i += 1
        elif ch in "\r\n":
            i += 1
            if ch == "\r" and i < len(body) and body[i] == "\n":
                i += 1
            if rust:
                while i < len(body) and body[i].isspace():
                    i += 1
        elif ch == "x" and re.match(r"[0-9a-fA-F]{2}", body[i + 1:i + 3]):
            result.append(chr(int(body[i + 1:i + 3], 16)))
            i += 3
        elif ch == "u":
            match = re.match(r"u\{([0-9a-fA-F_]+)\}", body[i:])
            if match:
                result.append(chr(int(match[1].replace("_", ""), 16)))
                i += len(match[0])
            elif not rust and re.match(r"[0-9a-fA-F]{4}", body[i + 1:i + 5]):
                result.append(chr(int(body[i + 1:i + 5], 16)))
                i += 5
            else:
                raise ValueError("unsupported Unicode container escape")
        elif not rust:
            result.append(ch)
            i += 1
        else:
            raise ValueError(f"unsupported Rust container escape \\{ch}")
    value = "".join(result)
    return value if rust else value.encode("utf-16-le", errors="surrogatepass").decode("utf-16-le")


def encode_code_string(value: str, quote: str) -> str:
    body = value.replace("\\", "\\\\").replace(quote, "\\" + quote).replace("\r", "\\r").replace("\0", "\\0")
    if quote == "`":
        body = body.replace("${", "\\${")
    else:
        body = body.replace("\n", "\\n").replace("\t", "\\t")
    return quote + body + quote


def migrate_code_container(source: str, path: Path, repository: Repository, *, rust: bool = False, **options) -> str:
    """Migrate source-valued literals, preserving all expected-output literals."""
    regions = []
    for start, end, body, kind in code_literals(source, path, rust=rust):
        prefix = source[max(0, start - 180):start]
        confirmed = bool(re.search(r"(?:\b(?:source|src|program|code|axiom)\s*[:=]|\b(?:SOURCE|PROGRAM|CODE)\s*=)\s*$", prefix))
        try:
            value = body if kind == "rust-raw" else decode_code_string(body, rust=rust)
        except ValueError as exc:
            if confirmed and source_candidate(body):
                raise MigrationError(path, source, start, str(exc)) from None
            continue
        if not confirmed:
            if source_candidate(value):
                raise MigrationError(path, source, start, "Axiom-like literal has no proven source role; update the generator or regenerate expected output")
            continue
        changed = embedded_source(value, path, source, start, repository, **options)
        if changed != value:
            if kind == "rust-raw":
                marker = re.match(r'r(#+)?"', source[start:end])[0]
                close = '"' + marker[1:-1]
                if close in changed:
                    raise MigrationError(path, source, start, "changed source collides with Rust raw-string delimiter")
                replacement = marker + changed + close
            else:
                replacement = encode_code_string(changed, '"' if rust else kind)
                decoded = decode_code_string(replacement[1:-1], rust=rust)
                if decoded != changed:
                    raise MigrationError(path, source, start, "container source encoding did not round-trip")
            regions.append((start, end, replacement))
    return apply_regions(source, regions)


def selected_files(paths: Iterable[str]) -> list[Path]:
    result = set()
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            result.update(p for p in path.rglob("*") if p.is_file() and p.suffix in {".ax", ".axbad"})
        elif path.is_file():
            result.add(path)
        else:
            raise ValueError(f"not a source file or directory: {path}")
    return sorted(p.resolve() for p in result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--review", "--dry-run", action="store_true", help="show a unified diff (the default)")
    modes.add_argument("--check", action="store_true", help="exit 1 when migration is needed; do not print a diff")
    modes.add_argument("--write", action="store_true", help="write changes after every selected file passes preflight")
    kinds = parser.add_mutually_exclusive_group()
    kinds.add_argument("--locals-only", action="store_true", help="migrate grouped lets without resolving constructors")
    kinds.add_argument("--records-only", action="store_true", help="migrate struct calls without changing grouped lets")
    parser.add_argument("--skip-invalid", action="store_true", help="leave refused files unchanged and continue; still exit 2")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root for standard module search")
    parser.add_argument("-I", "--import-dir", type=Path, action="append", default=[], help="additional module search directory")
    parser.add_argument("paths", nargs="+", help="Axiom files/directories or explicit Markdown files")
    args = parser.parse_args(argv)
    repository = Repository(args.root, args.import_dir)
    changes = []
    errors = []
    try:
        paths = selected_files(args.paths)
    except ValueError as exc:
        parser.error(str(exc))
    for path in paths:
        try:
            # newline='' keeps CRLF fixtures unchanged outside actual edits.
            with path.open(encoding="utf-8", newline="") as file:
                before = file.read()
            operations = {".md": migrate_markdown, ".mdx": migrate_markdown, ".py": migrate_python,
                          ".sh": migrate_shell, ".ts": migrate_code_container, ".js": migrate_code_container,
                          ".mjs": migrate_code_container, ".rs": lambda *a, **kw: migrate_code_container(*a, rust=True, **kw),
                          ".ax": migrate_source, ".axbad": migrate_source}
            operation = operations.get(path.suffix)
            if operation is None:
                raise ValueError(f"{path}: extract embedded Axiom and use migrate_source(); unsupported container {path.suffix!r}")
            after = operation(before, path, repository, locals=not args.records_only, records=not args.locals_only)
            if after != before:
                changes.append((path, before, after))
        except (MigrationError, OSError, UnicodeError, ValueError) as exc:
            errors.append(str(exc))
    for error in errors:
        print(error, file=sys.stderr)
    if errors and not args.skip_invalid:
        print("Migration aborted: no files written. Resolve the refusals, or use --skip-invalid to leave refused files unchanged.", file=sys.stderr)
        return 2
    if args.write:
        for path, before, _ in changes:
            with path.open(encoding="utf-8", newline="") as file:
                if file.read() != before:
                    print(f"{path}: source changed during preflight; no files written", file=sys.stderr)
                    return 2
    for path, before, after in changes:
        if args.write:
            with path.open("w", encoding="utf-8", newline="") as file:
                file.write(after)
        elif not args.check:
            sys.stdout.writelines(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                                     fromfile=str(path), tofile=str(path)))
        else:
            print(f"migration needed: {path}")
    print(f"{len(changes)} file(s) {'written' if args.write else 'need migration'}; {len(errors)} refusal(s)", file=sys.stderr)
    return 2 if errors else 1 if args.check and changes else 0


if __name__ == "__main__":
    sys.exit(main())
