; Scopes and bindings, for the highlighters that colour a use by its
; binding (tree-sitter's own `highlight` command and Helix): a use of a
; parameter takes the parameter's colour. The language server's
; semantic tokens resolve names the same way, so this keeps those
; editors and VS Code alike on the uses of a name, not only on its
; binding. Neovim reads this file for navigation, not for colour.
;
; Not yet: a use of a top-level function or macro elsewhere in the
; file. A highlighter files a binding in the innermost scope where it
; is written, and a function's name is written inside the function, so
; only the function's own recursive uses see it; elsewhere a use keeps
; its syntactic colour (a call, or a variable), where VS Code's server
; resolves it. A macro parameter is not bound here either: the server
; colours its uses in a template by position, as the highlights do.

; ---------------------------------------------------------------
; Scopes
; ---------------------------------------------------------------

(source_file) @local.scope
(function_definition) @local.scope
(lambda) @local.scope
(block) @local.scope
(match_arm) @local.scope
(for_expression) @local.scope
(parallel_expression) @local.scope

; ---------------------------------------------------------------
; Bindings
; ---------------------------------------------------------------

; A function's own name, for its recursive uses.
(function_definition name: (identifier) @local.definition)

(function_definition parameter: (identifier) @local.definition)
(lambda parameter: (identifier) @local.definition)

; A binding's own value does not see it: in `{ (let ps ps) ps }` the
; initializer uses the `ps` from outside the block.
(local_binding
  name: (identifier) @local.definition
  value: (_) @local.definition-value)
(parallel_binding
  name: (identifier) @local.definition
  value: (_) @local.definition-value)
(for_expression binder: (identifier) @local.definition)

(match_arm pattern: (identifier) @local.definition)
(constructor_pattern argument: (identifier) @local.definition)
(field_pattern name: (identifier) @local.definition !pattern)
(field_pattern pattern: (identifier) @local.definition)
(parenthesized_pattern pattern: (identifier) @local.definition)
(tuple_pattern element: (identifier) @local.definition)
(list_pattern element: (identifier) @local.definition)

; ---------------------------------------------------------------
; Uses
; ---------------------------------------------------------------

(identifier) @local.reference
