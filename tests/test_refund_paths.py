#!/usr/bin/env python3
"""Assert every charged action is refunded when it does not complete.

WHY THIS IS A STRUCTURAL TEST

The bug this guards against cannot be caught by calling the code. Streamlit
aborts a running script by raising StopException / RerunException, both of which
derive from BaseException -- so `except Exception` never sees them. Reproducing
that needs a live Streamlit runtime, a real browser interaction, and precise
timing. What CAN be checked cheaply, and what actually regressed, is the shape:

    consume_credit(...)          # money leaves the user
    try:
        ...work...
        delivered = True
    finally:
        if not delivered:
            refund_credit(...)   # money comes back

Before 2026-08-01 every refund on these pages lived in an `except Exception`.
The single most common way a scan dies -- the user clicking again because 40
seconds have passed and nothing looks like it is happening -- raises
BaseException, bypassed all of them, and charged a second credit for the retry.
Two credits, one scan, no refund. A reviewer found it; the 120 assertions in the
other suites did not, because none of them touch these files.

So this asserts the invariant directly on the syntax tree: every consume_credit
call site is inside a try whose finally refunds. It is deliberately blunt. It
will complain if someone adds a fourth charged action and forgets the guard,
which is exactly the mistake that was made twice already.

Usage:
    python3 tests/test_refund_paths.py
"""

from __future__ import annotations

import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = ("pages/Discovery.py", "pages/Deep_Analysis.py")

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _enclosing_trys(node, parents):
    """Every Try that lexically contains `node`, innermost first."""
    out, cur = [], node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, ast.Try):
            out.append(cur)
    return out


def _enclosing_stmt(node, parents):
    cur = node
    while cur in parents and not isinstance(cur, ast.stmt):
        cur = parents[cur]
    return cur if isinstance(cur, ast.stmt) else None


def _sibling_block(stmt, parents):
    """The statement list `stmt` belongs to, plus its index in it.

    The charge is deliberately OUTSIDE the try -- if consume_credit fails there
    is nothing to refund, so it cannot be inside the block that refunds. The
    guard therefore has to be a LATER SIBLING of the charge, not an ancestor.
    An earlier version of this test asserted the charge was inside the try; it
    failed on correct code and, more importantly, could not detect a removed
    backstop, because it was already failing for the wrong reason.
    """
    parent = parents.get(stmt)
    if parent is None:
        return None, -1
    for field in ("body", "orelse", "finalbody"):
        block = getattr(parent, field, None)
        if isinstance(block, list) and stmt in block:
            return block, block.index(stmt)
    return None, -1


def _contains_call(nodes, name: str) -> bool:
    return any(
        _call_name(n) == name
        for stmt in nodes
        for n in ast.walk(stmt)
    )


def test_streamlit_abort_is_baseexception() -> None:
    """Documents the premise. If this ever changes, the whole design can relax."""
    print("\npremise: Streamlit's abort bypasses `except Exception`")
    try:
        from streamlit.runtime.scriptrunner_utils.exceptions import (
            RerunException, StopException,
        )
    except ImportError as e:
        check("streamlit exceptions importable", False, str(e))
        return
    for exc in (StopException, RerunException):
        check(f"{exc.__name__} is NOT an Exception subclass",
              not issubclass(exc, Exception),
              "it is now catchable by `except Exception` -- re-evaluate the guards")
        check(f"{exc.__name__} IS a BaseException subclass",
              issubclass(exc, BaseException))


def test_every_charge_has_a_finally_refund() -> None:
    print("\nevery consume_credit call site is guarded by a finally-refund")
    for rel in TARGETS:
        path = os.path.join(REPO, rel)
        tree = ast.parse(open(path, encoding="utf-8").read())
        parents = _parents(tree)

        charges = [n for n in ast.walk(tree) if _call_name(n) == "consume_credit"]
        check(f"{rel}: found charged action(s)", len(charges) > 0,
              "no consume_credit call -- did the file move?")

        for call in charges:
            where = f"{rel}:{call.lineno}"
            stmt = _enclosing_stmt(call, parents)
            block, idx = _sibling_block(stmt, parents)

            guarded = None
            if block is not None:
                for later in block[idx + 1:]:
                    if isinstance(later, ast.Try) and later.finalbody \
                       and _contains_call(later.finalbody, "refund_credit"):
                        guarded = later
                        break

            check(f"{where} is followed by a try/finally that refunds",
                  guarded is not None,
                  "an abort after this charge delivers nothing and keeps the credit")

            if guarded is not None:
                # The refund must be conditional -- an unconditional one would
                # undo every successful charge too.
                conditional = any(isinstance(s, ast.If) for s in guarded.finalbody)
                check(f"{where} refund is conditional on a delivered flag",
                      conditional,
                      "unconditional refund in finally would undo successful charges")


def test_refunds_do_not_rely_only_on_except() -> None:
    """The regression itself: a refund reachable ONLY via `except Exception`."""
    print("\nno charged action relies solely on `except Exception` for its refund")
    for rel in TARGETS:
        path = os.path.join(REPO, rel)
        tree = ast.parse(open(path, encoding="utf-8").read())
        parents = _parents(tree)

        for call in [n for n in ast.walk(tree) if _call_name(n) == "refund_credit"]:
            in_handler = False
            cur = call
            while cur in parents:
                cur = parents[cur]
                if isinstance(cur, ast.ExceptHandler):
                    in_handler = True
                    break
            if not in_handler:
                continue
            # This refund is inside an except. Fine -- as long as the same try
            # also has a finally-refund backstop.
            trys = _enclosing_trys(call, parents)
            backed = any(t.finalbody and _contains_call(t.finalbody, "refund_credit") for t in trys)
            check(f"{rel}:{call.lineno} except-refund has a finally backstop",
                  backed,
                  "only reachable on Exception; a Streamlit abort skips it")


