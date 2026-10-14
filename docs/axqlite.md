# AXQLite

AXQLite is Axiom's embedded database: a single file of typed tables,
read and written in transactions, with nothing to install or run beside
your program. This guide walks through the tasks a program has, from a
first table to recovering after a crash. [axql.md](axql.md) defines
AXQL, its query language.

## Start here

```scheme
(import IO)
(import Err)
(import Axqlite)

(:: main Int)
;@axiom:effect(io)
(fn (main)
  (match (axqOpen "shop.db" openOptions)
    ((Err e) (println (errorText e)))
    ((Ok db)
      (let ((cheap 10))
        {
          (let ((_ (axqRunOn db (axqlCreateTable IF NOT EXISTS items (id INTEGER PRIMARY KEY) (name TEXT NOT NULL) (price INTEGER NOT NULL))))) 0)
          (let ((_ (axqRunOn db (axqlInsert items (name price) (values (text "tea") (int 4)) (values (text "pot") (int 25)) (values (text "cup") (int 6)))))) 0)
          (match (axqQueryOn db (axqlSelect items (name price) (where (< price (int cheap))) (orderBy price)))
            ((Ok rows)
              (for r in rows.rows
                (let (
                  (name (unwrapOr (rowTextNamed r "name") "?"))
                  (price (unwrapOr (rowIntNamed r "price") 0))
                )
                  (println "{name} costs {price}"))))
            ((Err e) (println (errorText e))))
          (let ((_ (axqClose db))) 0)
        }))))
```

```text
tea costs 4
cup costs 6
```

`(import Axqlite)` brings in everything: the connection API, the
`axql` statement macros, and `Row` and `Rows`. `axqlCreateTable`,
`axqlInsert` and `axqlSelect` are checked when the program compiles and
expand to AXQL text with parameters. `(text "tea")` and `(int cheap)`
are values, bound as parameters, and never become part of a statement's
text.

## Open a database

`(axqOpen path opts)` answers `(Result Connection Error)`. A connection
closes when its last owner leaves scope. `axqClose` closes it early and
reports errors. `openOptions` is the usual choice: it creates the file when it's
missing, opens it for reading and writing, and caches 256 pages. Build
an `OpenOptions` yourself for anything else:

```scheme fragment
(axqOpen "shop.db" (OpenOptions false true 64))   ; must exist, read only, 64 pages of cache
```

