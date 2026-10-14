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
| A container owns its elements: a `Vec` is released at scope end, and ownership follows the element type, not the constructor | Done | `MM-LIFE-2m`, `tests/stdlib/770-vec-files.ax` |
| An element passed beside its container to a function that overwrites its slot keeps a share, as one bound by a `let` does | Done | `MM-LIFE-2m`, `tests/stdlib/778-vec-lent-argument.ax` |
| A field passed beside its struct, or a result typed by a type variable, keeps a share for the call | Done | `MM-LIFE-2m`, `tests/stdlib/893-vecget-temporary.ax`, `tests/stdlib/894-field-beside-struct.ax` |
| An element taken out with `vecPop` is released: a trusted function declares `result(owned)` and the caller hands the share back | Done | `MM-LIFE-2n`, `tests/stdlib/846-vec-pop-release.ax` |
| A vector passed straight to `vecGet` is not leaked | Done | `MM-LIFE-2m`, `tests/stdlib/893-vecget-temporary.ax` |
| `vecFree` and `mapFree` leave safe code, so an early drop can't free a block still in use | Done | `tests/stdlib/406-array-form-large-block.ax`, `tests/stdlib/489-map-free.ax` |
| CI builds the compiler with a poisoned allocator and checks it still reaches its fixpoint | Not started | |
| No cast, `Int` container or raw store lets a heap value become an untracked word in safe code | Not started | `MM-VAL-24` |
| Raw-address functions leave the public library, so safe code can't forge an address | Not started | |
| `fileFd` and the raw descriptor calls can't close a descriptor a `File` owns | Done | `MM-EXEC-17a`, `tests/diagnostics/1225-descriptor-hole.ax` |
| Standard input, output and error are `File` values, with one owned poller and one terminal raw-mode guard | Done | `MM-EXEC-17b`, `tests/stdlib/785-stdio-file.ax`, `tests/stdlib/786-poller-owned.ax` |
| Secrets and keys wipe themselves when dropped, and a child process is an owner | Not started | |
| A reference can be promoted out of a region | Planned | `MM-RGN-7` |
| Cycles leak until an arena reset | Accepted for 1.0 | `MM-LIFE-3` |

## Checks and claims

| Item | State | Where it's specified or tested |
|---|---|---|
| A misspelt checked AXTAG key is an error | Done | `AX3039` |
| An `effect(unsafe)` claim is judged by what the body itself performs | Done | `AX3010`, `tests/diagnostics/1200-unsafe-claim-open-row.ax` |
| A statically certain defect or a refuted claim is an error, not a warning | Done | `tests/diagnostics/severity.policy` |
| Retired and never-assigned codes are recorded and never reused | Done | `ERR-DIAG-2` |
| A check after an error is poisoned, not cascaded | Planned | `ERR-DIAG-3` |
| A cast to a spawn handle counts as forging | Done | `MM-PAR-8`, `tests/diagnostics/1260-spawn-forging-cast.ax` |
| Two signatures for one name are refused, instead of the later one winning | Done | `tests/diagnostics/1255-duplicate-signature.ax` |

## One way to do each thing

| Item | State | Where it's specified or tested |
|---|---|---|
| Legacy CLI and syntax removed: `axiom FILE`, expression `fn`, keyword struct construction, format aliases | Done | `compat/BREAKING` |
| The precondition tag removed; contracts are `pre` and `post` | Done | `AX3095` |
| Macro hygiene has one implementation | Done | `MAC-HYG-10` |
| Literal identifiers in macro patterns compare by binding, not spelling | Planned | `MAC-HYG-9`, `MAC-LANG-14a` |
| `macro` and `emacro` become one form | Not started | [macro-system.md](macro-system.md) |
| Errors propagate with a postfix `?`, and the `try` macro is removed | Done | `ERR-SUGAR-4`, `tests/stdlib/847-try-operator.ax`, `tests/diagnostics/1303-try-removed.axbad` |
| A unit type with a value, `()`, so an `if` without an else, a dropped branch and a procedure need no filler `0` | In progress: the library and compiler drop theirs after the next reseed | `tests/stdlib/890-unit-and-if.ax`, `tests/stdlib/892-dropped-branches.ax` |
| Functional piping as a structural form: a pipe the tree, the language server and agents see stage by stage, with an explicit `_` hole for where the value goes | Not started | |
| One code-generation route: the MIR route is finished or removed | Not started | |

## Standard library

| Item | State | Where it's specified or tested |
|---|---|---|
| Shorter, consistent names, with duplicates removed and internals private | In progress | `compat/BREAKING` |
| Failure answers a `Result` or `Option`, never a sentinel | In progress | `ERR-ADOPT-1`, `compat/SENTINELS` |
| `Map` is generic, and `vecSortBy` takes a typed comparator | Not started | |
| `Par` folds into `Task` | Not started | |
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
| Owned task inputs and results, with cleanup across joins, cancellation and failure | In progress | `MM-PAR-6` |
| A `parallel` form keeps no arena storage once it returns, in both lowerings | Done | `tests/litmus/par-forms.ax`, `scripts/check-parallel.sh` |
| Fixed worker, queue, memory and stack budgets, with exhaustion and shutdown tests | In progress: a task's stack size isn't a budget yet | `MM-PAR-17` |
| Shared mutable heap graphs across tasks | Not started | |

## Tools

| Item | State |
|---|---|
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
| The restricted profile requires `nostack` of an allowed `asm` block, or counts what it pushes | Not started |
| The test runner runs tests in parallel | Done |
| The language server answers inside macro invocations | Not started |
| Packages can pin a version | Not started |
| A published performance profile | Not started |

## Platforms and release

| Item | State |
|---|---|
| CI green on linux-aarch64 and darwin-aarch64 | Done |
| Reproducible toolchain records for every supported target | In progress |
| The compiler hosts itself on Windows, and crypto runs there | Not started |
| The embedded checks run on a named board | Not started |
| Compatibility rules enforced at 1.0 (`COMPAT-6`) | Planned |

The maintainer cuts releases.
