#!/usr/bin/env python3
"""Pin the signal log, whose whole value is months of rows nobody reads yet.

THAT IS WHAT MAKES IT DANGEROUS. Every other component fails loudly: a broken
verdict shows on the page, a broken projection raises. This one degrades to a
column of NULLs and a WARNING in a log nobody is watching, and the cost is only
discovered when someone finally asks the question the table was built for --
by which point the rows cannot be recreated, because X's index is 7 days deep
and price history only accumulates forward.

So the tests here are mostly about the things that are UNRECOVERABLE:

  1. decision_trade_date. Without the bar's own date, a verdict issued at
     02:00 UTC, on a weekend or on a holiday has a created_at whose date is not
     a trading day. `join price_history on trade_date = created_at::date` then
     finds no row -- silently discarding roughly a third of the table.

  2. branch. The pillar list is in DISPLAY order, not the cascade's, so the
     first failed pillar names the wrong cause for reachable states. Anything
     grouping by cause needs the branch the cascade itself set.

  3. The near-miss states -- seed_only, own_clusters, confidence_notes,
     catalyst_hard_scored -- none of which appear in any module output, and
     without which a Watch that nearly became a Buy is indistinguishable from
     one that never came close.

  4. Never raising. This runs after the user has paid and the panel has
     rendered.

Usage:
    python3 tests/test_signal_log.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils import signal_log as SL  # noqa: E402
from utils.evidence import EvidenceRow  # noqa: E402
from utils.verdict import adjudicate  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []

FLAT = [100.0] * 12
FALLING = [100, 97, 94, 91, 88, 86, 84, 82, 81, 80, 79, 78]

MIGRATION = REPO / "supabase" / "migrations" / "20260817010000_signal_log.sql"


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def row(i, ch="social_base", **kw):
    d = dict(post_id=str(i), channel=ch, text=f"post {i}", author_id=f"a{i}",
             target_match_type="cashtag", target_subject_status="primary",
             evidence_types=("directional_view",), margin=0.6, scored=True,
             cluster_id=i, spam_risk="low", evidence_eligible=True)
    d.update(kw)
    return EvidenceRow(**d)


def build_row(v, **kw):
    """The dict record() would POST, without the network."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        import json
        captured["row"] = json.loads(req.data.decode())[0]
        raise RuntimeError("stop before network")

    import urllib.request
    real = urllib.request.urlopen
    real_cfg = SL._config
    SL._config = lambda n, d="": "https://x.test" if "URL" in n else "key"
    urllib.request.urlopen = fake_urlopen
    try:
        SL.record("TSLA", v, feature=kw.pop("feature", "deep_analyze"), **kw)
    finally:
        urllib.request.urlopen = real
        SL._config = real_cfg
    return captured.get("row")


def test_the_anchor_date_survives_to_the_row():
    print("\nUNRECOVERABLE: the bar date, without which nothing can be scored")
    v = adjudicate([row(i) for i in range(8)], FLAT)
    r = build_row(v, price_at_decision=412.5, decision_trade_date="2026-08-14")
    check("decision_trade_date is written", r.get("decision_trade_date") == "2026-08-14",
          str(r.get("decision_trade_date")))
    check("price_at_decision is written", r.get("price_at_decision") == 412.5,
          str(r.get("price_at_decision")))
    # A weekend verdict is the case that motivated the column. created_at will
    # be a Saturday; the anchor must remain Friday's session.
    r = build_row(v, price_at_decision=412.5, decision_trade_date="2026-08-14")
    check("a Saturday verdict still anchors to a trading day",
          r["decision_trade_date"] == "2026-08-14")
    r = build_row(v, price_at_decision=412.5)
    check("absent rather than guessed when unknown",
          r.get("decision_trade_date") is None, str(r.get("decision_trade_date")))
    # And the source of it actually exists.
    import inspect
    from utils import finance
    check("get_stock_data returns last_bar_date",
          "'last_bar_date'" in inspect.getsource(finance.get_stock_data))


