# Assurance

This page is for anyone weighing Axiom for safety-related, embedded or
operating-system work. It lists what the compiler and runtime
guarantee, the evidence behind each guarantee, the configurations that
evidence covers, the known defects, and what still stands between
Axiom and a certified system.

Four words mean different things here:

- **Implemented**: the code is in this repository.
- **Verified**: a named check or fixture tests the property, within the
  limits stated beside it.
- **Qualification-ready**: the evidence an assessor would ask for exists
  for one frozen configuration.
- **Approved**: an independent, competent assessor has accepted that
  evidence for a specific application, platform, configuration and
  development process.

Nothing in Axiom is approved, and nothing here claims compliance with a
standard. Whether a system built with Axiom is fit for a mission
depends on that system, its hardware and its development process, and
an independent assessment of them.

## Reference configurations

The evidence on this page comes from these configurations. A check
that passes on one doesn't extend its claim to another.

| | H3 | H2 | E1 |
|---|---|---|---|
| Target | `darwin-aarch64` | `linux-aarch64` | `baremetal-aarch64` |
| Machine | Apple silicon, macOS 14 runner | AArch64, Ubuntu 24.04 runner | Cortex-A72 as modelled by QEMU, `virt` board, EL1 |
| LLVM | 23 (Homebrew `llvm@23`) | 18 (Ubuntu `llvm-18`) | the host's |
| Runtime | processes by default, `--threads` | processes by default, `--threads` | one thread plus `isr(irq)` and `isr(fault)` |
| Heap | `mmap` arena | `mmap` arena | static arena with `--heap-ceiling` |
| Runs in CI | the whole test battery | the whole test battery | compile-time checks, and the QEMU runs on the H3 leg |

CI also builds the compiler from the seed on `linux-x86_64`, and on
`freebsd-x86_64` and `freebsd-aarch64` in virtual machines, and
assembles code for every hosted target. Nothing has run on embedded
hardware.

Each CI job that sets up LLVM through the provisioning action records
the `opt`, `llc`, `clang`, `ld.lld` and `lld-link` versions it used,
and stops if `llc` reports another LLVM major. The other four versions
are recorded but not compared. The FreeBSD jobs and the seed-only
bootstrap job print `llc --version` and compare nothing.
Optimisation levels 0 to 3 are exercised by the `.optstable` fixtures
under `tests/stdlib/`; the default is 1.

## Guarantees and evidence

The rules are in [the memory model](memory-model.md) and
[the restricted profile](restricted-profile.md). The IDs match the
memory model's *Assurance evidence* table.

### A. Correctness of the runtime

| ID | Guarantee | Rule | Evidence |
|---|---|---|---|
| R-A1 | A `parallel` binding that traps inside a recovery point answers its status once | `MM-PAR-7` | `tests/stdlib/522-parallel-recover.ax`, `scripts/check-parallel.sh` |
| R-A2 | No child outlives its scope on abort, trap, return or end | `MM-PAR-7` | `scripts/check-parallel.sh`, `scripts/check-task.sh` |
| R-A3 | A refused spawn or failed join is status 78, never success | `MM-PAR-7` | `scripts/check-parallel.sh` |
| R-A4 | A finished thread returns its arena | `MM-PAR-6a` | `scripts/check-parallel.sh` (H2 only) |
| R-A5 | The allocator refuses a negative or unrepresentable size with status 70 | `MM-ALLOC-7a` | `tests/stdlib/520-alloc-size.ax` |
| R-A6 | Every unsafe primitive, syscall, `asm` form and `extern` call needs `effect(unsafe)` | `MM-EXEC-9c`, `MM-EXEC-9d` | `tests/diagnostics/1010-unsafe-primitives.ax`, `tests/diagnostics/1080-unsafe-syscalls.ax`, `tests/diagnostics/1045-inline-asm-unsafe.ax`, `tests/diagnostics/1116-extern-unsafe.ax` |
| — | Unsafe declarations retain type checks, required effects and restrictions; handler values propagate their effects | `MM-EXEC-9d`, `MM-EXEC-10` | `tests/diagnostics/1395-unsafe-checks-retained.ax`, `tests/diagnostics/1390-handler-effects.ax`, `tests/stdlib/940-handler-values.ax` |
| R-A7 | A released block's free-list link can't be released as a count | `MM-LIFE-2k` | `tests/stdlib/521-release-filed.ax` |
| R-A8 | A join of a handle another binding spawned is status 78, refused before it waits or writes | `MM-PAR-7` | `scripts/check-parallel.sh` |
| R-A9 | A count that would overflow traps with status 70 before the write | `MM-LIFE-2l` | `tests/stdlib/527-retain-overflow.ax` |
| R-A10 | An unused declaration changes no other declaration's meaning | none | `scripts/check-metamorphic.sh` |
| R-A11 | A malformed `asm` form is refused at the part that is wrong before any assembler runs, and a template line the assembler rejects is reported at that line | `MM-FFI-9` | `scripts/check-embedded.sh` (A15), `tests/diagnostics/1280-inline-asm-forms.ax` |

