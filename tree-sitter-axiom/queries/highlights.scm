; Syntax highlighting for Axiom.
;
; The roles are VS Code's. In VS Code the language server colours each
; token (its semantic tokens, `SECTION HL` in `self_host/lsp.ax`) over
; the TextMate grammar in `editors/vscode/syntaxes/`, and this file
; gives every token the role VS Code gives it: one keyword colour, an
; operator wherever a word is spelled only with operator characters,
; a constructor for a capitalised name in an expression, the library's
; colour for the lexicon's builtin types and effects.
; `scripts/check-highlight-parity.sh` holds the three together, token
; by token, over the repository's own sources.
;
; Capture names follow the convention Neovim, Helix and the tree-sitter
; CLI share. Those consumers disagree about which of two patterns wins
; on the same node: Neovim and tree-sitter's own highlighter take the
; last, Helix the first. So no node here is captured twice. Each
; identifier position has its own pattern, and the predicates split a
; position by spelling rather than by order:
;
;   operator   only `+ - * / % < > = ! & | ^ ~`, or `...`, in any position
;   `_`        the wildcard, in any position
;   syntax/    a macro-template word, in any position
;   upper      starts with a capital: a constructor in an expression
;   lower      everything else
;
; The gate checks that no node is captured twice over the whole corpus.
;
; `locals.scm` colours a use of a name by its binding, as the language
; server does: a parameter's uses as parameters, a function's uses as
; functions. Editors that do not read it for colour (Neovim) show those
; uses as variables.

; ---------------------------------------------------------------
; Comments and metadata
; ---------------------------------------------------------------

(comment) @comment
(block_comment) @comment

; A `;;` doc comment is what hover and `axiom doc` show.
(doc_comment) @comment.documentation

; `;@axiom:effect(io)` is a claim the compiler checks, not a note.
(axtag) @attribute

; ---------------------------------------------------------------
; Literals
; ---------------------------------------------------------------

(integer_literal) @number
(float_literal) @number.float
(boolean_literal) @boolean
; A character is a string to the language server, as in VS Code.
(character_literal) @string
(string_literal) @string

; ---------------------------------------------------------------
; Words by spelling, in any position
; ---------------------------------------------------------------

