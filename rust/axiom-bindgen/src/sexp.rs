//! A tiny Axiom expression tree and a printer that writes it exactly as
//! `axiom fmt` would, so a generated module is formatter-clean without
//! shelling out to the formatter.
//!
//! THAT IS A CLAIM ABOUT TWO IMPLEMENTATIONS, and `check-fmt.sh`
//! section 1b is what holds it: every committed binding under
//! `rust/examples/*/axiom/` must be a fixed point of `axiom fmt`. It
//! was held by nothing until 2026-09-03 - `check-ffi.sh` diffs each
//! binding against a FRESH GENERATION, which compares this printer
//! with itself and stays green while both sides drift together away
//! from the formatter. Measured then: a one-line change to `fpApp` in
//! `self_host/format.ax` moves `Demo.ax` by five lines and this file
//! not at all. Change one printer, change the other.
//!
//! The formatter (`self_host/format.ax`) has no line-width rule; its
//! layout is STRUCTURAL, and the rules for the forms this generator
//! emits are:
//!
//! - an atom is simple; an application is simple iff every element is
//!   simple; `cast`, `let`, `if`, `match` and a multi-statement `{}`
//!   block are never simple (`isSimpleForm`);
//! - an application prints on one line when its one-line form fits
//!   within `WIDTH` columns from where it starts, or when its head is
//!   simple and it has at most four arguments, every one an atom;
//!   otherwise
//!   the head ends the line and the arguments follow ONE PER LINE two
//!   columns past the call's paren, then `)` stacked onto the last one
//!   (`fpApp`).
//!   Until 2026-09-04 there was no newline between the arguments at
//!   all - stage0's quirk, kept for byte-identity until stage0 was
//!   deleted;
//! - `let` with one binding keeps the binding on the `let` line (the
//!   init breaks onto its own line if it is not simple, and the pair
//!   closes stacked onto it); with several, each binding sits on its
//!   own line under `(let (` and the list closes with `)` at the
//!   `let`'s indent; the body is always on its own line at one deeper
//!   indent, and the `let` closes stacked onto it (`fpLet`,
//!   `fpLetBinding`, `fpLetBindingBroken`);
//! - `if` puts the condition on the `if` line and each branch on its
//!   own line, `)` stacked (`fpIf`);
//! - `match` puts every arm on its own line; an arm's body sits beside
//!   the pattern if simple, else below it; the `match` closes stacked
//!   (`fpMatch`, `fpTailBody`);
//! - a block prints `{`, one statement per line, `}` (`fpBeginVec`);
//! - a `fn` head stands alone and the body sits below it, `)` stacked
//!   (`fpDeclFn`).
//!
//! The indent is a column: a nested construct indents two columns past
//! the one it sits in, and a broken call hangs its arguments two columns
//! past its own paren. A closing paren never stands on its own line: it
//! stacks onto whatever precedes it.

/// An Axiom expression.
#[derive(Clone, Debug)]
pub enum Ex {
    /// A leaf, pushed to the output as the string stands: a name, a
    /// literal, an operator. It is the only form that is
    /// unconditionally simple, so an atom is never broken or wrapped
    /// and its string must already be one token.
    Atom(String),
    /// `(head args...)`.
    App(Vec<Ex>),
    /// `(cast Type e)`.
    Cast(String, Box<Ex>),
    /// `(let (bindings...) body)`; bindings are `(name init)`.
    Let(Vec<(String, Ex)>, Box<Ex>),
    /// `(if cond then else)`. Never simple, so it always breaks: the
    /// condition stays on the `if` line and each branch takes its own
    /// (`fpIf`).
    If(Box<Ex>, Box<Ex>, Box<Ex>),
    /// `(match scrutinee (pattern body)...)`; a pattern is an `App`.
    Match(Box<Ex>, Vec<(Ex, Ex)>),
    /// `{ statements... }` with at least two statements.
    Block(Vec<Ex>),
}

/// An [`Ex::Atom`] over a borrowed name. The tree owns its strings,
/// so this is the one place the copy is made.
pub fn atom(s: &str) -> Ex {
    Ex::Atom(s.to_string())
}

/// `(head args...)`. A head with no arguments is the bare head: the
/// formatter unwraps `(x)` to `x`, and a nullary extern is called by
/// naming it.
pub fn app(mut elems: Vec<Ex>) -> Ex {
    if elems.len() == 1 {
        elems.pop().unwrap()
    } else {
        Ex::App(elems)
    }
}

impl Ex {
    /// `isSimpleForm`.
    pub fn is_simple(&self) -> bool {
        match self {
            Ex::Atom(_) => true,
            Ex::App(elems) => elems.iter().all(Ex::is_simple),
            Ex::Block(stmts) => stmts.len() == 1 && stmts[0].is_simple(),
            Ex::Cast(..) | Ex::Let(..) | Ex::If(..) | Ex::Match(..) => false,
        }
    }
}