Runtime traps exit with the reserved statuses 70 to 86 at every
optimisation level (`scripts/check-trap-statuses.sh`). The table in
[the memory model](memory-model.md) says what raises each.

### B. Ownership and safe interfaces

| ID | Guarantee | Rule | Evidence |
|---|---|---|---|
| R-B1 | `vecGet` and `vecSet` trap with status 77 on a bad index before any read or write | `MM-MUT-5a` | `tests/stdlib/464-index-trap.ax`, `tests/stdlib/525-vec-set-bounds.ax` |
| R-B2 | `Par` and `Task` pools keep at most their width of workers and handles; a `Task` pool also keeps its `keep` budget of answers and its `memory` and `stack` budgets per task, and running out of a budget is an answer in a slot | `MM-PAR-13`, `MM-PAR-17` | `tests/stdlib/523-par-pool-bounded.ax`, `tests/stdlib/852-task-budgets.ax`, `tests/stdlib/920-task-stack.ax`, `scripts/check-task.sh` |
| R-B3 | A region-allocated value can't escape its region by store, return, capture or argument | `MM-RGN-1` to `MM-RGN-6` | `scripts/check-region-escape.sh` |
| R-B6 | A cast that forges a reference needs `effect(unsafe)`, and the declaration that says it is a trusted encapsulation whose callers inherit nothing | `MM-EXEC-9d` | `tests/diagnostics/1040-forging-cast.ax`, `tests/diagnostics/1260-spawn-forging-cast.ax` |
| R-B7 | A dead chain of any depth is released without deep recursion, and a second release of a dead block can't corrupt the allocator | `MM-LIFE-2d`, `MM-LIFE-2k` | `tests/stdlib/555-release-deep-chain.ax`, `scripts/check-reclaim-soak.sh` |
| R-B8 | Reuse is bounded by the allocator's size classes, and held, reusable and mapped bytes are readable with `memStats` | `MM-ALLOC-24`, `MM-ALLOC-25` | `tests/stdlib/703-memory-stats.ax`, `scripts/check-reclaim-soak.sh` |
| R-B9 | A recovery point allocates nothing, and a contained trap leaves the heap consistent | `MM-ALLOC-23` | `tests/stdlib/560-recover-record.ax`, `tests/stdlib/561-failed-operations.ax` |
| R-B10 | A typed read or write checks its range before the kernel sees it | `MM-EXEC-9f` | `tests/stdlib/610-typed-io-bounds.ax` |
| — | Releasing the last share of a `File`, socket or database connection closes it once | `MM-EXEC-17` | `tests/stdlib/697-resource-owner.ax`, `tests/stdlib/698-file-lifetime.ax` |
| — | An element `vecPop` hands over is released by the caller, through the `result(owned)` claim a trusted function makes | `MM-LIFE-2n` | `tests/stdlib/846-vec-pop-release.ax`, `scripts/check-container-reclaim.sh` |
| — | A `mut` local of a reference type releases the value a `set` overwrites and its last value at scope end, each once, and a lambda that captured it keeps its own share | `MM-MUT-1`, `MM-VAL-16` | `tests/stdlib/708-mut-slot-reassign.ax`, `tests/stdlib/709-mut-slot-capture.ax` |