A missing file without `create` is `axqIoFailed`, and a file that isn't
a database is `axqNotADatabase`. A connection opened read only refuses
every write with `axqReadOnly`. A bigger cache makes big transactions
faster; see [Limits](#limits).

## Model a table

A table is a set of typed rows. Declare what each column holds, what
it must hold, and what it starts as:

```text
CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT NOT NULL)
CREATE TABLE players (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  team LINK teams,
  goals INTEGER NOT NULL DEFAULT 0 CHECK (goals >= 0)
)
```

- `INTEGER PRIMARY KEY` makes `id` the row's rowid, its key.
- `NOT NULL` refuses a row without a value.
- `LINK teams` holds the rowid of a row of `teams`, and every write
  checks that row exists. A query reads through it: `team.name`.
- `DEFAULT 0` fills `goals` when a write leaves it out.
- `CHECK (goals >= 0)` refuses a row it finds false.

`axqlTable` declares a record type for a table, with typed reads and
writes, so a program never touches a column number:

```scheme
(import IO)
(import Err)
(import Axqlite)

(axqlTable Book books (id RowId) (title String) (year OptInt))

(:: main Int)
;@axiom:effect(io)
(fn (main)
  (match (axqOpen "books.db" openOptions)
    ((Err e) (println (errorText e)))
    ((Ok db)
      {
        (let ((_ (axqRunOn db (axqlDropTable IF EXISTS books)))) 0)
        (let ((_ (axqRunOn db createBookTable))) 0)
        (let ((_ (insertBook db (Book 1 "Dune" (Some 1965))))) 0)
        (let ((_ (insertBook db (Book 2 "Untitled" None)))) 0)
        (let ((_ (upsertBook db (Book 2 "Untitled" (Some 2026))))) 0)
        (match (queryBook db selectAllBook)
          ((Ok books)
            (for b in books
              (let (
                (t b.title)
                (y (match b.year ((Some n) (format n)) ((None) "unknown")))
              )
                (println "{t}: {y}"))))
          ((Err e) (println (errorText e))))
        (match (getBook db 1)
          ((Ok (Some b)) (let ((t b.title)) (println "book 1 is {t}")))
          ((Ok (None)) (println "no book 1"))
          ((Err e) (println (errorText e))))
        (let ((_ (axqClose db))) 0)
      })))
```

```text
Dune: 1965
Untitled: 2026
book 1 is Dune
```

For `(axqlTable Book books ...)` it declares:

| Name | What |
|---|---|
| `Book` | a struct with one field per column |
| `createBookTable` | a `Query`: the table's CREATE TABLE |
| `selectAllBook` | a `Query`: every row, columns in field order |
| `(rowToBook row)` | the `Book` a row holds, read by column name |
| `(insertBook db b)` | inserts `b` |
| `(upsertBook db b)` | inserts `b`, or replaces the row with its rowid |
| `(queryBook db q)` | runs `q` and answers each row as a `Book` |
| `(getBook db id)` | the `Book` with rowid `id`, or `None` |
| `(deleteBook db id)` | deletes the row with rowid `id` |

A field's type chooses its column:

| Field type | Column |
|---|---|
| `Int`, `Float`, `String`, `Bytes` | `INTEGER`, `REAL`, `TEXT`, `BLOB`, all `NOT NULL` |
| `OptInt`, `OptFloat`, `OptString`, `OptBytes` | the same, NULL allowed, held as an `Option` |
| `RowId` | an `Int` that is the `INTEGER PRIMARY KEY` |

`queryBook` takes any query whose columns carry the fields' names, such
as `(axqlSelect books * (where (> year (int 1960))))`. A column that is
missing, or holds the wrong type, is an `Err` from `rowToBook`. A table
declared this way has 1 to 16 fields, and no links, defaults or checks;
add those with `ALTER TABLE` or declare the table as text.

Tested by `tests/axqlite/623-macro-table.ax`.

## Change a table

`ALTER TABLE t ADD COLUMN c ...` adds a column and gives every row its
`DEFAULT`; `ALTER TABLE t RENAME TO u` renames a table, and the links
that point at it follow. `axqTables`, `axqColumns` and `axqIndexes`
describe what a database holds:

```scheme fragment
(match (axqColumns db "players")
  ((Ok cols) (for c in cols (let ((n c.name) (t c.columnType)) (println "{n}: {t}"))))
  ((Err e) (println (errorText e))))
```

Each `ColumnInfo` carries the column's `name`, its `columnType`
(`"INTEGER"`, `"REAL"`, `"TEXT"`, `"BLOB"` or `"LINK"`), `notNull`,
`primaryKey`, the table it `link`s to, and its `text` as declared. An
`IndexInfo` carries an index's `name`, whether it is `unique`, and its
`columns`.

Not yet: dropping or renaming a column; see the
[roadmap](roadmap.md#standard-library).

## Write statements

There are two spellings of every statement. The macro forms are the
first choice: a mistake in their shape is a compile error, and a value
can only enter as a parameter. The text form reads the same statements
from a string, for text you build at run time or read from a file.

| Macro | Text |
|---|---|
| `(axqlSelect items (name) (where (= id (int k))))` | `"SELECT name FROM items WHERE id = ?"` |
| `(axqlInsert items (name price) (values (text n) (int p)))` | `"INSERT INTO items (name, price) VALUES (?, ?)"` |
| `(axqlUpsert items (id price) (values (int k) (int p)))` | `"UPSERT INTO items (id, price) VALUES (?, ?)"` |
| `(axqlUpdate items (assign (price (+ price (int 1)))))` | `"UPDATE items SET price = price + ?"` |
| `(axqlDelete items (where (= price (int 0))))` | `"DELETE FROM items WHERE price = ?"` |

A macro form is a `Query`, a struct of the statement's text and its
parameters, and so is `(Query text params)`. Run one with:

- `(axqRunOn db q)` for a statement that returns no rows. It answers
  how many rows the statement changed.
- `(axqQueryOn db q)` for a `SELECT`. It answers every row as `Rows`.
- `(axqQueryEachOn db q f)`, which calls `f` with each row as it is
  produced.
- `(axqQueryOne db q)`, which answers the first row as an `Option`.
- `(axqQueryInt db q)`, for a query of one number such as
  `SELECT count(*) FROM items`. No row is `axqNotFound`.

`(axqExec db text)` runs a statement that takes no parameters.
[axql.md](axql.md#statements-as-axiom-forms) lists the macro forms.

`UPSERT` is how to change part of a row by its key: the columns it lists
change, and the rest stay. A row whose rowid doesn't exist yet is
inserted.

## Query

A `SELECT` reads one table, and through its links the rows they point
at. It can filter, sort, group and work out values:

```text
SELECT name, team.name AS team FROM players WHERE name LIKE 'A%' ORDER BY goals DESC LIMIT 10
SELECT team.name, count(*) AS players, sum(goals) FROM players GROUP BY team.name HAVING count(*) > 1
SELECT upper(name), coalesce(asText(goals), 'none') FROM players WHERE goals BETWEEN 1 AND 9
```

[axql.md](axql.md#expressions) lists the operators and functions, and
[Grouping](axql.md#grouping) the aggregates.

## Read rows

`Rows` holds `columns`, the result's column names, and `rows`, a
`(Vec Row)`. Read a `Row` by column number, from 0, or by name:

| Function | Answers |
|---|---|
| `rowInt`, `rowIntNamed` | an INTEGER |
| `rowReal`, `rowRealNamed` | a REAL, or an INTEGER converted to one |
| `rowText`, `rowTextNamed` | TEXT |
| `rowBlob`, `rowBlobNamed` | a BLOB's bytes |
| `rowIsNull`, `rowIsNullNamed` | whether the column is NULL |
| `rowValue`, `rowValueNamed` | the `Value` itself |

Each answers a `Result`. A column of another type, NULL included, is
`axqType`, and the message names the reader that takes it. A column
number out of range is `axqMisuse`, and a name the result doesn't have
is `axqSchema`, with the names it does have. `rowLen`, `rowName` and
`rowIndex` answer the shape. `axqValueText` writes any `Value` as AXQL
would write it as a literal, which is handy for printing.

A `Value` is `VNull`, `(VInt n)`, `(VReal x)`, `(VText s)` or
`(VBlob b)`, from `Axqlite.Value`.

## Stream rows

`axqQueryEachOn` hands each row to a function as the scan meets it, so
a large result never has to fit in memory at once. The function answers
`(Ok true)` to go on, `(Ok false)` to stop, or an `Err`, which stops the
scan and comes back unchanged:

```scheme
(import IO)
(import Err)
(import Axqlite)

(:: main Int)
;@axiom:effect(io)
(fn (main)
  (match (axqOpen "stream.db" openOptions)
    ((Err e) (println (errorText e)))
    ((Ok db)
      {
        (let ((_ (axqExec db "CREATE TABLE IF NOT EXISTS n (v INTEGER)"))) 0)
        (let ((_ (axqExec db "INSERT INTO n VALUES (1), (2), (3), (4), (5)"))) 0)
        (match (axqQueryEachOn db (axqlSelect n v (orderBy v))
          (lambda (r)
            (let ((v (unwrapOr (rowInt r 0) 0)))
              {
                (println "saw {v}")
                (Ok (< v 3))
              })))
          ((Ok count) (println "called {count} times"))
          ((Err e) (println (errorText e))))
        (let ((_ (axqClose db))) 0)
      })))
```

```text
saw 1
saw 2
saw 3
called 3 times
```

While the scan runs, the function may run other SELECTs on the same
connection. Outside a transaction it may not write, and it may never run
DDL, `BEGIN`, `COMMIT` or `ROLLBACK`, or close the connection: those
answer `axqMisuse`.

## Pass values safely

A macro form takes Axiom values only through its markers: `(int e)`,
`(real e)`, `(text e)`, `(blob e)`, and `(param v)` for a `Value`. Each
becomes a parameter, so whatever the value holds, it is compared and
stored as a value:

```scheme fragment
(axqlSelect users id (where (= name (text input))))   ; SELECT id FROM users WHERE name = ?
```

With `input` set to `' OR 1=1 --`, that query looks for a user with
exactly that name. The text form takes parameters too: `?` for the next
one, `?N` for the Nth, or `:name`, with the values in a `(Vec Value)`.
`(axqParamIndex stmt ":name")` answers where a named one goes.

Tested by `tests/axqlite/602-api-injection.ax` and `tests/axqlite/622-macro-injection.ax`.

## Prepare a statement

`(axqPrepare db text)` checks a statement's syntax and names once, and
answers a `Statement` to run many times:

| Function | Runs |
|---|---|
| `(axqRun stmt params)` | a statement that returns no rows |
| `(axqQuery stmt params)` | a SELECT, answering `Rows` |
| `(axqQueryEach stmt params f)` | a SELECT, calling `f` per row |
| `(axqFinalize stmt)` | nothing: it frees the statement |

`axqParamCount` and `axqStatementText` describe it. A statement parses
its text again each time it runs, against the schema as it is then: if
its table was dropped and created again with other columns, it runs
against the new ones, and if the table is gone it answers `axqSchema`.

Tested by `tests/axqlite/601-api-prepared.ax` and `tests/axqlite/609-api-reprepare.ax`.

## Update in a transaction

Outside a transaction, every statement commits on its own.
`(axqTransaction db f)` runs `f` in one transaction: it commits when `f`
answers `Ok`, and rolls back when it answers `Err`:

```scheme
(import IO)
(import Err)
(import Axqlite)

(:: transfer (-> Connection (Result Int Error)))
;@axiom:effect(io)
(fn (transfer db)
  (let ((a (axqExec db "UPDATE accounts SET balance = balance - 50 WHERE id = 1")?)
        (b (axqExec db "UPDATE accounts SET balance = balance + 50 WHERE id = 2")?))
    (axqErr axqConstraint "changed my mind" "transfer")))

(:: main Int)
;@axiom:effect(io)
(fn (main)
  (match (axqOpen "bank.db" openOptions)
    ((Err e) (println (errorText e)))
    ((Ok db)
      {
        (let ((_ (axqExec db "DROP TABLE IF EXISTS accounts"))) 0)
        (let ((_ (axqExec db "CREATE TABLE accounts (id INTEGER PRIMARY KEY, balance INTEGER NOT NULL)"))) 0)
        (let ((_ (axqExec db "INSERT INTO accounts VALUES (1, 100), (2, 0)"))) 0)
        (match (axqTransaction db transfer)
          ((Ok _) (println "committed"))
          ((Err e) (println (errorText e))))
        (match (axqQueryOn db (axqlSelect accounts balance (orderBy id)))
          ((Ok rows)
            (for r in rows.rows
              (println (unwrapOr (rowInt r 0) -1))))
          ((Err e) (println (errorText e))))
        (let ((_ (axqClose db))) 0)
      })))
```

```text
changed my mind while transfer
100
0
```

`(axqBegin db)` answers a `Transaction` to end with `axqCommit` or
`axqRollback`, and the statements `BEGIN`, `COMMIT` and `ROLLBACK` do
the same from text. Inside a transaction a statement that fails is
undone on its own, and the transaction stays open for you to carry on
or roll back.

`(axqExecBatch db script)` runs a script of statements separated by `;`
as one transaction, all or nothing. It suits a schema migration.

`(axqLastInsertRowid db)` answers the rowid the last `INSERT` or
`UPSERT` gave its last row, and `(axqChanges db)` how many rows the last
`INSERT`, `UPSERT`, `UPDATE` or `DELETE` changed.

Tested by `tests/axqlite/607-api-transaction.ax` and `tests/axqlite/610-api-batch.ax`.

## Index a table

A `CREATE INDEX` keeps a table's rows in the order of some columns, so
a `WHERE` or `ORDER BY` on them reads only the rows it needs:

```text
CREATE INDEX playersByName ON players (name)
CREATE UNIQUE INDEX teamsByName ON teams (name)
```

A `UNIQUE` index also refuses a second row with the same values.
[axql.md](axql.md#how-rows-are-found) says which `WHERE` terms an index
serves.

## Handle errors

Every failure is an `Err` whose `Error` carries one of these codes, a
message that says what failed and what to do, and a context naming the
statement and the function that ran it:

```text
books.author is 99, but authors has no row 99 while running `INSERT INTO books (title, author) VALUES ('Nope', 99)` in axqExec
```

| Code | Meaning | What to do |
|---|---|---|
| `axqBusy` | another connection holds the lock | nothing changed; retry later |
| `axqCorrupt` | the file is damaged | restore from a copy, or from a dump |
| `axqConstraint` | NOT NULL, a CHECK, a link to no row, a taken rowid, or a UNIQUE index | fix the data |
| `axqSyntax` | the statement doesn't parse | the message gives the line and column |
| `axqSchema` | a missing or duplicate table, column or index | fix the name; the message suggests one |
| `axqType` | a value of the wrong type | fix the value or the column, or convert it |
| `axqMisuse` | the API used out of order, or the wrong number of parameters | fix the program |
| `axqReadOnly` | a write on a read-only connection | open it for writing |
| `axqIoFailed` | the operating system refused a read, write, sync or lock | check the file and disk; the message names the call and why |
| `axqTooBig` | a value, key or database larger than supported | store less |
| `axqNotADatabase` | the file isn't an AXQLite database | check the path |
| `axqArithmetic` | INTEGER overflow, division by zero, or a number a function refuses | check the arithmetic |
| `axqNotFound` | `axqQueryInt` found no row | expect the row, or use `axqQueryOne` |

`errCode` answers the code, and `errorText` the whole message. A
statement that fails changes nothing.

Tested by `tests/axqlite/618-api-errors.ax`.

## Recover after a crash

A commit is durable when it answers `Ok`. Before it answers, the pages
it changed are on the disk: AXQLite writes the pages' old contents to a
journal beside the database, `<file>-journal`, syncs it, writes the new
pages, syncs the database, and then retires the journal. On macOS a
sync asks the drive to empty its own cache too.

If the program or the machine stops at any point during a commit, the
next connection to open the file finds the journal and puts the old
pages back. The database is then exactly as it was before the
transaction or exactly as it was after, never a mixture. There is
nothing to run: opening the file recovers it. Keep the journal with the
database: deleting a journal left by a crash loses the recovery.

The tests crash transactions at every write, sync and truncate, in
each way a crash can land. A call fails, a write tears, or the power
goes, keeping the drive's cache, losing every unsynced write, or losing
only the journal's or only the database's. Each crash is reopened and
compared, byte for byte, with the database before and after, and every
recovery is crashed again at each of its own steps.
[axqlite-format.md](axqlite-format.md) specifies the journal and the
recovery.

Tested by `tests/axqlite/201-crash-insert.ax` and `tests/axqlite/204-crash-drop.ax`.

Every durable commit waits for the disk, so many small transactions are
slow. Group writes into one transaction when you can.

A full disk is a failed write like any other: the statement answers
`axqIoFailed`, saying there is no space left, and the database keeps
every transaction that committed before it. Every change needs room in
the journal for the pages it touches, so on a full disk even a
`DELETE` can fail; free space elsewhere first.

## Back up and compact

`(axqDump db)` answers the whole database as an AXQL script: every
table, its rows with their rowids, and its indices. `axqExecBatch` on an
empty database loads it back, in one transaction:

```scheme fragment
(match (axqDump db)
  ((Ok script) (axqExecBatch fresh script))
  ((Err e) (Err e)))
```

A dump is also the way to compact a file: the file never shrinks below
its largest size, and a database loaded from a dump takes only the
pages its rows need. Tables that link to each other in a cycle can't be
ordered for a dump, and are `axqSchema`.

Tested by `tests/axqlite/617-api-schema.ax`.

## Concurrency

A connection is used by one binding at a time. `Connection` isn't a
`shared` type, so a `parallel` binding that captures one is refused at
compile time with `AX3064`. Give each binding its own connection.

Connections to one file, in one process or several, share it through a
lock on the whole file:

- A statement that reads takes a shared lock while it runs; inside a
  transaction the lock is kept until the transaction ends.
- A transaction that writes takes the exclusive lock at its first write
  and keeps it until it commits or rolls back.
- A lock never waits. One that isn't free answers `axqBusy` at once,
  and nothing has changed. Retrying, and when, is up to you.
- A connection sees only committed data.

The lock is `flock`, which is advisory: a program that writes the file
without taking it isn't stopped. On a network file system the lock may
not work at all, so keep a database on a local disk.

Tested by `tests/axqlite/615-api-two-connections.ax`.

## Memory

A connection's and a statement's own state live outside Axiom's arena.
Their last owner frees this state; `axqClose` and `axqFinalize` also
release it early. A statement or transaction keeps its connection alive.
An unfinished transaction rolls back when its last owner leaves scope.
Automatic cleanup discards errors; explicit close, finalise and rollback
return their results.

Closing a connection or finalising a statement a second time answers
`Ok`, as `fileClose` does, and so does closing through an alias. Any
other use after a close stops the program with status 85.

Tested by `tests/axqlite/616-api-auto-close.ax` and
`tests/stdlib/376-axq-close-twice.ax`.

Everything a function answers, such as a `Row` or a `String`, is
allocated in your arena when it answers.

`axqExec`, `axqExecBatch`, `axqRun` and `axqRunOn` reclaim everything
they allocate, so a loop of them keeps memory flat. A query's rows are
yours to keep, so wrap a loop of queries in a `region` to reclaim them:

```scheme fragment
(region r
  (match (axqQueryOn db (axqlSelect items * (where (= id (int k)))))
    ((Ok rows) (vecLen rows.rows))
    ((Err e) -1)))
```

## Limits

- One table per statement, with the rows its links point at: no joins
  or subqueries. [axql.md](axql.md#what-axql-doesnt-have) lists what
  AXQL leaves out.
- A database holds at most 2^31 pages of 4096 bytes. An index entry
  holds at most 1000 bytes. A table has at most 1000 columns, and a
  statement at most 32766 parameters and 3000 operators; `IN` takes a
  long list of values. Parentheses nest at most 200 levels, including
  function calls and `IN` lists.
- A statement parses its text each time it runs, which costs a few
  microseconds.
- A transaction that changes more pages than the cache holds writes
  some of them early, so give a connection that makes big changes a
  bigger cache: `(OpenOptions true false 8192)` holds 32 MiB.
- The macro forms have no literals and take lists of at most 16 items;
  [axql.md](axql.md#statements-as-axiom-forms) has the details.
- Not yet: compacting a file in place, and a change feed that tells a
  program when a table changes; see the
  [roadmap](roadmap.md#standard-library).

## See also

- [axql.md](axql.md): the AXQL language reference.
- [axqlite-format.md](axqlite-format.md): the file format, the journal,
  and recovery.
