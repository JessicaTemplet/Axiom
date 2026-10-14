# The Axiom error model

How an Axiom program represents failure, passes it along, recovers from
it, and hears about it from the compiler.

## In brief

In Axiom, failure is a value. A function that can fail returns a
`Result` from `stdlib/Err.ax`, and its caller matches on it. There are
no exceptions and no unwinding.

This page is the contract behind that: the types, how errors travel,
what they cost in memory, how a program recovers from a trap, and what
the compiler reports. You don't need it to write everyday code. For
that, read [Standard library](reference.md#standard-library) in the
reference, and [Fallible main](reference.md#fallible-main) for a
`main` that returns a `Result`. Read on if you're writing a library,
porting code that returns sentinel values, or changing the compiler.

---

## 0. How to read this document

Like [memory-model.md](memory-model.md), this page backs every claim
about the implementation with the probe that shows it, and marks every
claim about the design as one.

### 0.1 Rule identifiers

Every normative statement has a stable identifier, such as `ERR-TYPE-2`
or `ERR-PROP-3`. Like diagnostic codes, identifiers are never renamed
and never reused. A withdrawn rule keeps its number and is marked
retired.

### 0.2 Conformance language

`MUST`, `MUST NOT`, `SHOULD` and `MAY` are used as in RFC 2119. Each
rule says which of two audiences it binds:

- **Implementation obligations** bind the compiler and the runtime it
  emits.
- **Program obligations** bind you, the programmer. Nothing checks
  them, so each one is written down. `ERR-PROP-3` is the one that costs
  a program its stack if you ignore it.

### 0.3 Status markers

| Marker | Meaning |
|---|---|
| **H** | **Holds today.** The rule names the probe that shows it. |
| **P** | **Planned.** Required of a conforming implementation, and this one doesn't conform yet. The rule says what happens today instead. |
| **R** | **Refused.** The language doesn't provide this, by design, and the rule says why. |
| **B** | **Blocked.** Specified, but a defect elsewhere prevents it. The rule names the defect. |

An **H** rule with no evidence is a bug in this document. A marker can
carry a note: *gated* means a test or check in CI holds the rule, and
*program obligation* is defined in §0.2.

### 0.4 Reproducing the measurements

Every probe here runs against the compiler in your working tree:

```bash
axiom="$PWD/.axiom-bin/axiom"
"$axiom" --diagnostic-format=ai check probe.ax
"$axiom" --diagnostic-format=ai build --input probe.ax --output probe.bin
./probe.bin; echo $?
```

A program's answer is its **exit status**, the low 8 bits of `main`'s
result. That's why several numbers below are reported modulo 256, and
why `ERR-PROP-3`'s probe checks that the program finishes rather than
what it returns.

**A `memAlloc` address delta measures allocation only below the
allocator's chunk size.** The same loop reads 32.0 bytes per iteration
at 10,000 and at 50,000 iterations, but 795.7 at 100,000. At about
3.2 MB the allocator moves into a chunk that isn't next to the last
one, and the subtraction counts the gap. Every delta below stays inside
one chunk. Where that isn't possible, the page reports a per-iteration
cost worked out from two points.

---

## 1. What exists today

### 1.1 `Option` is built in, `Result` is imported

`Some` and `None` are built-in constructors. They are registered before
any user constructor (`self_host/typecheck.ax`), need no declaration and
no import, and are written `(Option Int)` in a signature.

`Ok`, `Err` and `Result` are not built in. They are ordinary
declarations in `stdlib/Err.ax` (§2), and you reach them with
`(import Err)`. Without that import, `(Ok 1)` is `AX3001` in an
expression and `AX3003` in a pattern. That asymmetry is `ERR-TYPE-2`
holding, not a gap. It's the one difference to carry into §2: `Option`
needs no import, and `Result` does.

<a id="12-failure-was-a-sentinel-in-64-places-by-a-proxy-that-has-since-inverted"></a>
### 1.2 Sentinel values, and how the migration is sized

A *sentinel* signals failure with a value from the success type's own
range, such as `-1` or a negated `errno`. The standard library was
written that way, and most of it has since moved to `Result` and
`Option` (§10).

A sentinel is worse than inelegant. It is a value of the success type,
so nothing in the type system tells "the file is 4 bytes long" from
"the call failed with `errno` 4, negated". Every sentinel depends on
the caller remembering to check.

The migration's baseline came from a `grep` proxy:
`grep -cE "errno|sentinel|\(- 0 1\)"` across `stdlib/*.ax` and
`stdlib/Sys/*.ax`, excluding `Err.ax` itself. As the baseline, it
counted 64 sites over 12 files. This is the table `ERR-ADOPT-1` and §10
refer to:

| Module | Sites | Convention |
|---|---|---|
| `stdlib/Sys.ax` | 21 | `-errno`, Darwin's carry-flag protocol normalised into it |
| `stdlib/IO.ax` | 11 | `-errno`, forwarded from `Sys` |
| `stdlib/Utf8.ax` | 6 | `-1` |
| `stdlib/Map.ax` | 6 | `-1` / absent-key |
| `stdlib/Cereal.ax`, `stdlib/Path.ax`, `stdlib/Rpc.ax` | 3 each | `-1` |
| `stdlib/Str.ax`, `stdlib/Intern.ax`, `stdlib/Sys/Platform.darwin.ax` | 2 each | `-1` / `-errno` |
| `stdlib/Par.ax` | 1 | `-errno`, in the private `parRunWord` only: the one word a join can carry |
| `stdlib/Vec.ax` | 1 | `-1` |

The platform shim is counted because `Platform.darwin.ax` is where the
carry-flag protocol is normalised. It is the one place the convention is
implemented rather than forwarded.

**The proxy no longer sizes anything.** Recounted once the migration
was well under way, the same command read 120 hits over 17 files, while
the public declarations it stood for had fallen from 38 to 2. It has
kept rising since.

Of those 120 hits, 94 were comment lines, and the migration wrote them.
`stdlib/Sys.ax` alone had 52 in comments against 5 in code, each
comment explaining what an `errno` meant at a call that no longer
returns one. *The proxy rises when the migration succeeds.* The header
of `compat/SENTINELS` records the same flaw in that census's old
metric, which counted doc-comments and so rewarded silence. Both sized
a population by matching text instead of reading declarations.

None of the 26 code hits is a public declaration that answers a
sentinel:

| What the line does | Hits | Where |
|---|---|---|
| Seeds a loop accumulator, such as `(mut found (- 0 1))`; never an answer | 4 | `Str.strFind` (which already answers `(Option Int)`), `rdFindHeaderEnd`, `rdContentLength`, `rpcReadMsg` |
| A private helper below a wrapper that already answers `Option`, keeping `-1` on the recursion (§10's rule for `internFindFrom`) | 4 | `internFindFrom` (twice), `pathLastSlashFrom`, `pathLastDotFrom` |
| `Map.ax`'s private probe walk below `mapGet`, whose absent-key answer is a caller-supplied default and not a sentinel | 4 | `mapFindSlot`, `mapFindLoop` (twice), `mapInsertNoGrow` |
| Passes `-1` as an argument, or sets a local to it, in `stdlib/Sys.ax` | 3 | `netAddrText`'s zero-run seed, `sysSignalOpenRaw`'s syscall slot, `sysRandomBytes`' `(set rc (- 0 1))` |
| A bitwise NOT written as XOR with all ones, `(^ x (- 0 1))` | 2 | `netSetBlocking`, `termFlagClear` |
| `EVFILT_READ`, a kernel constant that happens to be -1 | 2 | `pollReadFilter` on Darwin and FreeBSD |
| A private peek | 1 | `Cereal.ax`'s `jcPeek` |
| Renders an `errno` into a message | 1 | `IO.ax`'s `ioResult` |

The two public sentinels that remain, `keyStrEnd` and `keyInFill`
(§10.1), are invisible to the command for a third reason: they live in
`stdlib/Tui/`, outside its glob. Counted directly, they hold 6 and 7
hits. So the proxy over-counts prose in 17 files and misses the only
two rows still owed.

The proxy is kept here as a record, not as a metric. What sizes the
migration is `compat/SENTINELS`. It counts public *declarations* by what
their bodies answer: *failure*, a raw negative `errno` that `Result` is
for, and *absence*, a `-1` for "not found" that wants `Option`. Every
run of `scripts/check-compat.sh` recomputes it
(`tests/compat/verify-compat.py`'s `sentinel_census`), and the result
must match the committed file row for row. It reads **0 failure and 2
absence**. §10.1 names both, and what stops each from being ported.

### 1.3 Five traps

- **Division or remainder by zero** writes `axiom: division by zero` to
  fd 2 and exits with status **72**. Probe: `(fn (main) (/ 10 (- 1 1)))`
  exits 72. This confirms `MM-VAL-3a`.
- **An effect operation with no handler in its dynamic extent** traps
  and exits with status **71**. See [Effects](reference.md#effects) in
  the reference.
- **`MM-VAL-3b`**: `INT_MIN / -1` writes `axiom: division overflow`
  to fd 2 and exits with status **83**, and a left or right shift by
  an amount below 0 or above 63 writes `axiom: shift amount out of
  range` and exits **84**. Until 0.8.0 these cases were undefined:
  their answers changed with `--opt`, and nothing trapped.

`ERR-REC-2` gives division, remainder and the trapped cases a
value-returning alternative you can call instead. `ERR-REC-6` lets a
program that didn't call one contain the trap at an arena mark, instead
of dying of it. The runtime has other traps, such as running out of
memory (70) and an index out of range (77). `MM-EXEC-16` in the memory
model lists every reserved exit status.

---

## 2. The canonical types

**ERR-TYPE-1 (H). The failure type is `Result`, a two-parameter sum.**
It ships in `stdlib/Err.ax`:

```scheme
(pub data Result (a e)
  (Ok a)
  (Err e))
```

The constructors' types are `(a -> Result a e)` and `(e -> Result a e)`.
The success parameter comes first because that's the order you read the
computation in: `Result Int IoErr` is "an `Int`, or an `IoErr`".

**ERR-TYPE-2 (H). `Result` ships as an ordinary declaration in
`stdlib/`, not as a built-in.** A user-declared two-parameter ADT
already does everything a built-in would:

- It checks: `(data Res (a e) (Good a) (Bad e))` passes `check`. The
  type parameters are **one** group, `(a e)`. Written `(a) (e)`, the
  second group reads as a constructor named `e`. The declaration still
  checks, but a `match` over the type fails with a confusing
  non-exhaustive-match error (`AX3005`, "missing e").
- It is pure to inspect (`ERR-PROP-2`).
- It is classified as a reference and reclaimed at a release boundary,
  even when applied polymorphically (`ERR-MEM-3`).

A built-in would mean changing `self_host/typecheck.ax`, and so a seed
rebuild with `scripts/reseed.sh`, on a compiler whose bootstrap
fixpoint is a v1 exit criterion. Nothing measured justifies that cost
before the model has users. Making `Result` built in is deferred, not
refused. What would trigger it is a measured cost of writing the
`(import ...)`, not a preference.

**ERR-TYPE-3 (H). The canonical error payload is a concrete record, and
conversion between error types is explicit.**

```scheme
(pub struct Error
  (code : Int)          ; a stable AXERR number, never reused
  (message : String)    ; what happened, no trailing punctuation
  (context : String))   ; what the caller was doing, "" when none
```

`code` holds an errno when the kernel refused a call, and one of the
standard library's own codes otherwise. The two ranges never meet, so
a code always says which kind it is:

| Codes | Where they come from |
|---|---|
| 1–999 | An errno, as the kernel answered it. The largest on a supported target is Linux's 133. |
| 1001–1099 | `Sys`, `Task`, `Sync` and `Err`. `errDivideByZero` is 1006, `errOverflow` 1007, `errShiftTooWide` 1008 and `errShortWrite` 1009. |
| 1100–1199 | `Crypto` |
| 1200–1299 | `Chrono` |
| 1300–1399 | `Axqlite` |
| 1400–1499 | `Float` |

A code is never renumbered or reused within its range. Your program's
own codes are clear of both from 2000 up. Tested by
`tests/stdlib/373-err-code-ranges.ax`.

In Rust, `From<E>` lets `?` convert an error on the way out. Axiom has
no such mechanism, by design. A conversion is an ordinary value the
caller supplies, and nothing searches for one:

```scheme
(import Err)
(import IO)

(struct ConvertOf (a b)
  (convert : (-> a b)))

(:: mapErrWith (-> (ConvertOf e f) (Result a e) (Result a f)))
(fn (mapErrWith c r)
  (match r
    ((Ok x) (Ok x))
    ((Err y) (Err ((c.convert) y)))))       ; dispatch is application

(:: errOfInt (-> Int Error))
(fn (errOfInt n) (mkError n "low-level failure"))

(:: main Int)
;@axiom:effect(io)
(fn (main)
  (match (mapErrWith (ConvertOf errOfInt) (Err 5))
    ((Ok x) x)
    ((Err e) { (println (errorText e)) (errCode e) })))
```

It prints this and exits with status 5, the error's code:

```text
low-level failure
```

What Axiom lacks is *instance search*: Rust's compiler finds the
`From` instance from the types at the call site. In Axiom, dispatch is
application, and an instance is a value someone wrote down. The checker
does resolve a format hole from its argument's static type, but that is
one built-in form rewriting to a known renderer, not a search over
declared instances. So a conversion is a function the program calls
(`mapErr`) or a record it passes, never an instance the compiler
supplies. The two-parameter trait this replaces is blocker `B2` in §8,
resolved by removal in 0.6.0.

**ERR-TYPE-3a (retired). An error-inspecting combinator MAY read the
error's fields off the `match` binder.** The rule once said MUST NOT.
That was a checker limitation, not a design choice, and the limitation
is gone:

```scheme
(import Err)
(import IO)

(:: reContext (-> (Result a Error) String (Result a Error)))
(fn (reContext r ctx)
  (match r
    ((Ok x) (Ok x))
    ((Err y) (Err (Error y.code y.message ctx)))))

(:: main Int)
;@axiom:effect(io)
(fn (main)
  (match (reContext (divChecked 10 0) "splitting the bill")
    ((Ok n) n)
    ((Err e) { (println (errorText e)) 1 })))
```

It prints this and exits with status 1:

```text
divided by zero while splitting the bill
```

The checker instantiated `(Result a Error)`'s constructor field to a
fresh variable and never resolved it against the signature, so `y` had
no fields: `AX3004 type mismatch: expected struct or data type, found
_a`. `ctorPatEnv` removed the limitation. A constructor pattern's field
types are now instantiated against the scrutinee's before its binders
are bound. §8 (`B5`) records how the rule outlived the limitation.

The number stays burned and is never reused. `stdlib/Err.ax` keeps
`errContextOf`, because it is public, `docs/stdlib-api.md` lists it,
and removing an exported name would break importers for no gain. It is
now a convenience, not a requirement. `tests/stdlib/371-err-module.ax`
term 2 checks the two spellings against each other: changing the field
the direct read uses drops that term and no other (exit 253 against
255).

**ERR-TYPE-4 (H). `Option` is not an error type, and the conversion is
named.** `Option` says *absent*; `Result` says *failed, and here is
why*. `(okOr o e)` and `(toOption r)` convert explicitly, and there is
no implicit coercion in either direction. A missing map key and a
failed syscall are different facts, and the type keeps them apart.

**ERR-TYPE-5 (H). Error payload fields MUST declare their real types.**
Not `Int`. A `String` stored through a field declared `Int` is
invisible to reclamation and leaks, so this is a memory-safety
obligation as much as a type-design one (`ERR-MEM-1`). A reference
cast into such a field, where the checker can see the operand, is
refused with `AX3043`.

---

## 3. Propagation

**ERR-PROP-1 (H). An error is an ordinary value.** It travels through
calls, lambdas, closures, data structures and pattern matching with no
special mechanism, because Axiom has none to offer: no unwinding, no
early return and no exceptions. A function that can fail says so in its
return type, and every caller does something about it or doesn't
compile.

**ERR-PROP-2 (H, amended). Inspecting an error is pure. Constructing
one is not.** A function that only takes a value apart may carry
`;@axiom:effect(pure)`. A function that builds one allocates, and `pure`
refuses it:

```scheme refused
(data Pair (P Int Int) (Nil))

;@axiom:effect(pure)
(:: mkPair (-> Int Pair))
(fn (mkPair n) (P n n))

;@axiom:effect(pure)
(:: first (-> Pair Int))
(fn (first p) (match p ((P a b) a) ((Nil) 0)))

(:: main Int)
(fn (main) (first (mkPair 3)))
```

```text
error[AX3010]: AXTAG mismatch on `mkPair`: `effect(pure)` claim contradicted: body performs Alloc
```

`first` is accepted, and `axiom symbols` reports it as `#effect=pure` with no
`#effects=` beside it. So you can take a `Result` apart inside a
function that claims purity, but you can't build one there.

Counting constructor allocation is what lets `restrict(no-alloc)`
catch a constructor. With constructors left out of the inferred effect
set, as this rule once allowed, a function that built a value checked `OK` while its
IR called `axiom_alloc`. With them counted, the same function is
refused with `AX3049`:

```scheme refused
(data W (Wrap Int) (Empty))

;@axiom:restrict(no-alloc)
(:: mk (-> Int Int))
(fn (mk n) (match (Wrap n) ((Wrap x) x) ((Empty) 0)))

(:: main Int)
(fn (main) (mk 3))
```

`MM-EXEC-9a` in the memory model records the change and the effect
rows it moved.

**`Alloc` is not exempted from `pure`, by decision.** `pure` means an
empty definite effect row. An exemption would have to be carved out for
every reader of that row, not just for this rule, and `Alloc` is the
effect `restrict(no-alloc)` exists to name. So construction allocates,
and a constructing function is not pure. No `;@axiom:effect(pure)` claim in
`stdlib/` or `self_host/` sits on a constructing function.

**ERR-PROP-3 (H, program obligation). In a recursive function, the
fallible call MUST be the `match` scrutinee and the recursive call MUST
be what an arm answers. Never the reverse.**

The rest of the model depends on this rule, and it isn't a matter of
style. Here is the safe shape. The self call is in tail position inside
an arm, so the compiler turns the function into a loop:

```scheme
(fn (total n acc)
  (if (== n 0)
      (Ok acc)
      (match (step n)
        ((Err e) (Err e))
        ((Ok v) (total (- n 1) (+ acc v))))))
```

And the unsafe one. The `match` waits on the self call, so it isn't a
tail call, the loop conversion doesn't fire, and every level holds a
stack frame:

```scheme
(fn (total n acc)
  (if (== n 0)
      (Ok acc)
      (match (total (- n 1) (+ acc 1))
        ((Err e) (Err e))
        ((Ok x) (Ok x)))))
```

At the default `--opt 1` with an 8 MiB stack, the unsafe shape costs
**48 bytes of stack per call** and dies of `SIGSEGV` after about
**174,000** calls. The safe shape completes **5,000,000** iterations
with a flat stack.

The shape a propagation form generates, with the continuation in the
arm, is the shape that converts: `try` (`ERR-SUGAR-2`) expands to
exactly this. So the convenient spelling and the safe one agree.

Gated by `tests/stdlib/370-error-propagation.ax` term 16, with the
scrutinee shape run as its ablation.

**ERR-PROP-4 (H, gated). The compiler warns on a self-recursive call
in the scrutinee of a `match`.** The warning is `AX3045`,
`recursion-in-scrutinee`, and its help names the arm-tail rewrite.

It fires on any call to the enclosing function, at any depth of any
`match` scrutinee in its body, whatever the scrutinee's own type. A
call nested inside a larger scrutinee holds its frame just the same. A
condition on the scrutinee's return type, as first proposed, would
have missed the same hazard one expression up.

It doesn't report:

- a call to any other function, including mutual recursion through a
  scrutinee, which is the same hazard one call away, because the
  checker sees one declaration at a time;
- a name that the match's own scope binds;
- a scrutinee that didn't check cleanly.

Shallow recursion is correct, so this is a warning. It costs a line of
output and no build, where an error would refuse working programs.

Gated by `tests/diagnostics/1005-recursion-in-scrutinee.ax`, which
expects two warnings, for the bare call and for one nested deeper. The
arm-tail shape, a call to another function, shadowing spellings and a
poisoned scrutinee all stay silent. `tests/diagnostics/severity.policy`
and `scripts/check-diagnostic-coverage.sh` also hold it.

**ERR-PROP-5 (H). Higher-order propagation carries the callee's
effects, not the error.** A combinator that takes a fallible function,
such as `andThen`, is effect-transparent in that parameter, and
`axiom symbols` reports it with `#effect-params=`. Its own row holds
only what its body does, which is `Alloc` when it builds a `Result`
(`ERR-PROP-2`). No rule of this model changes effect inference.

---

## 4. Memory

The rules in this section are obligations on the design. They say what
gets reclaimed, and what your program must do so a loop that carries
errors runs in constant memory.

**ERR-MEM-1 (H). A payload field's declared type decides whether its
contents are reclaimed.** `fldClass` in `self_host/codegen.ax` builds a
block's reference map from its declared field types:

- *reference*: `String`, a declared `data` or `struct` type, a tuple
  and an arrow;
- *scalar*: the `Int`/`Float`/`Bool`/`Char` family;
- *unclassifiable*: a type variable, a `Ptr` or an alias. One
  unclassifiable field forces the whole block to an empty map.

A `String` stored through a field declared `Int` is invisible to
release, so it leaks. That is why `ERR-TYPE-5` exists: an error record
that declares `(message : Int)` and casts a `String` into it leaks.

**ERR-MEM-2 (H, program obligation). An error value handed to a self
tail call MUST pass through a `let` binding.**

```scheme
; from tests/stdlib/370-error-propagation.ax: bind the error, then pass it
(fn (carry r n)
  (if (<= n 0)
    0
    (let ((next (Bad (wide n))))
      (carry next (- n 1)))))
```

A `data` block is born owned, at count 1. Built inline in a tail-call
argument, the boundary retain (`MM-LIFE-2c` event 4) takes it to 2,
and the single boundary release brings it back to 1. It never reaches
0, so it is never reclaimed. Bound to a `let` first, the frame's scope
release spends the birth count and the loop stays flat.

This measurement set the rule: 2000 iterations, each allocating a fresh
32-byte `String` inside the error value.

| The value is… | bump moves |
|---|---|
| constructed inline in the tail-call argument | 288,176 bytes |
| returned from a function, passed inline | 288,176 bytes |
| **bound to a `let`, then passed** | **176 bytes** |

That is 144 bytes leaked per iteration, in the two spellings people
write first. `tests/stdlib/370-error-propagation.ax` term 4 holds the
`let`-bound spelling flat.

The current compiler no longer leaks on the inline spellings: they
stay flat over the same 2000 iterations too. So removing the `let` is
no longer an ablation of term 4, and term 64's retained allocation is
what shows the instrument can see growth. Nothing in
`tests/stdlib/370-error-propagation.ax` holds the inline spellings
flat, so the rule stands. A conforming implementation
**SHOULD** make it unnecessary by spending the birth count at the
boundary.

**ERR-MEM-3 (H). A polymorphic `Result` applied at concrete arguments
is classified and reclaimed.** `fldClass` classifies an applied type by
its head, so this needed its own probe beside `ERR-MEM-2`'s monomorphic
one. The same loop over 2000 `let`-bound iterations moves the bump 176
bytes monomorphic and 288 bytes polymorphic, at `(Result Int String)`.
Both are flat, so the model works polymorphically, which is the only
way it is worth having. `370-error-propagation.ax` term 4 runs over a
polymorphic error type.

**ERR-MEM-4 (H). The block a fallible call returns is reclaimed.** A
call answering a `Result` allocates one block. It is released after the
`match`, whether you match the call directly or bind it first:

```scheme
(match (step i) ((Ok v) v) ((Err e) 0))

(let ((r (step i)))
  (match r ((Ok v) v) ((Err e) 0)))
```

A `match` scrutinee is released after the merge only when no arm's
binder escapes through that arm's body (`scrutineeReleasable` and
`escapesViaBinders` in `self_host/codegen.ax`). A binder that reaches
its arm's value, bare or through arithmetic, counts as an escape unless
it is a machine scalar. A scalar can't alias the block it was copied
out of. A field read can be classified where it stands
(`fieldReadIsScalar`), but a match binder is a bare variable by the
time the escape walk reaches it. So the checker records the binder's
type.

The declared type isn't enough. `Ok`'s field is declared `a`, and only
the match site knows that `(step i)` at `(-> Int (Result Int String))`
makes it an `Int`. So the checker records the *instantiated* type. In
`bindOnePatArg`, where a constructor pattern's binders are bound at
their instantiated field types, `stampPatBinderTy` writes the type
constructor's name onto the binder's own node. Codegen's
`binderIsScalar` classifies that name with `scalarTyName`, the same
list `fldClass` uses for a declared field. A binder the checker could
not resolve is left unstamped, which reads as "assume it can alias".

Three details of the stamp:

- It lives in a twelfth word on `ASTNode`, not in word 6. Word 6
  already carries an evidence stamp for a call's spine head, which is
  also a `TAG_E_VAR` node, and a type name there would be read as a
  node.
- It is a name, not a type node. The unifier writes through type nodes
  in place, so a pointer kept across phases can be overwritten.
  `stampFieldStruct` records a name for the same reason.
- The escape walk has its own binder collection, `patBindersEsc`,
  instead of a flag on the shared one. The five other callers of
  `patBindersCg` want the binders as a scope, for shadowing and for
  the flow environment, and a scalar binder is still a binding. Only
  the escape question treats it differently.

The stamp has three states: `0` for never stamped, a name when every
check that reached the node agreed, and the empty string when two
checks disagreed. The empty string reads back as conservative.
Last-write-wins would be unsafe: a binder node checked at two types
could hand codegen an `Int` for a binder that is really a `String`, and
codegen would then release a block the binder still points into.

Trait default bodies once reached that state. `checkImplComplete`
shared one default body's nodes across every impl that omitted the
method, so two impls at two types checked one AST, and the emitted IR
released a block a returned `String` still lived in. The same sharing
caused `MM-LIFE-2j` in [memory-model.md](memory-model.md). Traits were
removed in 0.6.0, and the path went with them. A compiler built with
last-write-wins stamps now emits byte-identical IR across 278 fixtures,
every `stdlib/` module and `self_host/main.ax`, so nothing Axiom can
express checks one pattern binder twice at two types. The disagreement
arm stays as a guard against that class of mistake, and
`scripts/check-fallible-reclaim.sh` asserts that nothing reaches it.

Without the stamp, every fallible call leaks its 32-byte block. A
compiler that runs once can survive a cost linear in fallible calls.
The LSP, which runs per keystroke, and the pre-forked server, which
runs per request, can't. `ERR-ADOPT-3`'s workaround for that cost is
no longer needed.

`tests/stdlib/370-error-propagation.ax` holds the rule with three
terms:

- term 32 asserts the reclamation over 20,000 calls, 10,000 in each
  spelling;
- term 8 runs the same loop with no `Result` in it, so a difference is
  attributed to the error value and not to the loop;
- term 64 requires a retained allocation to move the same probe pair,
  because flat lines alone can't tell reclamation from a blind
  instrument.

`scripts/check-fallible-reclaim.sh` rebuilds the compiler with
`binderIsScalar` ablated and requires the fixture to fail at term 32
and no other. Ablated, it exits 95 instead of 127, and the probe delta
returns to 640,032 bytes over 20,000 calls: this rule's 32 bytes a
call, to the byte.

**ERR-MEM-5 (H). An error record may declare at most 46 payload
words.** `AX3029` refuses a wider block: the reference map covers 47
payload words, and a data cell spends one on its tag. `AX3030` caps a
declaration at 64 type variables. `Error` as specified declares 3
fields, far below the limit. The rule exists so a future cause chain
doesn't walk into it.

**ERR-MEM-6 (R). The model does not use linear types, and neither does
the language.** `linear` and `consume` parsed but enforced nothing: no
use was counted, so a value could be used twice or not at all.
`Linear T` was a real nominal barrier (`AX3004` against `T`), enough to
keep a wrapper distinct in a signature but not enough to build
ownership on. Every rule above is correct without linearity, and a
marker that reads as an ownership guarantee but supplies none is worse
than no marker. So the keywords are refused instead of reserved: they report
`AX2004` with migration advice, alongside `union`, `foreign` and
`deriving`. `region` was once in that list and is now a checked scope
([Regions](reference.md#regions)).

`MM-LIFE-7`, if it lands, would add two things to this model and change
none of its rules: an error value could **move** into a callee without
retain/release, and a `Result` discarded on a branch could be dropped
early instead of at scope end. Both would optimise `ERR-MEM-2` and
`ERR-MEM-4`, not replace them.

---

## 5. Recovery

**ERR-REC-1 (R). There is no unwinding, no early return and no
exception, and the model does not add one.** Recovery is a value
arriving at a `match`. The runtime leaves no other option.
`ERR-REC-6` describes the one narrow exception, and why it is not the
general mechanism this rule refuses.

General unwinding is refused for three reasons, each measured:

- Every call would become a two-destination `invoke`, so the emitter
  would need to know the enclosing landing pad. An `invoke` in argument
  position splits a block underneath a `phi` that the emitter builds
  from "whichever block actually reaches the merge", because there is
  no block graph to ask.
- A cleanup pad must release pending values at an arbitrary point.
  That needs liveness, which needs a control-flow graph, and there
  isn't one.
- The unwinder is a hosted link: 188 undefined symbols for every
  program, not only the ones that use it.

**ERR-REC-2 (H). Every trap gets a value-returning alternative; the
raw operator keeps its semantics.** `stdlib/Err.ax` ships them:

```scheme
(import Err)
(import IO)

(:: report (-> (Result Int Error) Int))
;@axiom:effect(io)
(fn (report r)
  (match r
    ((Ok q) (println "quotient {q}"))
    ((Err e) (println (errorText e)))))

(:: main Int)
;@axiom:effect(io)
(fn (main)
  {
    (report (divChecked 10 2))
    (report (divChecked 10 0))
    0
  })
```

```text
quotient 5
divided by zero
```

| The trap | Alternative | Answers |
|---|---|---|
| `(/ a 0)`: fd 2, exit 72 | `(divChecked a b)` | `(Err DivideByZero)` |
| `(% a 0)`: the same | `(remChecked a b)` | `(Err DivideByZero)` |
| `INT_MIN / -1`: fd 2, exit 83 | `divChecked` | `(Err Overflow)` |
| `(<< 1 100)`, `(>> x 64)`: fd 2, exit 84 | `shlChecked`, `shrChecked` | `(Err ShiftTooWide)` |

A checked operator is a different function with a different type, so
no existing program changes meaning, and no hot loop pays for a check
it didn't ask for. The raw cases trap instead of answering (`MM-VAL-3b`):
name the sharp edge instead of silently rounding it off. Until 0.8.0
the last two rows were undefined and `--opt`-dependent; the guard is
what ended that, and the checked operators are unchanged by it.

All four are pinned by `tests/stdlib/371-err-module.ax`. Term 64 pins
`divChecked`'s zero case, term 32 its `INT_MIN / -1` guard together
with `shlChecked` refusing a shift of 100, and term 128 the codes
`remChecked` and `shrChecked` raise.

**ERR-REC-3 (R). Effects are not an error channel.** `handle` is
evidence-passing and **tail-resumptive**: the handler's return value is
the operation's result, and execution continues where the operation was
performed. A handler **cannot abort the computation it handles**,
because there is no mechanism for it to do so. An operation with no
handler traps and exits 71 instead of answering. So an `effect` can't
express "stop, unwind, recover", and a program that models failure as
an effect gets a trap where it wanted a `catch`. Use `effect` for
*capabilities* and `Result` for *failure*.

One capability does look like an error channel: asking the caller what
to do with a malformed record, then carrying on with the answer. That
is `ERR-REC-7`, shipped as `stdlib/Fallible.ax`. The handler decides
per record and cannot abort, and the loop it serves never learns that
anything happened.

**ERR-REC-4 (H, gated). `main` renders an error and exits with a code
reserved for the purpose.**

```scheme
(import Err)

(:: main (Result Int Error))
(fn (main)
  (withContext (Err (mkError 7 "disk full")) "saving records"))
```

This prints to fd 2 and exits with status 70:

```text
axiom: disk full while saving records
```

A `main` answering `(Result Int Error)` writes `axiom: {message}`, plus
the `context` when it is non-empty, to fd 2 and exits **70**. An `Ok`
payload is the exit status. 70 sits next to two statuses the runtime
already owns: 71 is the unhandled-operation trap and 72 the division
trap. Exit codes 1–69 stay your program's own. The
dispatch understands exactly `(Result Int Error)` with `errorText` in
scope, and any other `main` shape behaves as before. The reference
shows it in use under [Fallible main](reference.md#fallible-main).

Tested by `tests/stdlib/490-main-result-ok.ax` (an `Ok` payload answers
as the status) and `tests/stdlib/491-main-result-err.ax` (the sentence
on fd 2 via `NAME.err`, status 70 via `NAME.exit`). Like every stdlib
golden, `scripts/check-stdlib-selfhost.sh` runs them at `--opt` 0 and
2.

**ERR-REC-6 (H). A trap may be contained, and only a trap.**
`(__axiom_recover mark thunk)` arms a **recovery point** at an arena
mark and runs `thunk`. A trap listed in the table below, raised inside
it, answers the *arming call* with its status instead of writing to
fd 2 and exiting:

```scheme
(import IO)

(:: divide (-> Int Int Int))
(fn (divide a b)
  (/ a b))

(:: main Int)
;@axiom:effect(io)
(fn (main)
  (let ((status
    (__axiom_recover
      __axiom_arena_mark
      (lambda (x) (divide 10 0)))))
    {
      (println "recovered {status}")
      (divide 10 0)
    }))
```

```text
recovered 72
```

The second division runs outside any recovery point, so it ends the
program: `axiom: division by zero` on fd 2, and exit status 72.

| Inside a recovery point | Outside one |
|---|---|
| out of memory answers **70** | `axiom: out of memory (mmap failed)`, exit 70 |
| a reference count at its maximum answers **70** | `axiom: reference count limit exceeded`, exit 70 (`MM-LIFE-2l`, `tests/stdlib/527-retain-overflow.ax`, both halves at `--opt` 0-3) |
| an unhandled effect answers **71** | `axiom: unhandled effect`, exit 71 |
| division by zero answers **72** | `axiom: division by zero`, exit 72 |
| division overflow answers **83** | `axiom: division overflow`, exit 83 (`tests/stdlib/695-intmin-div-trap.ax`, both halves at `--opt` 0-3) |
| a shift amount out of range answers **84** | `axiom: shift amount out of range`, exit 84 (`tests/stdlib/696-shift-wide-trap.ax`, both halves at `--opt` 0-3) |
| an index out of range answers **77** | `axiom: vector index out of range`, exit 77 (`tests/stdlib/525-vec-set-bounds.ax`) |
| a violated contract answers **80** | ``axiom: precondition failed in `half`: (> n 0)``, exit 80 |
| a `parallel` spawn the kernel refused answers **78** | `axiom: parallel: could not spawn the binding`, exit 78 (emitted, not yet executed) |
| `parallel` on a target with no lowering answers **79** | `axiom: parallel is not available on this target`, exit 79 (windows-x86_64, windows-aarch64; emitted, not executed) |
| a `__syscallN` on a target with no syscall ABI answers **74** | `axiom: no syscall ABI on this target`, exit 74 (windows-x86_64, windows-aarch64; emitted, not executed) |

Out of memory, an unhandled effect and division by zero each have both
halves in one program, at four optimisation levels:
`tests/stdlib/401-recover-effect.ax`, `402-recover-oom.ax` and
`403-recover-div.ax`, gated by `scripts/check-recover.sh`. Division
overflow and an out-of-range shift are the same shape:
`tests/stdlib/695-intmin-div-trap.ax` and
`tests/stdlib/696-shift-wide-trap.ax`, each with `.optstable`, under
the stdlib runner rather than `check-recover.sh`, which would only
re-assert what those runs already pin.

The contract row covers `;@axiom:pre(...)` and `post(...)`. A violated
contract is programmer error in the same sense as a division by zero:
the arena is intact, nothing half-wrote a structure, and a process that
armed a recovery point asked to survive exactly this. So
`@__axiom_contract_fail` calls `__axiom_recover_abort` first, as the
division trap does. Section 1 of `scripts/check-contracts.sh` holds both
halves in one program: the arming call answers `recovered 80` on
stdout, and a second violation outside every recovery point exits 80
with the sentence on fd 2. Both halves share one program for the reason
`403-recover-div.ax` gives: with `__axiom_recover` unreferenced, the
mechanism is dead code and the armed test folds to false.

Two traps are not in the table, 75 and 76, because each fires inside an
arena reset, and a recovery point's abort *is* an arena reset to the
arming mark:

- 75 (`MM-ALLOC-16a`) fires when a reset is handed a mark whose chunk
  is no longer on the active list. By then the reset's walk has pushed
  every chunk it passed onto the free list while hunting for the
  missing one. That is the list an abort would reset through, so
  answering with another reset asks the corrupted structure the same
  question.
- 76 (`MM-ALLOC-16b`) fires when a reset would reclaim the evidence
  record a live `handle` still dispatches through. An abort's whole job
  is restoring evidence slots, so running it to answer "a slot points
  into memory this reset is about to reclaim" repeats the fault. The
  abort restores every slot *before* it resets, so its own reset never
  trips this check.

The line is drawn on whose invariant broke, not on how bad it sounds.
The traps in the table are conditions a program can be written to
survive. A violated contract, for one, is the programmer's own sentence
about their own values. 75 and 76 are violated implementation invariants
(`I8`, and `MM-ALLOC-16b`'s evidence-record extent), which sit nearer
the memory-safety faults this rule refuses to contain.

A recovery point is not unwinding, a `catch` or an early return, so
`ERR-REC-1` stands for everything outside the table. There is no
landing pad. The one code that runs on the way out is the cleanup of
the resource owners the extent made, such as a file's close or a mutex
guard's release ([memory model](memory-model.md) `MM-EXEC-20`). The point
is wherever `__axiom_recover` was called: a program can't place it at a
frame of its choosing, and a recovered extent can't be resumed. It does
**not** contain a memory-safety fault either. A SIGSEGV is not a trap,
nothing asks the recovery point, and afterwards the heap invariants are
unknown. Java, Go and Rust all abort there too.

This narrow version is sound because there is little to unwind. Axiom
has no destructors beyond those owners' cleanups, no finalizers and no
stack-allocated data, so "unwinding" reduces to running those cleanups
and restoring the stack pointer, the arena and the effect slots.

Two things stay yours. The abort reclaims everything the thunk
allocated, so a structure older than the point must not be made to hold
any of it: return the thunk's answer through the arming call and store
it afterwards. And a descriptor, mapping or lock the thunk took stays
taken, because nothing runs on the way out. `MM-ALLOC-23` in the memory
model states both, and `tests/stdlib/561-failed-operations.ax` shows the
safe shape. `MM-ALLOC-23` in the memory model gives the memory
argument, including the one cost it doesn't avoid: a retain abandoned
below the mark. The measurement bounds it: 100,000 aborts hold max RSS
at 1,376 KiB, against 419,328 KiB for the same program with nothing to
recover from.

Failure divides into three classes, and a recovery point serves only
one of them:

- *Expected* failure, such as bad input, a missing file or a timeout,
  is `Result` (`ERR-TYPE-1`).
- A *memory-safety fault* can't be contained by any language, because
  afterwards the heap invariants are unknown.
- *Programmer error*, such as out of memory, an unhandled effect or a
  division by zero, sits between them. It is the only class an
  in-process abort can serve.

So a worker in a pre-forked pool that divides by zero on one request no
longer takes the process with it. The request boundary is already an
arena scope (`MM-ALLOC-22`), and the recovery point is the same
boundary answering a status. A recovery point doesn't replace `Result`:
`ERR-REC-5`'s obligation applies to a recovered status exactly as it
does to an `Err` arriving at a `match`.

`axiom test` is built on it ([Testing](reference.md#testing)). A test
runner needs exactly what this mechanism gives: one failure ends one
unit of work, and the process carries on. `axiom test` arms one
recovery point per test and reports the status it answers with.
`stdlib/Test.ax` makes a failed assertion an unhandled operation of an
`Assert` effect, so it answers 71 through the row above, with no new
machinery. `tests/testrunner/mixed-tests.ax` fails in three of these
ways and still reports the test declared after all three
(`scripts/check-test-runner.sh`).

A program that never arms a recovery point pays nothing. The
mechanism's only mutable state is a single global that only the arm
site writes. With no arm site anywhere, `opt` folds the load in the
abort to the initialiser, deletes the global, deletes the three calls
the traps make, and folds all three functions to `ret i64 0`.
`scripts/check-recover.sh` asserts those three properties separately at
`opt -O1` instead of counting lines, because P1's symbol table takes
the address of every function a module defines, and so keeps three
names alive in every program either way.

**ERR-REC-7 (H, gated). A batch loop's malformed record is a question
for the loop's handler, answered where it arises, and the answer costs
the record nothing.**

```scheme
(import IO)
(import Fallible)

; Deep in the batch: report a bad record and carry on with the answer.
(:: parseRecord (-> Int Int))
(fn (parseRecord i)
  (if (== (% i 7) 0)
    (fallibleMalformed "record malformed")
    i))

(:: total (-> Int Int))
(fn (total n)
  (let (
    (mut i 1)
    (mut acc 0)
  )
    {
      (while (<= i n)
        (let ((v (parseRecord i)))
          {
            (set acc (if (fallibleIsSkipped v)
              acc
              (+ acc v)))
            (set i (+ i 1))
          }))
      acc
    }))

(:: main Int)
;@axiom:effect(io)
(fn (main)
  {
    (println (cast Int (handle (total 20) (Fallible) fallibleSkip)))
    (println (cast Int (handle (total 20) (Fallible) (fallibleDefault 100))))
    0
  })
```

```text
189
389
```

`stdlib/Fallible.ax` declares one effect with one operation,
`(fallibleMalformed message)`, which answers an `Int`. A callee at any
depth below the loop performs it on a record it can't parse, and
continues with whatever the innermost handler answers. Three handlers
ship, each a value you hand to `handle`:

- `fallibleSkip` answers `fallibleSkipped`, the most negative `Int`,
  which the loop checks once per record with `fallibleIsSkipped`;
- `(fallibleDefault d)` answers `d`;
- `(fallibleCounting tally next)` counts in `tally` and answers as
  `next` would.

Nesting shadows and restores as `handle` always does: an inner
`fallibleSkip` wins while its `handle` is live, and the outer
`(fallibleDefault 100)` answers again after it exits.
`tests/stdlib/410-fallible.ax` pins all of that line by line.

This rule leaves `ERR-REC-3` as it is: the handler cannot abort. An
operation performed with no handler is the unhandled-effect row of
`ERR-REC-6`: 71 to a recovery point, and `axiom: unhandled effect` with
exit 71 outside one. A `main` that reaches `fallibleMalformed` with no
handler is refused with `AX3053` at compile time
(`tests/diagnostics/389-unhandled-at-main.ax`).

*The cost is the rule.* A batch loop has no request boundary, so there
is no arena reset (`MM-ALLOC-22`), and a byte per record is a byte the
process keeps for the whole run. The operation's shape was chosen by
measuring the alternatives with the arena mark cell, the instrument
`370-error-propagation.ax` uses, over 10,000 records of which 1,428
performed the operation:

| operation shape | bytes per operation, when the shape was chosen | now |
|---|---|---|
| one argument, a literal message | 0 | 0 |
| one argument, an `Int` | 0 | 0 |
| two arguments, `(op message fallback)` | 32: the curried handler's inner closure, which the dispatch did not release | 0 |
| one argument, a message built per record | 80: the string, because an application through a closure did not release its owned argument | 0 |

So the operation takes one argument, and the fallback comes from the
handler: `(fallibleDefault d)` allocates its closure once, at the
`handle`. A program **MUST** pass a literal, or a value it already
holds, never a message built for the record: the loop knows which
record it is on. `410`'s memory terms hold the three handlers at 0
bytes per record, and require a handler that keeps a string per record
to move the same instrument.

`scripts/check-steady-state.sh`'s `batch` probe checks resident memory:
2,000,000 records under `fallibleSkip` hold **1,376 KiB**, the same as
200,000, and its `keeping` twin must grow past 5×. The same gate builds
`examples/batch-fallible/batch-fallible.ax`, the same loop reading
every record as text, and holds it to the same band at 100,000 and
1,000,000 records.

*Why a sentinel and not `Option`.* `(Some v)` on every well-formed
record is a block per record, allocated and released a million times,
where a sentinel is one comparison. A `handle` expression's own type is
also the checker's wildcard, so an `Option` would arrive through a
`cast` at every site. If your records can hold the most negative `Int`,
write a one-line handler that answers your own sentinel.

*What this does not change.* `ERR-REC-3` stands: the handler answers,
it does not unwind. The two costs in the table were facts about closure
application, not defects in this module. A compiler that releases the
closure and the string makes both spellings free, and this rule's
obligation then shrinks to a preference. The current compiler does both:
`tests/stdlib/410-fallible.ax` term `e` holds the built message flat,
and `scripts/check-closure-reclaim.sh` ablates the closure release and
requires the cost back.

**ERR-REC-5 (P, program obligation). A recovered error MUST NOT be
discarded silently.** `(match r ((Ok x) x) ((Err _) 0))` compiles, and
it is sometimes right. It is also how a migration away from sentinel
values brings back the problem it set out to fix. The compiler can't
tell the two apart, so the obligation falls on the program and its
review. `ERR-DIAG-2` proposes the lint that would make it visible.

---

## 6. Diagnostics

**ERR-DIAG-1 (H). Every diagnostic this model adds goes through
`mkDiag`/`mkDiagFix` at the site that detects the condition, carries a
stable code and a kebab-case slug, and has long-form text in
`self_host/explain.ax`.** Error handling doesn't change how the
compiler reports. This rule keeps it that way, so no one adds a second
channel for "error-model errors".

**ERR-DIAG-2 (P). Proposed codes.** This section reserves numbers for
diagnostics this model needs and the compiler doesn't build yet. The
rest of the compiler keeps growing, so it can spend a reserved number
first. Three rules keep the numbering straight:

- A proposal renumbers when the compiler spends its number first.
- A new code takes the next number above the highest one in use, the
  *free end*, and never a number from the reserved block.
- A retired number **MUST NOT** be reused. `AX3008`, `AX3032`,
  `AX3075`, `AX3079` and `AX3080` are retired. `AX3081`, `AX3082`,
  `AX3083`, `AX3087` and `AX3088` were skipped and never assigned, and
  the same rule covers them.

No codes are proposed right now. When one is, it goes in this table:

| Proposed | Slug | Condition |
|---|---|---|

Before a proposed code is built, it needs:

- a construction site;
- `explain.ax` text;
- a `tests/diagnostics/` case with `.axdl`, `.human` and `.json`
  goldens, blessed by `AXIOM_BLESS=1 scripts/check-diagnostics.sh NNN`;
- a run of that case against a compiler built before the change, to
  show the case isn't vacuous.

`scripts/check-doc-drift.sh` fails when a row in the table above
proposes a number the compiler already constructs. It also requires the
codes the compiler constructs and the codes `explain.ax` explains to be
the same set, so explaining a code before it is built turns the gate
red.

This section records the codes below. All of them are built, and
`axiom explain` gives the long form of each.

| Code | What it reports | Notes |
|---|---|---|
| `AX3035` `macro-binder-target` | a macro argument in a binder position that is not a name | The expander defect this model ran into (§7, `B1`). |
| `AX3036` `extern-type` | an `extern` item whose type can't cross the boundary | |
| `AX3037` `axtag-unverifiable` | an `effect(pure)` claim over a call the effect walk can't resolve | A warning. |
| `AX3038` `effect-unverifiable` | the same condition under a `handle` | A warning. |
| `AX3039` `axtag-key-typo` | an AXTAG key one slip away from a key the compiler checks: a change of case, one edit, or two neighbours swapped | `tests/diagnostics/1134-axtag-near-miss.ax` |
| `AX3040` `result-only-tyvar` | a type variable the caller chooses and the callee produces | The slug names the shape it was built for and stays as the machine key. The rule also covers a function-typed parameter's own variable. |
| `AX3041` `extern-library-name` | an `extern` block's library name that isn't one | Reported by the parser. |
| `AX3042` `undeclared-effect` | a function that performs a required effect (`IO`, `Entropy`, `Spawn` or `Block`) and doesn't declare it | |
| `AX3043` `error-payload-untyped` | a reference smuggled through a field declared `Int` | `tests/diagnostics/1008-error-payload-untyped.ax` |
| `AX3044` `ambiguous-type` | a bare type name declared in more than one imported module | Reported by the namespace pass. |
| `AX3045` `recursion-in-scrutinee` | a self-recursive call in the scrutinee of a `match` | A warning. `ERR-PROP-4` |
| `AX3046` `discarded-result` | a `Result`-typed expression in statement position, its value unused | An error. A deliberate discard binds it to `_`. `tests/diagnostics/1006-discarded-result.ax` |
| `AX3047` `sized-integer-type` | a C or Rust primitive spelling in type position | Without it, the name is read as a type variable and silently accepted. |
| `AX3048` `deprecated-name` | a reference to a name its declaration marks `;@axiom:deprecated` | A warning by design. |
| `AX3049` `restriction-violated` | a `restrict(...)` claim the declaration breaks | An error. |
| `AX3050` `contract-malformed` | a `;@axiom:pre(...)` or `post(...)` that can't be compiled into a check | An error. See below. |
| `AX3051` `restriction-unverifiable` | a `restrict(...)` claim the effect walk can't check | A warning. |
| `AX3052` `restriction-unknown` | a name inside `restrict(...)` that isn't a restriction | An error. |
| `AX3053` `unhandled-operation` | a custom effect still in `main`'s effect row when inference finishes | An error. See below. |
| `AX3054` `effect-name-reserved` | an `effect` declared with a built-in effect's name | An error with no warning stage. See below. |
| `AX3055` `effect-op-untyped` | an effect operation that declares no type | An error, because the handler check and the call's arity check both depend on that arrow. |
| `AX3056` `struct-field-untyped` | a struct field whose type the parser couldn't read | An error. See below. |
| `AX3067` `struct-arity-mismatch` | a `struct` built with the wrong number of fields | Split from `AX3008`. |
| `AX3068` `nullary-lambda` | a parameterless `lambda` applied | Split from `AX3008`. |
| `AX3069` `sizeof-arity` | a surplus argument to `sizeof` or `alignof` | Split from `AX3008`. |
| `AX3070` `unsafe-field-access` | a field read on a `data` type with a nullary constructor beside a fielded one | Split from `AX3008`. |
| `AX3071` `unretained-store` | a bare `__store64` of a reference-typed value through `cast` | `tests/diagnostics/1002-unretained-store.ax` |
| `AX3072` `addr-nonliteral` | `__addr` of anything but a string literal | `tests/diagnostics/1003-addr-nonliteral.ax` |
| `AX3073` `undeclared-unsafe` | a declaration performs a raw primitive, calls an `extern` or forges a reference without `;@axiom:effect(unsafe)` | An error. `tests/diagnostics/1004-undeclared-unsafe.ax`, `tests/diagnostics/1040-forging-cast.ax`, `tests/diagnostics/1116-extern-unsafe.ax` |
| `AX3089` `signature-needed` | a function with no signature, called from inside its own body or from a declaration above it, before its type is known | An error. `tests/diagnostics/1032-signature-needed.ax` |
| `AX3090` `recover-thunk-unseen` | a recovery point's thunk that isn't a lambda written at the call or a top-level function, so the region check can't walk it | An error. `tests/diagnostics/1033-recover-escape.ax` |
| `AX3091` `inline-asm` | an `asm` form the compiler can't lower: an unknown architecture or operand kind, a second arm for one architecture, a template naming no operand, a second output, or a register an arm can't name | An error, where the form is written. `tests/diagnostics/1044-inline-asm.ax` |
| `AX3095` `precondition-removed` | a `;@axiom:precondition(...)` tag, which the language no longer has | An error wherever the tag stands. `tests/diagnostics/1043-precondition-removed.ax` |
| `AX3116` `result-claim` | a `result(...)` claim the compiler cannot read: a value other than `owned`, or the claim on a function that is not a trusted encapsulation | `tests/diagnostics/1259-result-claim.ax` |
