# Axiom 1.0 roadmap

This is the one plan for Axiom 1.0: what 1.0 means, what is done and
what is left. [What's ready today](status.md) describes the current
release, and the [assurance page](assurance.md) maps each guarantee to
its evidence. When work lands, its row here changes in the same commit.

## What 1.0 means

Axiom 1.0 is a release you can build production software on with the
confidence you would have in Rust or Ada.

- **Safe code is memory-safe and resource-safe.** Code without
  `;@axiom:effect(unsafe)` can't free a value twice, use one after it
  is freed, forge a reference, or leak an owner it dropped. Files,
  sockets and other resources close themselves.
- **Every check means what it says.** A claim the compiler can't back
  is an error. A warning stays only for a stated limit of the analysis,
  with a way to make the code answerable.
- **There's one way to do each thing.** The standard library has one
  spelling per operation, and no legacy form is accepted.
- **The surface is stable.** At 1.0 the compatibility rules become
  enforceable (`COMPAT-6`), and every break before then has a row in
  `compat/BREAKING`.
- **Trunk is green** on linux-aarch64 and darwin-aarch64, with a
  reproducible toolchain record.

Qualification against a safety standard is not part of 1.0. The
[assurance page](assurance.md#gaps-to-qualification) lists what an
integrator would still need.

## Where things stand

| Workstream | State |
|---|---|
| [Memory and resource safety](#memory-and-resource-safety) | In progress |
| [Checks and claims](#checks-and-claims) | In progress |
| [One way to do each thing](#one-way-to-do-each-thing) | In progress |
| [Standard library](#standard-library) | In progress |
| [Concurrency and bounded execution](#concurrency-and-bounded-execution) | In progress |
| [Tools](#tools) | In progress |
| [Platforms and release](#platforms-and-release) | In progress |

## Memory and resource safety

| Item | State | Where it's specified or tested |
|---|---|---|
| A `mut` slot owns what it holds, and releases what `set` overwrites | Done | `MM-MUT-1`, `tests/stdlib/708-mut-slot-reassign.ax` |
| A heap value returned where the signature says `Int` is refused | Done | `MM-VAL-24`, `tests/diagnostics/1240-record-through-int.ax` |
| Identity and hashing of a heap value use the typed `Addr`, not a cast | Done | `tests/stdlib/810-addr-identity.ax` |
| A container owns its elements: a `Vec` is released at scope end, and ownership follows the element type, not the constructor | Done | `MM-LIFE-2m`, `tests/stdlib/770-vec-files.ax`, `tests/stdlib/895-scalar-element-read.ax` |
| An element passed beside its container to a function that overwrites its slot keeps a share, as one bound by a `let` does | Done | `MM-LIFE-2m`, `tests/stdlib/778-vec-lent-argument.ax` |
| A field passed beside its struct, or an element handed to a function whose result is a type variable, keeps a share for the call, and the result keeps it when it is that element | Done | `MM-LIFE-2m`, `tests/stdlib/894-field-beside-struct.ax`, `tests/stdlib/900-lent-arg-tyvar-result.ax` |
| A `match` over a field or a vector element, and a `let` of one, keep the value while the code under them overwrites its place | Done | `MM-LIFE-2m`, `tests/stdlib/901-match-place-overwrite.ax` |
| A `match` arm that computes a word from its binder, as `(Some (strLen a))` does, lets the scrutinee go | Done | `MM-LIFE-2c`, `tests/stdlib/902-match-arm-over-binder.ax` |
| An element taken out with `vecPop` is released: a trusted function declares `result(owned)` and the caller hands the share back | Done | `MM-LIFE-2n`, `tests/stdlib/846-vec-pop-release.ax` |
| A vector passed straight to `vecGet` is not leaked | Done | `MM-LIFE-2m`, `tests/stdlib/893-vecget-temporary.ax` |
| Final reference results from calls with surplus arguments own one share through temporaries, returns and generic field stores | Done | `tests/stdlib/962-overapplied-result-ownership.ax` |
| `vecFree` and `mapFree` leave safe code, so an early drop can't free a block still in use | Done | `tests/stdlib/406-array-form-large-block.ax`, `tests/stdlib/489-map-free.ax` |
| CI builds the compiler with a poisoned allocator and checks it still reaches its fixpoint | Done | `scripts/check-poisoned-alloc.sh` |
| No cast, `Int` container or raw store lets a heap value become an untracked word in safe code | Not started | `MM-VAL-24` |
| Raw-address functions and the word-to-reference accessors (`vecGetStr`, `vecGetVec`, `memGetWordStr`, `memGetWordVec`) leave the public library, so safe code can't forge an address or a reference | Not started | `MM-EXEC-9d` |
| `fileFd` and the raw descriptor calls can't close a descriptor a `File` owns | Done | `MM-EXEC-17a`, `tests/diagnostics/1225-descriptor-hole.ax` |
| Standard input, output and error are `File` values, with one owned poller and one terminal raw-mode guard | Done | `MM-EXEC-17b`, `tests/stdlib/785-stdio-file.ax`, `tests/stdlib/786-poller-owned.ax` |
| Secrets and keys wipe themselves when dropped, and a child process is an owner | Not started | |
| Typed promotion of owned values out of a region, preserving nested ownership, aliases and sharing | Planned | `MM-RGN-7` |
| Cycles leak until an arena reset | Accepted for 1.0 | `MM-LIFE-3` |

## Checks and claims

| Item | State | Where it's specified or tested |
|---|---|---|
| Contracts are visible in hover, signature help, completion and generated documentation | Done | `tests/lsp/drive.py`, `scripts/check-stdlib-api.sh` |
| Contracts diagnose certainly false calls and postconditions at compile time | Planned | |
| A misspelt checked AXTAG key is an error | Done | `AX3039` |
| Handler values propagate effects through local bindings and handler parameters | Done | `tests/diagnostics/1390-handler-effects.ax`, `tests/stdlib/940-handler-values.ax` |
| Unsafe declarations retain type, effect and restriction checks | Done | `tests/diagnostics/1395-unsafe-checks-retained.ax` |
| Checked memory boundaries explain source representation and lifetime obligations consistently in the compiler and editor | Done | `tests/lsp/memory-boundaries.py` |
| An `effect(unsafe)` claim is judged by what the body itself performs | Done | `AX3010`, `tests/diagnostics/1200-unsafe-claim-open-row.ax` |
| A statically certain defect or a refuted claim is an error, not a warning | Done | `tests/diagnostics/severity.policy` |
| Retired and never-assigned codes are recorded and never reused | Done | `ERR-DIAG-2` |
| A check after an error is poisoned, not cascaded | Done | `ERR-DIAG-3`, `tests/diagnostics/1380-poisoned-checks.ax` |
| A definition without a signature draws one diagnostic, not one per use of its parameters | Planned | `ERR-DIAG-3` |
| A cast to a spawn handle counts as forging | Done | `MM-PAR-8`, `tests/diagnostics/1260-spawn-forging-cast.ax` |
| Two signatures for one name are refused, instead of the later one winning | Done | `tests/diagnostics/1255-duplicate-signature.ax` |

## One way to do each thing

| Item | State | Where it's specified or tested |
|---|---|---|
| Imports form the module header and cannot appear later or inside expressions | Done | `tests/diagnostics/1400-late-import.axbad`, `tests/diagnostics/1401-nested-import.axbad`, `tests/diagnostics/1402-lambda-import.axbad` |
| Block-local declarations and named struct construction across compiler, library, documentation and editor tools; retire grouped bindings and positional struct construction | In progress | `tests/stdlib/963-block-local-bindings.ax`, `tests/stdlib/964-named-struct-construction.ax` |
| REPL uses the brand palette and shares diagnostic and language help with the CLI | Done | `scripts/check-repl-tui.sh`, `tests/repl/170-explain-shape.txt` |
| Editor and REPL completion exclude retired syntax | Done | `tests/lsp/drive.py`, `scripts/check-replcomp.sh` |
| Fixed-width signed and unsigned integers through 64 bits, with explicit casts and width-correct operators | Done | `tests/stdlib/948-fixed-width-integers.ax`, `tests/stdlib/949-fixed-width-alias-callback.ax` |
| Compact fixed-width integer struct fields, with aligned reference maps and native allocation measurements | Done | `tests/stdlib/959-compact-struct-fields.ax` |
| Compiler bracket and format flags use guarded UInt8 storage with measured allocation savings | Done | `tests/selfhost/1018-compiler-byte-storage.ax`, `tests/selfhost/272-highlight.ax` |
| Compact ADT and vector storage with complete generic storage evidence | Planned | |
| Wider integer literals, widths beyond 64 bits and checked fixed-width FFI shims | Planned | |
| Transparent type aliases support parameters, simultaneous substitution and finite expansion | Done | `tests/stdlib/946-generic-aliases.ax`, `tests/diagnostics/1420-generic-alias-arity.ax`, `tests/diagnostics/1421-generic-alias-parameters.axbad`, `tests/diagnostics/1422-recursive-alias.ax` |
| Generic bodies preserve parameters through returned containers, ADTs and callbacks; each call constrains repeated parameters independently | Done | `tests/diagnostics/1443-generic-result-rigidity.ax`, `tests/stdlib/956-generic-result-rigidity.ax` |
| Distinguish homonymous nominal types across module boundaries, runtime protocols and host bindings | Done | `tests/stdlib/954-nominal-identity.ax`, `tests/stdlib/957-main-result-nominal.ax`, `tests/diagnostics/1444-err-protocol-provenance.ax`, `tests/ffi/host/nominal.ax` |
| Numeric conversions are explicit; mixed numeric operands and incompatible ADT parameters are refused | Done | `tests/diagnostics/1117-mixed-numeric-operands.ax`, `tests/diagnostics/1410-explicit-numeric-boundaries.ax` |
| Explicit numeric conversions document rounding and range; exact conversions refuse value loss | Done | `tests/stdlib/947-exact-numeric-conversions.ax` |
| Legacy CLI and syntax removed: `axiom FILE`, expression `fn`, keyword struct construction, format aliases | Done | `compat/BREAKING` |
| The precondition tag removed; contracts are `pre` and `post` | Done | `AX3095` |
| Macro hygiene has one implementation | Done | `MAC-HYG-10` |
| Literal identifiers in macro patterns compare by binding, not spelling | Planned | `MAC-HYG-9`, `MAC-LANG-14a` |
| `macro` and `emacro` become one form: a rule's template is declarations or one expression, and the invocation's position picks | Done | `MAC-LANG-14`, `tests/stdlib/930-macro-both-positions.ax` |
| Errors propagate with a postfix `?` | Done | `ERR-SUGAR-4`, `tests/stdlib/847-propagate.ax`, `tests/diagnostics/1300-propagate-position.ax` |
| A unit type with a value, `()`, so an `if` without an else, a dropped branch and a procedure need no filler `0` | Done | `tests/stdlib/890-unit-and-if.ax`, `tests/stdlib/892-dropped-branches.ax` |
| Explicit numeric conversion uses `(as value Type)`; bit reinterpretation has named Float operations | Done | `tests/stdlib/943-as-numeric.ax`, `tests/diagnostics/1411-as-boundaries.ax` |
| AXQL select aliases use `(alias expression name)` and keep `as` for language conversions | Done | `tests/axqlite/620-macro-text.ax`, `tests/axqlite/625-macro-forms.ax` |
| Prefix pipelines evaluate each stage once, with explicit argument placement | Done | `tests/stdlib/945-pipelines.ax`, `tests/diagnostics/1430-pipeline-boundaries.ax` |
| One code-generation route: the MIR route is finished or removed | Not started | |

Numeric values never widen or narrow implicitly. Expected types do not
insert conversions into calls, returns, assignments or constructor
payloads. Algebraic data types (ADTs) keep their type arguments and payload
representation. Convert payloads explicitly when constructing a different
ADT instantiation.

## Standard library

| Item | State | Where it's specified or tested |
|---|---|---|
| Shorter, consistent names, with duplicates removed and internals private | In progress | `compat/BREAKING` |
| Failure answers a `Result` or `Option`, never a sentinel | In progress | `ERR-ADOPT-1`, `compat/SENTINELS` |
| `Map` is generic, and `vecSortBy` takes a typed comparator | Done | `tests/stdlib/081-map-value-type.ax`, `tests/stdlib/405-vec-sort.ax` |
| `Par` folds into `Task` | Done | `tests/stdlib/476-par-pool.ax`, `tests/stdlib/492-par-pool-checked.ax` |
| Sleep and a monotonic clock | Done | `MM-PAR-12`, `tests/stdlib/851-instant-sleep.ax` |
| A would-block test for sockets: `errorKind` answers `WouldBlock` | Done | `tests/stdlib/840-error-kind.ax` |
| AXQLite: links between tables, a dump that loads back, and messages that name the statement and say what to do | Done | `tests/axqlite/513-axql-links.ax`, `tests/axqlite/618-api-errors.ax` |
| AXQLite: aggregates, `GROUP BY`, functions, `LIKE`, `IN`, `UPSERT`, `DEFAULT`, `CHECK` and `ALTER TABLE ADD COLUMN` | Done | `tests/axqlite/512-axql-aggregates.ax`, `tests/axqlite/514-axql-constraints.ax` |
| AXQLite: `DROP COLUMN` and `RENAME COLUMN` | Done | `tests/axqlite/516-axql-alter.ax` |
| AXQLite: link paths of more than one step, and links followed backwards | Not started | |
| AXQLite: a change feed a program can watch for a table's changes | Not started | |
| AXQLite: compacting a database file in place | Not started | |

## Concurrency and bounded execution

| Item | State | Where it's specified or tested |
|---|---|---|
| Channels, mutexes and cancel tokens are owners, so disposal can't race a use | Done | `MM-PAR-15`, `MM-PAR-16`, `tests/stdlib/803-shared-owner.ax` |
| Typed promotion of owned task inputs and results across joins, with cleanup on cancellation and failure | In progress | `MM-PAR-6`, `MM-RGN-7` |
| Task slab sizes are checked before mapping or spawning; indices cross without overflow-prone packing | Done | `tests/stdlib/941-task-index-and-size.ax` |
| Explicit task grain with bounded range jobs, ordered answers and per-range budgets | Done | `tests/stdlib/951-task-ranges.ax`, `tests/stdlib/952-task-ranges-lifecycle.ax` |
| Checked streaming callbacks reject retained answers and temporary allocations, with explicit forwarding contracts | Done | `tests/stdlib/953-scoped-task-fold.ax`, `tests/diagnostics/1440-scoped-callback-escape.ax` |
| Cancellation is checked between spawn attempts, and grace starts at the observed shutdown boundary | Done | `tests/stdlib/960-task-launch-cancellation.ax`, `tests/litmus/check-task-launch.py` |
| Checked numeric callbacks with primitive captures, local accumulators and fixed-width task answers | Done | `tests/stdlib/961-task-compute-contract.ax`, `tests/diagnostics/1445-compute-callback.ax` |
| Scale multicore work with bounded worker reuse and scheduling, preserving isolation, submit order and cancellation | Planned | `MM-PAR-3`, `MM-PAR-5`, `MM-PAR-17` |
| A `parallel` form keeps no arena storage once it returns, in both lowerings | Done | `tests/litmus/par-forms.ax`, `scripts/check-parallel.sh` |
| Fixed worker, queue, memory and stack budgets, with exhaustion and shutdown tests | Done | `MM-PAR-17`, `tests/stdlib/920-task-stack.ax` |
| Shared mutable heap graphs across tasks | Not started | |

Worker reuse requires the checked `compute(worker)` boundary: general
callbacks can mutate a captured vector independently in each fresh
process, while serial reuse would accumulate those mutations. The
compute entry still forks each invocation. Persistent scheduling,
measured startup amortisation and a broader checked numeric syntax,
including pattern matching, remain work to do.

## Tools

| Item | State |
|---|---|
| Formatting preserves the import header, doc-comment attachment and literal contents, without trailing layout whitespace | Done |
| Width-aware argument packing and attached call comments, with batch preflight, stdin/stdout and non-writing diffs | Done |
| Long function headers, signatures, types, aliases, selective imports and positional patterns share canonical list layout | Done |
| Doc comments: `;;` documents the declaration below and `;;!` the module, and hover, the library reference and `axiom doc` read them | Done |
| Hover explains every word of the language: keywords, builtin types, effects, operators, AXTAG keys and values, and imported modules | Done |
| Every public standard library name carries a doc comment, and every module a `;;!` block | Done |
| `;@axiom:nolint` quiets hints and warnings by name, code or group, per declaration or per file, and never an error | Done |
| The language server sends semantic tokens, so every editor colours keywords, functions, macros, constructors, fields and effects apart from variables | Done |
| A VS Code extension ships in the repository, with a TextMate grammar and the language client (`editors/vscode/`) | Done |
| Tree-sitter colours each token the way VS Code does, gated character by character | Done |
| Tree-sitter colours a use of a top-level function or macro, or of an imported name, by what it resolves to | Not started |
| The VS Code extension is published to the marketplace | Not started |
| `axiom doc` renders the documentation and the library reference locally and opens them in your browser | Done |
| Debug information (DWARF), a message on SIGSEGV, and inlined frames in backtraces | Not started |
| An assembler error inside an `asm` template is reported at the form | Done |
| `asm` takes floating-point, vector and memory operands | Not started |
| The restricted profile requires `nostack` on every selected arm of an allowed `asm` body | Done |
| Selected steady and nonblocking entries are analysed transitively; stale or ambiguous assurance exceptions fail closed | Done |
| Imported foreign items keep separate checker, source and linker identities when their source names match | Done |
| Resource reports consume checked `Spawn` and `Block` effects, preserve handled effect subtraction and refuse unknown kernel calls in nonblocking entries | Done |
| Stack reports cover every selected source entry and the assembled IRQ save and dispatch extent; additional machine roots cannot bypass main | Done |
| Selective foreign-item imports and transitive foreign references preserve visibility and module resolution | Done | `tests/profile/verify-foreign-resolution.py` |
| Selective import lists are validated inside every imported module, including unused names | Not started |
| The test runner runs tests in parallel | Done |
| UTF-16 ranged document edits apply in order and atomically, with monotonic versions and versioned diagnostics | Done |
| The language server cancels obsolete work and serves requests from revisioned background analysis on hosts with process and poller support | Done |
| Unsaved imported documents feed checks and navigation, with transitive dependency invalidation and diagnostics for open modules | Done |
| Workspace request results are cached by revision, with exact file and module-search validation | Done |
| Parsed and checked semantic models are reused across different requests | Not started |
| Completion uses expected types, offers auto-import edits and resolves expensive details on demand | Not started |
| Safe assists organise top-level imports and preserve attached comments | Not started |
| Semantic tokens support range and delta requests | Done |
| The language server answers inside macro invocations | Not started |
| Packages can pin a version | Not started |
| A published performance profile | Not started |

The editor comparison follows rust-analyzer's
[features](https://rust-analyzer.github.io/book/features.html) and
[architecture](https://rust-analyzer.github.io/book/contributing/architecture.html).
Axiom has navigation, hover, completion, refactoring and formatting.
The rows above track the remaining analysis, responsiveness and completion
work; they do not claim full feature parity.

## Platforms and release

| Item | State |
|---|---|
| CI green on linux-aarch64 and darwin-aarch64 | Done |
| Reproducible toolchain records for every supported target | In progress |
| The compiler hosts itself on Windows, and crypto runs there | Not started |
| The embedded checks run on a named board | Not started |
| Compatibility rules enforced at 1.0 (`COMPAT-6`) | Planned |

The maintainer cuts releases.