def _call_name_in(body, name: str) -> bool:
    """Does this statement list actually CALL `name`?

    Asserted on the call graph rather than on source text: a comment saying
    the branch refunds is not a refund.
    """
    import ast
    return any(_call_name(n) == name
               for n in ast.walk(ast.Module(body=body, type_ignores=[])))


def test_an_empty_summary_is_refunded_not_rendered() -> None:
    """A panel of dashes is worse than an error: it is a silent charge.

    When the ledger yields no verdict the page falls back to the older prose
    adjudicator. If THAT produces nothing the dict is empty, and
    render_recommendation_panel .get()s its way to Signal "--", Confidence
    "--" and a neutral score -- then sets the delivered flag, keeps the credit
    and writes no row. Before the pipeline was extracted this state raised and
    the finally refunded; the guard restores that outcome, and this pins it.
    """
    print("\nan empty summary must refund, not render a panel of dashes")
    import ast
    # DISCOVERY WAS CARVED OUT OF THIS by a one-element tuple, and it was the
    # page that had the bug: it charged, marked the run completed, wrote no
    # row, and showed a grey info box no user could tell from a quiet market.
    # It guards the state in its button handler rather than at the render, so
    # it is checked on its own terms below.
    for page in ("Deep_Analysis.py",):
        tree = ast.parse(open(os.path.join(REPO, "pages", page)).read())
        # The page is now a card renderer, so the empty-result state is
        # "no card" rather than "no ai_summary". Matched on the CARD name
        # rather than the exact boolean shape, because the guard legitimately
        # tests two things (absent card, or a card carrying an error).
        guards = [n for n in ast.walk(tree)
                  if isinstance(n, ast.If)
                  and any(isinstance(x, ast.Name) and x.id == "_card"
                          for x in ast.walk(n.test))
                  and _call_name_in(n.body, "refund_credit")]
        check(f"{page} guards the empty-summary case", len(guards) == 1,
              f"found {len(guards)}")
        if not guards:
            continue
        body = guards[0].body
        names = {_call_name(n) or "" for n in
                 ast.walk(ast.Module(body=body, type_ignores=[]))} - {""}
        check(f"{page} refunds on it", "refund_credit" in names, str(sorted(names)))
        check(f"{page} stops before the panel renders",
              any(isinstance(n, ast.Attribute) and n.attr == "stop"
                  for n in ast.walk(ast.Module(body=body, type_ignores=[]))),
              str(sorted(names)))

    disc = open(os.path.join(REPO, "pages", "Discovery.py")).read()
    tree = ast.parse(disc)
    # The guard is a branch of the button handler's if/elif chain, sitting
    # BEFORE the branch that marks the analysis delivered. Assert on the call
    # graph, not on the text: a comment mentioning the refund is not a refund.
    guards = [n for n in ast.walk(tree)
              if isinstance(n, ast.If)
              and isinstance(n.test, ast.UnaryOp)
              and isinstance(n.test.op, ast.Not)
              and _call_name(n.test.operand) == "_deliverable"]
    # TWO of them, and they do different jobs. The button handler refunds
    # before the run is ever marked delivered; the render guard only declines
    # to draw, because by then the credit decision has already been taken on a
    # previous script run. Asserting a single site would have accepted either
    # one alone -- and the render guard alone is precisely the bug.
    bodies = [{_call_name(n) or "" for n in
               ast.walk(ast.Module(body=g.body, type_ignores=[]))} - {""}
              for g in guards]
    check("Discovery guards the empty-summary case at both points",
          len(guards) == 2, f"found {len(guards)}")
    check("...and the charging one refunds",
          any("refund_credit" in b for b in bodies), str(bodies))
    check("...and the rendering one declines to draw",
          any("info" in b for b in bodies), str(bodies))
    # Both pages must ask the question the SAME way, or one of them drifts back.
    # ONE definition, IMPORTED by both -- not two that happen to agree today.
    # They did not agree: one gated on card()'s error stub and the other on the
    # analysis, and the difference silently skipped the log write for a run
    # that had produced a real verdict.
    deep = open(os.path.join(REPO, "pages", "Deep_Analysis.py")).read()
    for name, src in (("Discovery", disc), ("Deep_Analysis", deep)):
        check(f"{name} imports the shared deliverable() predicate",
              "deliverable as _deliverable" in src, name)
        check(f"{name} defines no rival predicate",
              "def _summary_available" not in src, name)


def main() -> int:
    print("=" * 74)
    print("  Refund-path guards -- charged work must never be silently kept")
    print("=" * 74)
    for t in (test_streamlit_abort_is_baseexception,
              test_every_charge_has_a_finally_refund,
              test_refunds_do_not_rely_only_on_except,
              test_an_empty_summary_is_refunded_not_rendered):
        try:
            t()
        except Exception as e:
            FAILED.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"  FAIL  {t.__name__} CRASHED  <- {type(e).__name__}: {e}")

    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("\n  A charged action can be aborted without refunding:")
        for name, detail in FAILED:
            print(f"    - {name}: {detail}")
    else:
        print("\n  Every charged action is guarded.")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