A `mut` local releases only when the compiler can show that no read of
it is still in use at the `set` or the scope end, and never when its
type is a type variable. Any other `mut` local keeps what it
held: the memory stays allocated and a `File` stays open.
[The memory model](memory-model.md) lists those shapes under event 3.

A function that performs an unsafe operation says `effect(unsafe)`,
which makes it a trusted encapsulation. Nothing proves a trusted body
correct. A raw function
that takes an address or a handle as an `Int` can still crash safe code
that passes it a bad one. `axiom symbols` marks every trusted function
`#unsafe=trusted`, so you can list the set to review.

### C. Concurrency

| ID | Guarantee | Rule | Evidence |
|---|---|---|---|
| R-C1 | No container or unborrowed reference is captured by a concurrent binding | `MM-PAR-6` | `tests/diagnostics/656-parallel-container-capture.ax`, `scripts/check-parallel.sh` |
| R-C2 | A mutex excludes, and its guard releases it once on every way out of its scope, a recovered trap included; every blocking call has a timed form; tasks report failure, deadline and cancellation per slot | `MM-PAR-11` to `MM-PAR-13`, `MM-PAR-15` | `tests/stdlib/800-mutex-guard.ax`, `scripts/check-task.sh`, `scripts/check-protocol-model.sh` |
| R-C2a | A bounded channel delivers every word exactly once, in order per sender | `MM-PAR-10` | `tests/stdlib/528-chan.ax`, `scripts/check-chan.sh` |
| R-C3 | Atomics are sequentially consistent on every hosted target, and the litmus tests show no forbidden outcome | `MM-PAR-1`, `MM-PAR-9` | `scripts/check-atomics.sh` |
| R-C4 | A misaligned atomic traps with status 82 | `MM-PAR-9` | `tests/stdlib/544-misaligned-atomic.ax` |
| R-C5 | A process binding can start and join threads, and a task whose thread never ends is still ended by its deadline, in both lowerings | `MM-PAR-7` | `tests/litmus/thread-in-fork.ax`, `scripts/check-task.sh` |
| R-C6 | Channels, mutexes and tokens are sealed owners, freed when their last holder lets go and never while a binding uses them; safe code can't cast a word to a spawn handle, a guessed word names a live one only by a chance near 2^-40, and a rejoined or forged one traps with status 85 | `MM-PAR-8`, `MM-PAR-16` | `tests/stdlib/803-shared-owner.ax`, `tests/diagnostics/1260-spawn-forging-cast.ax`, `scripts/check-handles.sh` |
| R-C7 | A `parallel` binding can borrow its parent's strings and immutable data, and no binding's count traffic reaches the parent | `MM-PAR-6b` | `tests/stdlib/630-parallel-borrow.ax`, `scripts/check-race.sh` |
| R-C8 | A pure parallel computation answers the same at every width and in both lowerings | `MM-PAR-14` | `tests/stdlib/620-par-float-order.ax`, `scripts/check-task.sh` |
| R-C9 | A sleep ends no sooner than asked, measured on a monotonic clock that never steps backwards | `MM-PAR-12` | `tests/stdlib/851-instant-sleep.ax`, `scripts/check-task.sh` |
| R-C10 | A `parallel` form keeps no arena storage once it returns, however many run, in both lowerings | `MM-RGN-7` | `tests/litmus/par-forms.ax`, `scripts/check-parallel.sh` |

`scripts/check-race.sh` runs the threaded programs under
ThreadSanitizer. It covers the thread lowering only.