/// `col` spaces: the indent is a column, as `foIndent` is.
fn ind(col: usize) -> String {
    " ".repeat(col)
}

/// The widest line a form is laid out on: `fmtWidth` in
/// `self_host/format.ax`.
const WIDTH: usize = 100;

/// The column the next byte of `out` lands at.
fn column(out: &str) -> usize {
    match out.rfind('\n') {
        Some(i) => out.len() - i - 1,
        None => out.len(),
    }
}

/// `elems` as one application on one line, printed into a fresh buffer
/// the way `fpAppFlat` prints its trial, or `None` when an element
/// breaks across lines.
fn app_flat(elems: &[Ex]) -> Option<String> {
    let mut s = String::from("(");
    for (i, e) in elems.iter().enumerate() {
        if i > 0 {
            s.push(' ');
        }
        if !print_flat(e, &mut s) {
            return None;
        }
    }
    s.push(')');
    (s.len() <= WIDTH).then_some(s)
}

// A width trial visits a node once and stops as soon as it cannot fit.
// Calling the ordinary printer here would recursively start more trials.
fn print_flat(e: &Ex, out: &mut String) -> bool {
    if out.len() > WIDTH {
        return false;
    }
    match e {
        Ex::Atom(s) => out.push_str(s),
        Ex::Cast(ty, inner) => {
            out.push_str("(cast ");
            out.push_str(ty);
            out.push(' ');
            if !print_flat(inner, out) {
                return false;
            }
            out.push(')');
        }
        Ex::App(elems) => {
            out.push('(');
            for (i, arg) in elems.iter().enumerate() {
                if i > 0 {
                    out.push(' ');
                }
                if !print_flat(arg, out) {
                    return false;
                }
            }
            out.push(')');
        }
        _ => return false,
    }
    out.len() <= WIDTH
}

/// Print `e` at `level`, assuming the cursor already sits at the
/// column where the expression starts.
pub fn print(e: &Ex, level: usize, out: &mut String) {
    match e {
        Ex::Atom(s) => out.push_str(s),
        Ex::Cast(ty, inner) => {
            out.push_str("(cast ");
            out.push_str(ty);
            out.push(' ');
            print(inner, level, out);
            out.push(')');
        }
        Ex::App(elems) => {
            let head = &elems[0];
            let args = &elems[1..];
            let inline = head.is_simple() && args.len() <= 4 && args.iter().all(Ex::is_simple);
            if inline && args.iter().all(|a| matches!(a, Ex::Atom(_))) {
                out.push('(');
                for (i, arg) in elems.iter().enumerate() {
                    if i > 0 {
                        out.push(' ');
                    }
                    print(arg, level, out);
                }
                out.push(')');
                return;
            }
            let flat = app_flat(elems);
            if let Some(f) = &flat {
                let atoms = args.iter().all(|a| matches!(a, Ex::Atom(_)));
                if (inline && atoms) || column(out) + f.len() <= WIDTH {
                    out.push_str(f);
                    return;
                }
            }
            // A broken call hangs its arguments two columns past its own
            // paren (`foSetIndent` in `fpApp`).
            let col = column(out);
            // The formatter keeps the older one-line rule only for a call
            // with a comment inside; generated code has none, so a call
            // that is not laid out on one line above always breaks.
            out.push('(');
            print(head, level, out);
            out.push('\n');
            for (i, a) in args.iter().enumerate() {
                if i > 0 {
                    out.push('\n');
                }
                out.push_str(&ind(col + 2));
                print(a, col + 2, out);
            }
            out.push(')');
        }
        Ex::Let(binds, body) => {
            out.push_str("(let (");
            if binds.len() == 1 {
                let (name, init) = &binds[0];
                out.push('(');
                out.push_str(name);
                out.push(' ');
                if init.is_simple() {
                    print(init, level, out);
                } else {
                    out.push('\n');
                    out.push_str(&ind(level + 2));
                    print(init, level + 2, out);
                }
                out.push(')');
            } else {
                out.push('\n');
                for (name, init) in binds {
                    out.push_str(&ind(level + 2));
                    out.push('(');
                    out.push_str(name);
                    out.push(' ');
                    print(init, level + 2, out);
                    out.push_str(")\n");
                }
                out.push_str(&ind(level));
            }
            out.push_str(")\n");
            out.push_str(&ind(level + 2));
            print(body, level + 2, out);
            out.push(')');
        }
        Ex::If(c, t, f) => {
            out.push_str("(if ");
            print(c, level, out);
            out.push('\n');
            out.push_str(&ind(level + 2));
            print(t, level + 2, out);
            out.push('\n');
            out.push_str(&ind(level + 2));
            print(f, level + 2, out);
            out.push(')');
        }
        Ex::Match(scrut, arms) => {
            out.push_str("(match ");
            print(scrut, level, out);
            out.push('\n');
            for (i, (pat, body)) in arms.iter().enumerate() {
                if i > 0 {
                    out.push('\n');
                }
                out.push_str(&ind(level + 2));
                out.push('(');
                print(pat, level + 2, out);
                tail_body(body, level + 2, out);
                out.push(')');
            }
            out.push(')');
        }
        Ex::Block(stmts) => {
            out.push_str("{\n");
            for (i, s) in stmts.iter().enumerate() {
                if i > 0 {
                    out.push('\n');
                }
                out.push_str(&ind(level + 2));
                print(s, level + 2, out);
            }
            out.push('\n');
            out.push_str(&ind(level));
            out.push('}');
        }
    }
}