def test_every_cascade_branch_is_labelled_and_distinct():
    print("\nUNRECOVERABLE: which branch fired, as a stable token")
    cases = {
        "buy_sentiment": ([row(i) for i in range(8)], FLAT),
        "sentiment_negative": ([row(i, margin=-0.7) for i in range(8)], FLAT),
        "risk_high": ([row(i, risk_severity="severe", evidence_types=("risk",))
                       for i in range(6)], FLAT),
        "bearish_event": ([row(i, catalyst_severity="hard", margin=-0.6,
                               evidence_types=("catalyst", "directional_view"))
                           for i in range(2)] + [row(10 + i) for i in range(10)], FLAT),
        "buy_catalyst": ([row(i, catalyst_severity="hard", margin=0.6,
                              evidence_types=("catalyst", "directional_view"))
                          for i in range(6)], FLAT),
        "too_few_sources": ([row(i) for i in range(2)], FLAT),
        "price_missing": ([row(i) for i in range(8)], None),
        "channel_conflict": ([row(i, margin=0.6) for i in range(12)]
                             + [row(50 + i, target_match_type="alias", margin=-0.7)
                                for i in range(4)], FLAT),
        "quality_reject": ([], None),
        "no_alignment": ([row(i) for i in range(4)], FLAT),
        "seed_only": ([row(i) for i in range(3)]
                      + [row(100 + i, ch="discovery_seed") for i in range(9)], FLAT),
    }
    seen = {}
    for expect, (rows, prices) in cases.items():
        v = adjudicate(rows, prices)
        check(f"{expect} fires and is labelled", v.branch == expect,
              f"got {v.branch!r} ({v.recommendation})")
        seen[expect] = v.recommendation
    check("no branch is left blank", all(seen.values()))
    # The nearest miss of all: the Buy branch fired, then confidence took it
    # back. It has no failing pillar, so nothing else can identify it.
    spam = [row(i, cluster_id=0, margin=0.6) for i in range(10)] + \
           [row(50 + i, margin=0.6) for i in range(6)]
    v = adjudicate(spam, FLAT)
    if v.recommendation != "Buy":
        check("a confidence-downgraded Buy is identifiable",
              v.branch in ("buy_downgraded_low_confidence", "quality_reject",
                           "too_few_sources", "no_alignment"), v.branch)


def test_near_miss_state_is_captured():
    print("\nUNRECOVERABLE: the states that separate a near miss from a distant one")
    # Seed-dominated: 3 own clusters, 9 seed.
    v = adjudicate([row(i) for i in range(3)]
                   + [row(100 + i, ch="discovery_seed") for i in range(9)], FLAT)
    r = build_row(v)
    check("seed_only is recorded", r.get("seed_only") is True, str(r.get("seed_only")))
    check("own_clusters is recorded", r.get("own_clusters") == 3, str(r.get("own_clusters")))
    # A confirmed event whose rows were never scored defaults hard_direction to
    # 0.0, which passes the `>= 0` bullish test.
    unscored = [row(i, catalyst_severity="hard", scored=False, margin=0.0,
                    evidence_types=("catalyst",)) for i in range(6)]
    v = adjudicate(unscored, FLAT)
    r = build_row(v)
    check("catalyst_hard_scored distinguishes 'unmeasured' from 'neutral'",
          r.get("catalyst_hard_scored") == 0
          and (r.get("catalyst_hard_clusters") or 0) >= 1,
          f"scored={r.get('catalyst_hard_scored')} clusters={r.get('catalyst_hard_clusters')}")
    # Confidence notes explain a cap that leaves no other trace.
    v = adjudicate([row(i, cluster_id=0, margin=0.6) for i in range(20)]
                   + [row(50 + i, margin=0.6) for i in range(6)], FLAT)
    r = build_row(v)
    check("confidence_notes are recorded when confidence was capped",
          r.get("confidence_notes") is None or isinstance(r["confidence_notes"], str),
          str(r.get("confidence_notes")))


