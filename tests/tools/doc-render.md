# Renderer fixture

<!-- doc-links-resolve-from: docs -->

This page exercises every construct `axiom doc` renders. Its rendered
body is `tests/tools/doc-render.golden`, so a change to the renderer
is a visible diff.

## Headings

### Repeated heading

### Repeated heading

#### Fourth level with `code` and *emphasis*

## Paragraphs and inline

A paragraph with `inline code`, **bold**, *italic*, a [link to the
reference](reference.md#if), a [link up](../README.md#install), a
[contributing link](../CONTRIBUTING.md), an
[examples link](../examples/README.md), a
[changelog link](../CHANGELOG.md), an [anchor](#headings), an
[external link](https://example.com/x?a=1&b=2) and an autolink
<https://example.com/auto>. A bare https://example.com/bare. ends a
sentence. Escapes: \*not emphasis\*, \`not code\`, a \\ backslash.
Entities: 3 < 4 & 5 > 2, a <placeholder> in prose, and `a <b> & c` in
code. A `` `backtick` `` inside code. A lone * star and an
under_score_name.

Raw HTML passes through: <a id="old-anchor"></a> and <!-- a comment -->
and a line break<br> here.

## Code

```scheme
;;! Module doc line.
;; Doc comment with `code`.
; A plain comment, and #| a block |# comment.
(:: main Int)
;@axiom:effect(io)
(fn (main)
  (let ((mut n 1) (s "text \"quoted\"") (c 'x') (f 1.5))
    {
      (set n (+ n 2))
      (if (&& true (!= n 0)) (println "{n}") 0)
      (match (Some n) ((Some v) v) ((None) 0))
      (fn data data)
      0
    }))
```

```scheme fragment
(:: half (-> Int Int))
```

```scheme refused
(fn (main) "unterminated
```

```axiom
(data Shape (a) (Circle Float) (Square Int))
```

```bash
axiom doc --out /tmp/site --no-open
```

```text
<main> is escaped & kept
```

```
No info string.
```

    An indented code block,
    kept as is.

## Lists

- first item
- second item with a nested list:
  - nested one
  - nested two
    1. ordered inside, with a
       continuation line
    2. second
- third item
spanning a lazy continuation line

1. one
2. two

   A second paragraph in a loose item.

3. three

A list that starts at three:

3. starts at three
4. four

* star bullet
* another star

## Table

| Name | Kind | Note |
|:-----|:----:|-----:|
| `fmtInt` | value | left, centre, right |
| `a \| b` | escaped pipe | `code` and **bold** |

## Quote and rule

> A quoted paragraph with `code`.
> Second line of the quote.
>
> > Nested quote.

---

***

## Image

![The renderer's test image](assets/doc-render.svg)

![Remote](https://example.com/remote.png)

<details>
<summary>Browse</summary>

Hidden content, still Markdown.

</details>

Done.