### D. Embedded and real-time

| ID | Guarantee | Rule | Evidence |
|---|---|---|---|
| R-D1 | The restricted profile refuses recursion, steady-state allocation and the other RP rules over the whole program, and bounds stack use from machine code | RP-1 to RP-9 | `scripts/check-report.sh`, `scripts/check-stack-bound.sh` |
| R-D2a | Device registers are read and written at their own width, with barriers | `MM-FFI-8` | `scripts/check-embedded.sh` |
| R-D2b | An unhandled CPU exception exits 81, and an `isr(fault)` hook chooses the exit once | `MM-EXEC-16`, `MM-EXEC-19` | `tests/embedded/fault.ax`, `tests/embedded/fault-hook.ax`, `scripts/check-isr.sh` |
| R-D2c | A periodic interrupt workload and a DMA driver keep their budgets and ownership protocol | `MM-EXEC-18`, `MM-FFI-8` | `tests/embedded/periodic.ax`, `tests/embedded/dma.ax` |
| R-D2e | The MMU and caches are on before `main`, with read-only code, non-executable data and stack guards | `MM-EXEC-19` | `tests/embedded/overflow.ax`, `scripts/check-embedded.sh` |

R-D2b, R-D2c and R-D2e run under QEMU on the darwin-aarch64 CI leg.
Emulator evidence is not hardware evidence: QEMU models no caches,
timing or bus faults.

### E. The evidence itself

| ID | Property | Evidence |
|---|---|---|
| R-E1 | Executable models of the allocator and the channel protocol agree with the runtime, seeded mutants never crash the compiler, accepted mutants produce IR `llc` accepts, and the threaded programs run clean under ThreadSanitizer | `scripts/check-runtime-model.sh`, `scripts/check-protocol-model.sh`, `scripts/check-fuzz.sh`, `scripts/check-race.sh` |
| R-E2 | The compiler rebuilds itself byte for byte, two builds agree, and a diverse double compile agrees with an independent checker | `scripts/check-bootstrap.sh`, `scripts/check-reproducible.sh`, `scripts/check-ddc.sh` |
| — | The seed is its source's emission, and its lineage replays to the original anchor | `scripts/check-seed-provenance.sh`, `scripts/check-seed-lineage.sh` |
| — | A gate never reuses a compiler built from another tree | `scripts/check-gate-lib.sh` |

Each method has limits. Bootstrap convergence doesn't prove the
compiler correct, a model proves only the model, and fuzzing explores
the neighbourhood of the corpus.

## Known defects

- Safe code can still turn a reference into a word with `(cast Int v)`,
  or by storing it in an `Int` container slot or through `memSetWord`.
  A value that leaves this way stops being tracked. A function result
  can't do it (`MM-VAL-24`).
- The same word can come back as a reference in safe code, through a
  trusted accessor such as `vecGetVec` or `memGetWordVec`. So a
  `parallel` binding can reach a container its parent holds without
  capturing it, which is the only route R-C1 refuses.
- `vecGetStr` and `vecGetVec` read any slot of any vector as a
  reference, so safe code can forge a `String` or a `Vec` out of a
  vector of numbers (`MM-EXEC-9d`).
- An element or a field handed to a closure that overwrites the slot it
  came from can be freed while the closure reads it. A named function
  is safe only when the container is passed beside the element.
- A `lambda` that captures a value a `match` arm bound keeps a share of
  it that is never released, so a captured `File` never closes.
- Some `match` shapes keep a value they could release. A `let` bound
  to a `match` can keep the match's answer or the binding the match
  reads, and a function handed an owned `Option` that it reads a field
  out of keeps the `Option`. Each leaks, and none frees early.
- Cycles hold their memory until an arena reset.

## Using Axiom in a safety-related system

- Freeze the configuration: the commit, `--target`, `--opt`, `--threads`
  or not, the seed's `bootstrap/CHAIN` row, and the exact `opt`, `llc`
  and linker versions. Make sure `opt` is installed: a build without
  it only warns.