def test_seed_only_never_blames_a_scan_that_contributed_nothing():
    print("\nthe explanation must be true: no seed means no seed excuse")
    v = adjudicate([row(i) for i in range(4)], FLAT)
    check("4 own posts and zero seed is NOT seed_only", v.seed_only is False,
          f"branch={v.branch}")
    check("...and the reason does not mention a sector scan",
          "sector scan" not in v.reason.lower(), v.reason)
    v = adjudicate([row(i) for i in range(3)]
                   + [row(100 + i, ch="discovery_seed") for i in range(9)], FLAT)
    check("but a genuinely seed-dominated corpus still is", v.seed_only is True,
          f"branch={v.branch}")


def test_first_failed_pillar_is_not_sold_as_the_cause():
    print("\nfirst_failed_pillar is display order, and is named for what it is")
    src = (REPO / "utils" / "signal_log.py").read_text()
    check("the misleading name 'blocked_by' is gone", "blocked_by" not in src)
    check("and the column it writes is first_failed_pillar",
          '"first_failed_pillar"' in src)
    # It is non-null under a GREEN Buy: the catalyst exemption fails the price
    # pillar by design. Anything treating it as "was blocked" is wrong.
    hard = [row(i, catalyst_severity="hard", margin=0.6,
                evidence_types=("catalyst", "directional_view")) for i in range(6)]
    v = adjudicate(hard, FALLING, [1] * 11 + [9])
    if v.recommendation == "Buy":
        check("a green Buy can still carry a failed pillar",
              SL._first_failed_pillar(v) is not None,
              "if this is None the invariant simply did not arise")


def test_projection_columns_carry_their_own_caveat():
    print("\nproj_* are conditioned on the verdict; the flag must travel with them")
    from utils.projections import simple_projection
    import numpy as np
    rng = np.random.default_rng(4)
    prices = list(100 * np.cumprod(1 + rng.normal(0, 0.02, 40)))
    v = adjudicate([row(i) for i in range(8)], FLAT)

    gated = simple_projection(prices, 0.8, days=30, quality_ok=False)
    tilted = simple_projection(prices, 0.8, days=30, quality_ok=True)
    r_g = build_row(v, projection=gated)
    r_t = build_row(v, projection=tilted)
    check("proj_quality_gated is stored", r_g.get("proj_quality_gated") is True,
          str(r_g.get("proj_quality_gated")))
    check("...and is False when the tilt was applied",
          r_t.get("proj_quality_gated") is False, str(r_t.get("proj_quality_gated")))
    check("proj_sentiment_tilt is stored so the drift is quantifiable",
          (r_t.get("proj_sentiment_tilt") or 0) != 0,
          str(r_t.get("proj_sentiment_tilt")))
    check("proj_mae_p75 comes from p75, not the median",
          "mae_p75" in (REPO / "utils" / "signal_log.py").read_text())


def test_ledger_shape_counts_channels_separately():
    print("\nledger shape: a seed row is not an own row")
    rows = ([row(i) for i in range(5)]
            + [row(20 + i, ch="newswire") for i in range(3)]
            + [row(40 + i, ch="discovery_seed") for i in range(2)]
            + [row(60 + i, spam_risk="high", evidence_eligible=False) for i in range(4)])
    s = SL._ledger_shape(rows)
    check("total rows", s["ledger_rows"] == 14, str(s))
    check("social rows", s["rows_social"] == 9, str(s["rows_social"]))
    check("newswire rows", s["rows_newswire"] == 3, str(s["rows_newswire"]))
    check("seed rows", s["rows_seed"] == 2, str(s["rows_seed"]))
    check("spam rows", s["ledger_spam_high"] == 4, str(s["ledger_spam_high"]))
    check("eligible", s["ledger_eligible"] == 10, str(s["ledger_eligible"]))
    check("no ledger -> all None, never 0",
          all(x is None for x in SL._ledger_shape(None).values()))


def test_it_never_raises_and_never_stores_junk():
    print("\nrobustness: this runs after the user has already paid")
    v = adjudicate([row(i) for i in range(8)], FLAT)

    # ISOLATE THE CREDENTIALS. This block asserts record() returns False and
    # never raises -- but it used to pass only because signal_log did not exist
    # yet and every POST 404'd. The moment the migration was applied, the same
    # calls started SUCCEEDING against production and the suite wrote junk TSLA
    # rows into the table the product is trying to learn from. A test that
    # depends on a table being absent is a test that silently becomes a writer.
    _real_config = SL._config
    SL._config = lambda name, default="": ""
    try:
        _no_creds_cases(v)
    finally:
        SL._config = _real_config

    check("record() cannot reach the network from this suite",
          SL._config is _real_config)