((identifier) @operator
  (#match? @operator "^([-+*/%<>=!&|^~]+|[.][.][.])$"))

((identifier) @variable.builtin
  (#eq? @variable.builtin "_"))
(wildcard_pattern) @variable.builtin

((identifier) @keyword
  (#match? @keyword "^syntax/"))

; ---------------------------------------------------------------
; Types
; ---------------------------------------------------------------

; The lexicon's builtin types are the library's; the grammar's other
; builtin words (`I64`, `Double`, ...) are ordinary type names to the
; compiler.
((builtin_type) @type.builtin
  (#match? @type.builtin "^(Int|Float|Bool|Char|String)$"))
((builtin_type) @type
  (#not-match? @type "^(Int|Float|Bool|Char|String)$"))
(unit_type) @type.builtin

((type_constructor (constructor_identifier) @type.builtin)
  (#match? @type.builtin "^(Int|Float|Bool|Char|String|Foreign|Handle|Vec|Option|Result|Addr)$"))
((type_constructor (constructor_identifier) @type)
  (#not-match? @type "^(Int|Float|Bool|Char|String|Foreign|Handle|Vec|Option|Result|Addr)$"))

(type_variable) @type.parameter
; `(data Tree a ...)`: one parameter, written without its parens.
((type_parameters) @type.parameter
  (#match? @type.parameter "^[a-z]"))
(struct_declaration marker: (identifier) @type.parameter)

(qualified_type module: (_) @module)
(qualified_type submodule: (_) @module)
((qualified_type name: (constructor_identifier) @type.builtin)
  (#match? @type.builtin "^(Int|Float|Bool|Char|String|Foreign|Handle|Vec|Option|Result|Addr)$"))
((qualified_type name: (constructor_identifier) @type)
  (#not-match? @type "^(Int|Float|Bool|Char|String|Foreign|Handle|Vec|Option|Result|Addr)$"))

; Effects take the type colours, as VS Code paints them: a builtin
; effect the library's, a declared one a type's.
(builtin_effect) @type.builtin
((effect (identifier) @type.builtin)
  (#match? @type.builtin "^(IO|Alloc|Mut|Unsafe|Spawn|Block|Entropy|Div|Pure)$"))
((effect (identifier) @type)
  (#not-match? @type "^(IO|Alloc|Mut|Unsafe|Spawn|Block|Entropy|Div|Pure|[-+*/%<>=!&|^~]+|[.][.][.]|_|syntax/.*)$"))


; ---------------------------------------------------------------
; Declarations
; ---------------------------------------------------------------

((function_definition name: (identifier) @function)
  (#not-match? @function "^(_|syntax/.*|[.][.][.]|[-+*/%<>=!&|^~]+)$"))
((type_signature subject: (identifier) @function)
  (#not-match? @function "^(_|syntax/.*|[.][.][.]|[-+*/%<>=!&|^~]+)$"))
(extern_item name: (identifier) @function)
(effect_operation name: (identifier) @function)

((import name: (identifier) @type)
  (#match? @type "^[A-Z]"))
((import name: (identifier) @function)
  (#not-match? @function "^([A-Z]|_$|syntax/|[.][.][.]$|[-+*/%<>=!&|^~]+$)"))
(import module: (module_path) @module)

([
  (macro_declaration name: (identifier) @function.macro)
  (macro_rule rule_name: (identifier) @function.macro)
]
  (#not-match? @function.macro "^(_|syntax/.*|[.][.][.]|[-+*/%<>=!&|^~]+)$"))

([
  (function_definition parameter: (identifier) @variable.parameter)
  (lambda parameter: (identifier) @variable.parameter)
  (macro_declaration parameter: (identifier) @variable.parameter)
  (macro_rule parameter: (identifier) @variable.parameter)
  (macro_pattern_form (identifier) @variable.parameter)
]
  (#not-match? @variable.parameter "^(_|syntax/.*|[.][.][.]|[-+*/%<>=!&|^~]+)$"))

([
  (data_declaration name: (identifier) @type)
  (struct_declaration name: (identifier) @type)
  (type_alias name: (identifier) @type)
  (subtype_declaration name: (identifier) @type)
  (effect_declaration name: (identifier) @type)
]
  (#not-match? @type "^(_|syntax/.*|[.][.][.]|[-+*/%<>=!&|^~]+)$"))

(data_constructor name: (constructor_identifier) @constructor)
((data_constructor name: (identifier) @constructor)
  (#not-match? @constructor "^(_|syntax/.*|[.][.][.]|[-+*/%<>=!&|^~]+)$"))
(constructor_pattern constructor: (constructor_identifier) @constructor)
(syntax_binders_pattern constructor: (identifier) @constructor)

([
  (field_declaration name: (identifier) @variable.member)
  (named_field name: (identifier) @variable.member)
  (extern_clause key: (identifier) @variable.member)
]
  (#not-match? @variable.member "^(_|syntax/.*|[.][.][.]|[-+*/%<>=!&|^~]+)$"))

(region_expression name: (identifier) @label)
(parallel_expression region: (identifier) @label)

(qualified_identifier module: (identifier) @module)
(dotted_module object: (identifier) @module)
(dotted_module field: (field_identifier) @module)
(qualified_identifier name: (identifier) @function.call)

; `p.x`: the field is a field, and `p` is coloured as any expression.
(field_expression field: (field_identifier) @variable.member)
(constructor_field name: (field_identifier) @variable.member)
(struct_construction constructor: (identifier) @constructor)

; ---------------------------------------------------------------
; Bindings and patterns
;
; A binder is a variable whatever its case: a bare `None` in a match
; arm binds a name, as the compiler reads it.
; ---------------------------------------------------------------

([
  (local_binding name: (identifier) @variable)
  (parallel_binding name: (identifier) @variable)
  (for_expression binder: (identifier) @variable)
  (match_arm pattern: (identifier) @variable)
  (constructor_pattern argument: (identifier) @variable)
  (field_pattern name: (identifier) @variable)
  (field_pattern pattern: (identifier) @variable)
  (parenthesized_pattern pattern: (identifier) @variable)
  (tuple_pattern element: (identifier) @variable)
  (list_pattern element: (identifier) @variable)
  (syntax_binders_pattern prefix: (identifier) @variable)
  (syntax_for_arm binder: (identifier) @variable)
  (syntax_for_declaration binder: (identifier) @variable)
]
  (#not-match? @variable "^(_|syntax/.*|[.][.][.]|[-+*/%<>=!&|^~]+)$"))

; ---------------------------------------------------------------
; Expressions
;
; The head of an application is called: a function, or a constructor
; when it is capitalised. An identifier anywhere else an expression
; stands is a variable, or a constructor when capitalised.
; ---------------------------------------------------------------

((application . (identifier) @constructor)
  (#match? @constructor "^[A-Z]"))
((application . (identifier) @function.call)
  (#not-match? @function.call "^([A-Z]|_$|syntax/|[.][.][.]$|[-+*/%<>=!&|^~]+$)"))

([
  (application . (_) (identifier) @constructor)
  (block body: (identifier) @constructor)
  (as_expression operand: (identifier) @constructor)
  (function_definition body: (identifier) @constructor)
  (if_expression operand: (identifier) @constructor)
  (for_expression start: (identifier) @constructor)
  (for_expression end: (identifier) @constructor)
  (for_expression step: (identifier) @constructor)
  (for_expression operand: (identifier) @constructor)
  (for_expression body: (identifier) @constructor)
  (handle_expression body: (identifier) @constructor)
  (handle_expression handler: (identifier) @constructor)
  (lambda body: (identifier) @constructor)
  (local_binding value: (identifier) @constructor)
  (constructor_field value: (identifier) @constructor)
  (list_literal element: (identifier) @constructor)
  (macro_declaration body: (identifier) @constructor)
  (macro_rule template: (identifier) @constructor)
  (match_arm body: (identifier) @constructor)
  (match_expression scrutinee: (identifier) @constructor)
  (parallel_binding value: (identifier) @constructor)
  (parallel_expression body: (identifier) @constructor)
  (region_expression body: (identifier) @constructor)
  (set_expression target: (identifier) @constructor)
  (set_expression value: (identifier) @constructor)
  (subtype_declaration lower: (identifier) @constructor)
  (subtype_declaration upper: (identifier) @constructor)
  (while_expression condition: (identifier) @constructor)
  (while_expression body: (identifier) @constructor)
  (literals_clause literal: (identifier) @constructor)
  (removed_form (identifier) @constructor)
  (extern_clause . (_) (identifier) @constructor)
  (field_expression object: (identifier) @constructor)
  (propagate_expression operand: (identifier) @constructor)
  (syntax_join_name left: (identifier) @constructor)
  (syntax_join_name right: (identifier) @constructor)
]
  (#match? @constructor "^[A-Z]"))

([
  (application . (_) (identifier) @variable)
  (block body: (identifier) @variable)
  (as_expression operand: (identifier) @variable)
  (function_definition body: (identifier) @variable)
  (if_expression operand: (identifier) @variable)
  (for_expression start: (identifier) @variable)
  (for_expression end: (identifier) @variable)
  (for_expression step: (identifier) @variable)
  (for_expression operand: (identifier) @variable)
  (for_expression body: (identifier) @variable)
  (handle_expression body: (identifier) @variable)
  (handle_expression handler: (identifier) @variable)
  (lambda body: (identifier) @variable)
  (local_binding value: (identifier) @variable)
  (constructor_field value: (identifier) @variable)
  (list_literal element: (identifier) @variable)
  (macro_declaration body: (identifier) @variable)
  (macro_rule template: (identifier) @variable)
  (match_arm body: (identifier) @variable)
  (match_expression scrutinee: (identifier) @variable)
  (parallel_binding value: (identifier) @variable)
  (parallel_expression body: (identifier) @variable)
  (region_expression body: (identifier) @variable)
  (set_expression target: (identifier) @variable)
  (set_expression value: (identifier) @variable)
  (subtype_declaration lower: (identifier) @variable)
  (subtype_declaration upper: (identifier) @variable)
  (while_expression condition: (identifier) @variable)
  (while_expression body: (identifier) @variable)
  (literals_clause literal: (identifier) @variable)
  (removed_form (identifier) @variable)
  (extern_clause . (_) (identifier) @variable)
  (field_expression object: (identifier) @variable)
  (propagate_expression operand: (identifier) @variable)
  (syntax_join_name left: (identifier) @variable)
  (syntax_join_name right: (identifier) @variable)
]
  (#not-match? @variable "^([A-Z]|_$|syntax/|[.][.][.]$|[-+*/%<>=!&|^~]+$)"))

; ---------------------------------------------------------------
; Keywords
;
; One colour, as VS Code has one: the language server sends `keyword`
; for every head keyword and for the words inside a form (`mut`, `in`,
; `by`, `is`, `range`, `literals`).
; ---------------------------------------------------------------

[
  "fn"
  "lambda"
  "macro"
  "data"
  "struct"
  "type"
  "subtype"
  "effect"
  "if"
  "match"
  "while"
  "for"
  "in"
  "by"
  "let"
  "set"
  "handle"
  "pub"
  "import"
  "extern"
  "region"
  "parallel"
  "literals"
  "is"
  "range"
  "sizeof"
  "alignof"
  "as"
  "mut"
  "syntax/for"
  "syntax/binders"
  "syntax/join"
] @keyword

; `union`, `foreign`, `trait`, `impl` and `cond` parse, so an editor
; can mark the dead word rather than show an error whose extent depends
; on where recovery landed; the compiler refuses them (`AX2004`).
(removed_keyword) @error

; ---------------------------------------------------------------
; Operators and punctuation
; ---------------------------------------------------------------

[
  "::"
  "->"
  "="
  ".."
  "..-"
] @operator
(ellipsis) @operator
(propagate_expression "?" @operator)

[
  ":"
  "!"
  "*"
  ","
  "."
] @punctuation.delimiter

[
  "("
  ")"
  "["
  "]"
  "{"
  "}"
] @punctuation.bracket

(pipe_expression "|>" @operator)
(pipe_expression stage: (identifier) @function)
