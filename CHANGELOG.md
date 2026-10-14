# Changelog

Notable changes to Axiom, newest first.

This file starts at `0.2.0` because `0.2.0` is the first tag. The tree
said `0.1.0` from its first commit and nothing ever tagged it, so there
is no `0.1.x` to compare against and no `0.2.0-rc` that preceded this.
Rather than invent a history, the entry below states what `0.2.0` *is*
— and, in the same detail, what it is not.

Every claim here carries the gate that holds it. A claim without one is
a comment, which is this project's rule everywhere else and applies to
its changelog too.

---

## Unreleased

- Four defects a read-only audit found, fixed. `((vecGet v 0) 41)`
  passed 41 to `vecGet` and printed an address: a call that supplies
  more arguments than an imported callee declares now applies the rest
  to the closure it answers, as a local callee's did. `(strAlloc -1)`
  answered a `String` of length -1 and `(mapWithCapacity 2^61+1)` hung;
  the first now stops with status 77 and the second with 70. A
  `vecSortBy` comparator that grew the vector let the sort scramble a
  block the vector had given back; the sort now stops with 77. And a
  region value stored into a vector built with a bare `vecNew` was
  never checked; it is `AX3060` now, in a `region` and in a recovery
  point. Tested by `tests/stdlib/848-over-apply-imported.ax` and
  `tests/stdlib/849-size-refusals.ax`.

- Tree-sitter colours Axiom the way VS Code does. The queries now give
  each token the role the language server and the TextMate grammar give
  it: one keyword colour, operators wherever they stand, constructors
  for capitalised names in expressions, library colours for builtin
  types and effects, and a parameter's uses coloured as the parameter
  through a new `locals.scm`. No node is captured twice, so Neovim and
  Helix agree; before, Neovim and the tree-sitter CLI let the catch-all
  `@variable` override every function, call and field name. The grammar
  splits field access (`p.x`) out of the identifier, reads `(a e)` after
  a `data` name as its type parameters and a `handle`'s list as its
  effects, as the compiler does. In VS Code, `true` and `false` keep the
  constant colour when the server attaches, an immutable binding keeps
  the variable colour, and the server no longer colours `for` in
  `(let ((for 5)) ...)` as a keyword. Tested by
  `scripts/check-highlight-parity.sh`.

- Release what `vecPop` hands over. A popped element's share was
  transferred to the caller and never given back, about 78 bytes per
  pop measured on a string. A trusted function now says
  `;@axiom:result(owned)` when its type-variable result carries a
  share, and the caller releases it at a `let`'s scope end, after a
  call, or when it is discarded. The claim is accepted only beside
  `effect(unsafe)`; anything else is `AX3116`. Tested by
  `tests/stdlib/846-vec-pop-release.ax`,
  `tests/diagnostics/1259-result-claim.ax` and
  `scripts/check-container-reclaim.sh`.