def _no_creds_cases(v):
    for name, kw in {
        "no credentials": {},
        "empty ticker": {"ticker": ""},
        "None verdict": {"verdict": None},
        "blank feature": {"feature": "   "},
    }.items():
        try:
            args = {"ticker": "TSLA", "verdict": v, "feature": "deep_analyze"}
            args.update(kw)
            out = SL.record(args["ticker"], args["verdict"], feature=args["feature"])
            check(f"{name} -> False, no raise", out is False, str(out))
        except Exception as e:
            check(f"{name} -> False, no raise", False, f"{type(e).__name__}: {e}")


def _value_coercion_cases():
    check("NaN is dropped, never stored", SL._num(float("nan")) is None)
    check("inf is dropped, never stored", SL._num(float("inf")) is None)
    check("bools are not coerced to numbers", SL._num(True) is None)
    check("junk text is dropped", SL._num("abc") is None)
    check("whitespace text becomes None", SL._txt("   ", 10) is None)


def _resilience_cases():
    # A renamed field must cost one column, not the whole row.
    class Broken:
        pass
    v2 = adjudicate([row(i) for i in range(8)], FLAT)
    object.__setattr__(v2, "branch", None)
    r = build_row(v2)
    check("a missing branch still writes the other columns",
          r is not None and r.get("quality_score") is not None,
          "the row must survive one absent attribute")


def test_the_migration_matches_what_is_written():
    print("\nschema: every key posted must exist as a column")
    sql = MIGRATION.read_text()
    v = adjudicate([row(i) for i in range(8)], FLAT)
    from utils.projections import simple_projection
    import numpy as np
    rng = np.random.default_rng(4)
    prices = list(100 * np.cumprod(1 + rng.normal(0, 0.02, 40)))
    r = build_row(v, price_at_decision=100.0, decision_trade_date="2026-08-14",
                  projection=simple_projection(prices, 0.5, days=30),
                  ledger=[row(i) for i in range(5)], evidence_age_s=120)
    missing = [k for k in r if k not in sql]
    check("no posted key is absent from the migration", not missing, str(missing))
    check("the unique constraint that stops rerun duplicates exists",
          "unique (event_id, ticker, feature)" in sql)
    check("verdict values are constrained", "signal_log_verdict_chk" in sql)
    check("feature values are constrained", "signal_log_feature_chk" in sql)
    check("decision_trade_date is a date column", "decision_trade_date date" in sql)


def _code_only(path) -> str:
    """Source with comments and docstrings removed.

    A substring assertion over raw source is satisfied by prose. Stripping
    first means the only thing that can satisfy it is the code.
    """
    import ast, io, tokenize
    src = path.read_text()
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        out.append(tok)
    stripped = tokenize.untokenize(out)
    # Docstrings survive tokenisation; drop them with the AST.
    tree = ast.parse(stripped)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)
    return ast.unparse(tree)