/// `fpTailBody`: a space and the body inline when simple, else the body
/// alone on the next line with the closing paren's indent restored.
fn tail_body(body: &Ex, level: usize, out: &mut String) {
    if body.is_simple() {
        out.push(' ');
        print(body, level, out);
    } else {
        out.push('\n');
        out.push_str(&ind(level + 2));
        print(body, level + 2, out);
        out.push('\n');
        out.push_str(&ind(level));
    }
}

/// `(pub fn (name params...) body)` as the formatter lays it out.
pub fn decl_fn(name: &str, params: &[String], body: &Ex) -> String {
    decl_fn_vis(true, name, params, body)
}

/// `(fn (name params...) body)`: a module-private function, laid out
/// the same way.
pub fn decl_private_fn(name: &str, params: &[String], body: &Ex) -> String {
    decl_fn_vis(false, name, params, body)
}

fn decl_fn_vis(public: bool, name: &str, params: &[String], body: &Ex) -> String {
    let mut out = String::new();
    out.push_str(if public { "(pub fn (" } else { "(fn (" });
    out.push_str(name);
    for p in params {
        out.push(' ');
        out.push_str(p);
    }
    // The head stands alone; the body goes on the next line, as
    // `fpDeclFn` lays it out.
    out.push_str(")\n  ");
    print(body, 2, &mut out);
    out.push(')');
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nested_width_trials_finish() {
        let mut e = atom("0");
        for _ in 0..96 {
            e = app(vec![atom("f"), e]);
        }
        let mut out = String::new();
        print(&e, 0, &mut out);
        assert_eq!(out.matches('(').count(), 96);
        assert_eq!(out.matches(')').count(), 96);
        assert!(out.contains('0'));
    }

    #[test]
    fn inline_and_broken_applications() {
        let mut s = String::new();
        print(&app(vec![atom("f"), atom("a"), atom("b")]), 0, &mut s);
        assert_eq!(s, "(f a b)");
        let mut s = String::new();
        print(
            &app(vec![
                atom("f"),
                atom("a"),
                atom("b"),
                atom("c"),
                atom("d"),
                atom("e"),
            ]),
            2,
            &mut s,
        );
        assert_eq!(s, "(f a b c d e)");
        // One that does not fit breaks, one argument per line.
        let long: Vec<Ex> = std::iter::once(atom("f"))
            .chain((0..12).map(|i| atom(&format!("argument{i}"))))
            .collect();
        let mut s = String::new();
        print(&app(long), 2, &mut s);
        assert!(s.starts_with("(f\n  argument0\n  argument1\n"));
    }

    #[test]
    fn let_forms() {
        let mut s = String::new();
        print(
            &Ex::Let(vec![("x".into(), atom("1"))], Box::new(atom("x"))),
            0,
            &mut s,
        );
        assert_eq!(s, "(let ((x 1))\n  x)");
        let mut s = String::new();
        print(
            &Ex::Let(
                vec![("x".into(), atom("1")), ("y".into(), atom("2"))],
                Box::new(atom("x")),
            ),
            2,
            &mut s,
        );
        assert_eq!(s, "(let (\n    (x 1)\n    (y 2)\n  )\n    x)");
    }

    #[test]
    fn fn_decl() {
        let body = Ex::Match(
            Box::new(atom("c")),
            vec![(
                app(vec![atom("Counter"), atom("__h")]),
                app(vec![atom("ffiHandleClose"), atom("__h")]),
            )],
        );
        assert_eq!(
            decl_fn("counterClose", &["c".into()], &body),
            "(pub fn (counterClose c)\n  (match c\n    ((Counter __h) (ffiHandleClose __h))))"
        );
    }
}