- Use the [restricted profile](restricted-profile.md) and its report.
  Its claims are checked over the whole program.
- Decide your safe state. A trap exits with a defined status; what the
  system does next is yours. On bare metal, an `isr(fault)` hook
  chooses the exit.
- Review the trusted set (`#unsafe=trusted` in `axiom symbols`) and
  every `extern` block, since nothing checks their bodies.
- Bound long-running memory with regions, and watch it with
  `memStats`.
- Hardware faults are out of scope. The handle table catches a flipped
  handle word; nothing else in the language detects memory corruption.

## Gaps to qualification

| Gap | What closes it | Needs |
|---|---|---|
| No independent review; one maintainer, with AI assistance, wrote the code and the checks | Reviewers separate from the implementation, working to a written plan | people |
| No plans written against a licensed standard | Development, verification, configuration and tool plans agreed with the assessor | licensed standards, assessor |
| No structural coverage of application object code | Statement, decision or MC/DC coverage on the target, plus analysis of compiler-added code | target, tools |
| Compiler-added code (counting, traps, the allocator, start-up) isn't traced to source | A per-construct analysis with tests reaching every emitted path | work |
| Nothing has run on embedded hardware; only QEMU boots the images | A named board running the embedded checks | hardware |
| No worst-case execution time or multicore interference analysis | Static or measurement-based timing analysis on the target processor | hardware, tools |
| No tool qualification data for the compiler or the report tool | Tool operational requirements, a qualification plan and traced test cases for one frozen configuration | assessor |
| LLVM is outside the project's control | Verify the executable rather than the toolchain, or use a qualified backend | assessor |

## Standards

Aviation and space projects need evidence for a frozen application and
toolchain. The links below identify the source documents; the evidence
column is Axiom's mapping, not a claim that an objective is satisfied.

| Basis | Scope | Axiom evidence and limits |
|---|---|---|
| [FAA AC 20-115D](https://www.faa.gov/documentLibrary/media/Advisory_Circular/AC_20-115D.pdf) | Recognises DO-178C for airborne software and DO-330 for software tool qualification | Seed lineage, reproducibility and the restricted profile provide inputs. Tool qualification and application verification remain project obligations. |
| [FAA AC 20-193](https://www.faa.gov/documentLibrary/media/Advisory_Circular/AC_20-193.pdf) | Assurance objectives for airborne multicore processors | Concurrency tests exercise language behaviour. They establish no timing or interference bound for an aircraft processor. |
| [ECSS-Q-ST-80C Rev.2](https://ecss.nl/standard/ecss-q-st-80c-rev-2-software-product-assurance-30-april-2025/) | Software product assurance, including development tools and other software affecting the delivered product | Clause 5.6 covers tool selection, suitability and correct use; 6.2.6.12 covers milestone traceability review. The guarantee tables above provide trace links, with qualification gaps listed separately. |
| [ECSS-E-ST-40C Rev.1](https://ecss.nl/standard/ecss-e-st-40c-rev-1-software-30-april-2025/) | Space software engineering from requirements through operations and maintenance | Specifications and tests cover parts of the implementation. A mission still needs its own requirements, verification records and selected configuration. |

The two ECSS editions are dated 30 April 2025. Apply the edition and
tailoring agreed for your project. In particular, emulator runs provide
no evidence for radiation tolerance, hardware fault recovery or timing
on the flight hardware.

Automotive and general functional-safety projects need their own
assessment against ISO 26262 or IEC 61508-3. No clause mapping for those
standards is claimed here.

## Support

The supported release and its window are in [SECURITY.md](../SECURITY.md),
along with how to report a vulnerability. Every change is recorded in
[CHANGELOG.md](../CHANGELOG.md), and breaks to the standard library are
declared in `compat/BREAKING` ([compatibility](compatibility.md)).
Report a defect as a GitHub issue.