def test_the_write_sites_are_guarded_against_reruns():
    print("\none paid analysis is one row, even across Streamlit reruns")
    disc = (REPO / "pages" / "Discovery.py").read_text()
    check("Discovery keys its guard on the credit event",
          "_logged_analyses" in disc and "deep_analysis_event_id" in disc)
    check("...and both writes sit behind it", "_should_log" in disc)
    check("Discovery passes event_id, so duplicates are detectable server-side",
          "event_id=_ev" in disc)
    # The same invariant enforced on the other page. Discovery held the third
    # hand-written copy of the pipeline and the fourth copy of the card's
    # wording; both are gone and must not return.
    check("Discovery holds no second copy of the write",
          "verdict_log.record(" not in disc and "signal_log.record(" not in disc)
    check("...nor a second copy of the pipeline",
          "build_ledger" not in disc and "simple_projection" not in disc
          and "adjudicate(" not in disc)
    check("...nor its own copy of the card's wording",
          "Evidence leans upside" not in disc and "Thin data" not in disc)
    # Absence is not enough: deleting the wording AND not calling card() leaves
    # a panel of em-dashes, which no negative assertion can see. Code-only, so
    # a comment mentioning card() cannot satisfy it.
    _disc_code = _code_only(REPO / "pages" / "Discovery.py")
    check("...and it actually renders FROM the card",
          "_card_for(_a)" in _disc_code and "_card.get(" in _disc_code)
    check("...including the price tiles",
          "_price_tiles(_a)" in _disc_code)
    # The two pages must print the SAME sample size for one paid analysis.
    # Discovery printed the corpus union -- ~90 posts -- beside a verdict
    # resting on 5 independent voices, while the other page printed the 5.
    # The USE, not just the mention: leaving the variable assigned and then
    # not reading it passed a presence check while the panel quietly went back
    # to printing the corpus union.
    # BOTH pages take the sample size off the card, so one paid analysis
    # cannot be described as 5 posts on one page and ~90 on the other. Reading
    # it off the card rather than off a Verdict is also what lets the remote
    # path render at all -- it has no Verdict object.
    _deep_code = _code_only(REPO / "pages" / "Deep_Analysis.py")
    for _name, _src in (("Discovery", _disc_code), ("Deep_Analysis", _deep_code)):
        check(f"{_name} sizes the sample from the card",
              "independent_voices" in _src and "quality.eligible_clusters" not in _src,
              _name)
    check("Discovery keeps its cohort tag, which verdict_log cannot otherwise "
          "recover", 'route="discovery"' in disc)
    deep = (REPO / "pages" / "Deep_Analysis.py").read_text()
    # CODE ONLY. These are substring tests, and the modules they read are
    # deliberately comment-dense -- a comment saying "we used to write
    # decision_trade_date=a.bar_date" satisfied the assertion below while the
    # call passed None, which is item #1 on this suite's own list of silent
    # failures. Comments cannot vouch for code.
    ana = _code_only(REPO / "utils" / "analyze.py")
    # These two assertions used to read Deep_Analysis.py, because the write
    # lived there. It now lives in utils/analyze.persist and the page calls it,
    # so they follow the behaviour rather than being deleted -- and the check
    # below exists so a future edit cannot quietly reinstate the second copy
    # they were originally written to guard.
    check("Deep Analyze holds no second copy of the write",
          "verdict_log.record(" not in deep and "signal_log.record(" not in deep,
          "the page writes the tables directly again")
    check("the pipeline passes the bar date",
          "decision_trade_date=a.bar_date" in ana)
    # Quote-agnostic: _code_only round-trips through ast.unparse, which
    # normalises string delimiters. The behavioural version of this assertion
    # lives in tests/test_analyze.py, which reads the kwarg the writer is
    # actually called with; this one only guards the shape.
    check("the pipeline does not store the literal 'unknown' sector",
          'a.sector != "unknown"' in ana or "a.sector != 'unknown'" in ana)
    # The expander must render BEFORE the network writes, not after.
    i_exp = deep.find("render_full_analysis_expander(analysis_results)")
    i_log = deep.find("_persist(")
    check("the full analysis renders before the logging POSTs",
          -1 < i_exp < i_log, f"expander@{i_exp} log@{i_log}")


def main() -> int:
    print("=" * 74)
    print("  signal_log: the table whose failures are silent")
    print("=" * 74)
    test_the_anchor_date_survives_to_the_row()
    test_every_cascade_branch_is_labelled_and_distinct()
    test_near_miss_state_is_captured()
    test_seed_only_never_blames_a_scan_that_contributed_nothing()
    test_first_failed_pillar_is_not_sold_as_the_cause()
    test_projection_columns_carry_their_own_caveat()
    test_ledger_shape_counts_channels_separately()
    test_it_never_raises_and_never_stores_junk()
    _value_coercion_cases()
    _resilience_cases()
    test_the_migration_matches_what_is_written()
    test_the_write_sites_are_guarded_against_reruns()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
