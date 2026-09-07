#!/usr/bin/env python3
"""Publish the nightly sector pulse as static pages. Standard library only.

WHY

The marketing site explains what the product does. Seven pages of explanation
get you found for your own name and not much else -- nobody searches for a
company they have never heard of. What earns search traffic is content nobody
else publishes, and the pulse already produces some every night: ten sectors
measured for participation, direction and who drove the day.

Rendering that as one page per trading day turns a table nobody can see into a
growing set of pages that are genuinely useful, genuinely unique, and cost
nothing to produce -- the numbers are computed by the nightly sync whether
anyone reads them or not.

WHAT IT WRITES

  site/pulse/index.html          the archive, newest first
  site/pulse/<date>/index.html   one page per trading day
  site/sitemap.xml               regenerated to include every page

ANON KEY ONLY. It reads through get_sector_pulse_recent(), the SECURITY
DEFINER function any browser may call, so the generator needs no service-role
key and can run in CI with a public credential.

THE WORDING RULE, AND WHY IT CONSTRAINS THIS FILE

docs/SECTOR_PULSE.md records what the year-long backtest did and did not
support: the states may be described and ranked, and may NOT be presented as a
forecast. The measured edge was a quarter of a percentage point of median
excess return inside an interquartile range thirteen times wider. So every
sentence generated here describes what happened -- how many companies
participated, which way volume leaned, who drove the increase -- and none
predicts what happens next. `event` is the one state where firm language is
earned, because it is reliably negative at every horizon tested, and naming a
single company as the cause is a warning rather than a call.

Usage:
    python3 scripts/generate_pulse_pages.py            # last 30 trading days
    python3 scripts/generate_pulse_pages.py --days 90
    python3 scripts/generate_pulse_pages.py --dry-run  # report, write nothing
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import sector_pulse as P  # noqa: E402

SITE = "https://about.thestocksentinel.com"
APP = "https://thestocksentinel.com"
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site")

SECTOR_LABEL = {
    "tech": "Technology", "healthcare": "Health care", "energy": "Energy",
    "finance": "Finance", "consumer": "Consumer", "utilities": "Utilities",
    "real estate": "Real estate", "industrials": "Industrials",
    "materials": "Materials", "communication": "Communication",
}
STATE_ORDER = {"accumulating": 0, "event": 1, "quiet": 2, "distributing": 3}
STATE_LABEL = {"accumulating": "Accumulating", "event": "Single-name event",
               "quiet": "Quiet", "distributing": "Distributing"}


def pct(v, digits=0):
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def signed(v, digits=1):
    return "—" if v is None else f"{v * 100:+.{digits}f}%"


def top_of(row):
    c = row.get("top_contrib") or []
    return c[0] if c and isinstance(c[0], dict) else None


def explain(row) -> str:
    """One sentence per sector. Describes what happened; never forecasts."""
    st, t = row.get("state"), top_of(row)
    if st == "event" and t:
        return (f"{t['ticker']} accounted for {t['share_of_rise']:.0%} of the day's rise in "
                f"trading volume, at {t['rel_vol']:.0f}× its own normal level — one company, "
                f"not a sector-wide move.")
    if st == "accumulating":
        return (f"{row['acc_days_5d']} of the last 5 sessions closed up on heavier volume, "
                f"with {pct(row['breadth'])} of companies trading above their normal level.")
    if st == "distributing":
        ud = row.get("ud_ratio_5d")
        return ("Volume leaned to falling sessions over the last five days"
                + (f" — {ud:.2f}× as many dollars traded on down days as up days." if ud else "."))
    return f"{pct(row['breadth'])} of companies traded above their normal level. Nothing unusual."


def headline(rows) -> tuple[str, str]:
    """(page title fragment, meta description). Unique per day, from the data."""
    acc = [r for r in rows if r["state"] == "accumulating"]
    ev = [r for r in rows if r["state"] == "event"]
    if acc:
        top = max(acc, key=lambda r: r["breadth"] or 0)
        lead = f"{SECTOR_LABEL[top['sector']]} showed the broadest participation"
        desc = (f"{SECTOR_LABEL[top['sector']]} led on participation with {pct(top['breadth'])} of "
                f"companies above normal volume.")
    elif ev:
        e = ev[0]; t = top_of(e)
        lead = f"{SECTOR_LABEL[e['sector']]} moved on a single company"
        desc = (f"{SECTOR_LABEL[e['sector']]} was driven by {t['ticker']}, "
                f"{t['share_of_rise']:.0%} of the volume increase.") if t else f"{SECTOR_LABEL[e['sector']]} moved on one name."
    else:
        d = [r for r in rows if r["state"] == "distributing"]
        n = len(d)
        lead = (f"{n} sector{'s' if n != 1 else ''} saw volume lean to falling sessions"
                if d else "A quiet session across all ten sectors")
        desc = "No sector showed broad accumulation. " + (
            f"{', '.join(SECTOR_LABEL[r['sector']] for r in d[:3])} leaned to down-day volume."
            if d else "Participation was below normal across the board.")
    return lead, desc[:158]


def head(title, desc, canonical, extra=""):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="canonical" href="{canonical}">
<meta name="robots" content="index, follow, max-image-preview:large">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Stock Sentinel">
<meta property="og:url" content="{canonical}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(desc)}">
<meta property="og:image" content="{SITE}/assets/og.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{SITE}/assets/og.png">
<link rel="stylesheet" href="/assets/style.css">
{extra}</head>
<body>
<header class="site">
  <div class="wrap">
    <a class="brand" href="/">Stock Sentinel</a>
    <nav aria-label="Main">
      <a href="/how-it-works/">How it works</a>
      <a href="/method/">Method</a>
      <a href="/pulse/" aria-current="page">Sector activity</a>
      <a href="/pricing/">Pricing</a>
      <a href="/faq/">FAQ</a>
    </nav>
  </div>
</header>
<main class="wrap">
"""


FOOT = f"""</main>
<footer class="site">
  <div class="wrap">
    <div class="cols">
      <a href="/how-it-works/">How it works</a>
      <a href="/method/">Method</a>
      <a href="/pulse/">Sector activity</a>
      <a href="/pricing/">Pricing</a>
      <a href="/faq/">FAQ</a>
      <a href="/terms/">Terms</a>
      <a href="/privacy/">Privacy</a>
      <a href="{APP}/Contact" rel="noopener">Contact</a>
    </div>
    <p class="disclaim">Stock Sentinel measures public discussion about publicly traded companies and
    presents it alongside market price data. It does not provide financial advice, does not manage
    money, and does not execute trades.</p>
  </div>
</footer>
</body>
</html>
"""


def day_page(date: str, rows: list, prev: str | None, nxt: str | None) -> str:
    d = dt.date.fromisoformat(date)
    pretty = d.strftime("%-d %B %Y") if os.name != "nt" else d.strftime("%d %B %Y")
    weekday = d.strftime("%A")
    lead, desc = headline(rows)
    title = f"Sector activity — {pretty}"
    rows = sorted(rows, key=lambda r: (STATE_ORDER[r["state"]], -(r["breadth"] or 0)))

    ld = f"""<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"Dataset",
 "name":"US sector trading activity, {pretty}",
 "description":"Participation, volume direction and leading contributors across ten US equity sectors on {date}.",
 "temporalCoverage":"{date}",
 "url":"{SITE}/pulse/{date}/",
 "creator":{{"@type":"Organization","name":"Stock Sentinel","url":"{SITE}/"}},
 "isAccessibleForFree":true}}
</script>
"""
    out = [head(f"{title} | Stock Sentinel", desc, f"{SITE}/pulse/{date}/", ld)]
    out.append(f"""  <section class="hero">
    <div class="eyebrow">Sector activity · {weekday}</div>
    <h1>{title}</h1>
    <p class="lead">{html.escape(lead)}. Ten US equity sectors, measured on how many companies
    took part rather than how many dollars moved — so one large company cannot stand in for a sector.</p>
  </section>

  <section>
    <table class="spec">
      <thead><tr><th>Sector</th><th>State</th><th>Participation</th><th>5-session</th><th>What happened</th></tr></thead>
      <tbody>
""")
    for r in rows:
        out.append(f"""        <tr><td>{SECTOR_LABEL[r['sector']]}</td><td>{STATE_LABEL[r['state']]}</td>"""
                   f"""<td>{pct(r['breadth'])}</td><td>{signed(r['eq_return_5d'])}</td>"""
                   f"""<td>{html.escape(explain(r))}</td></tr>\n""")
    out.append("""      </tbody>
    </table>
    <p style="color:var(--faint);font-size:14px">Participation is the share of a sector's companies trading
    above 1.5&times; their own normal volume. The 5-session figure is an equal-weight average return, so
    every company counts once regardless of size.</p>
  </section>

  <section>
    <h2>How to read this</h2>
    <table class="spec">
      <thead><tr><th>State</th><th>Meaning</th></tr></thead>
      <tbody>
        <tr><td>Accumulating</td><td>Volume leaned to rising sessions, participation was broad and not falling, and no single company dominated the increase.</td></tr>
        <tr><td>Single-name event</td><td>One company accounted for a large share of the day's volume increase at a climax level. The sector total moved; the sector did not.</td></tr>
        <tr><td>Distributing</td><td>More dollars traded on falling sessions than rising ones.</td></tr>
        <tr><td>Quiet</td><td>Nothing above the thresholds. Most sectors, most days.</td></tr>
      </tbody>
    </table>
    <div class="note warn">
      <p><strong>This describes what happened, not what happens next.</strong> These states are a
      record of participation and volume direction on one trading day. They are not forecasts, not
      recommendations, and not financial advice. See <a href="/method/">the method</a> for how the
      measurement works and what it cannot do.</p>
    </div>
  </section>

  <div class="cta-row">
""")
    if prev:
        out.append(f'    <a class="cta ghost" href="/pulse/{prev}/">&larr; {prev}</a>\n')
    out.append('    <a class="cta ghost" href="/pulse/">All days</a>\n')
    if nxt:
        out.append(f'    <a class="cta ghost" href="/pulse/{nxt}/">{nxt} &rarr;</a>\n')
    out.append(f"""  </div>

  <section>
    <h2>Scan a sector yourself</h2>
    <p>This page shows where money moved. Stock Sentinel's Market Scan shows what people were
    <em>saying</em> — which companies in a sector are getting unusual social attention, and how much
    evidence sits behind each reading.</p>
    <div class="cta-row">
      <a class="cta" href="{APP}/" rel="noopener">Open Stock Sentinel &rarr;</a>
      <span class="cta-note"><b>$5</b> for 2 credits &middot; never expire &middot; no subscription</span>
    </div>
  </section>
""")
    out.append(FOOT)
    return "".join(out)


def index_page(by_date: dict) -> str:
    dates = sorted(by_date, reverse=True)
    newest = dates[0]
    desc = (f"Daily participation and volume direction across ten US equity sectors. "
            f"{len(dates)} trading days through {newest}.")[:158]
    out = [head("Sector activity, day by day | Stock Sentinel", desc, f"{SITE}/pulse/")]
    out.append(f"""  <section class="hero">
    <div class="eyebrow">Sector activity</div>
    <h1>Which US sectors were busy, day by day</h1>
    <p class="lead">Every trading day, ten sectors measured on how many companies took part rather
    than how many dollars moved. Published the evening of each session. {len(dates)} days recorded.</p>
  </section>

  <section>
    <table class="spec">
      <thead><tr><th>Date</th><th>Broadest participation</th><th>Single-name events</th></tr></thead>
      <tbody>
""")
    for d in dates:
        rows = by_date[d]
        acc = [r for r in rows if r["state"] == "accumulating"]
        ev = [r for r in rows if r["state"] == "event"]
        top = max(acc, key=lambda r: r["breadth"] or 0) if acc else max(rows, key=lambda r: r["breadth"] or 0)
        lead = f"{SECTOR_LABEL[top['sector']]} ({pct(top['breadth'])})"
        evs = ", ".join(f"{SECTOR_LABEL[e['sector']]} · {top_of(e)['ticker']}" for e in ev if top_of(e)) or "—"
        out.append(f'        <tr><td><a href="/pulse/{d}/">{d}</a></td><td>{lead}</td><td>{evs}</td></tr>\n')
    out.append("""      </tbody>
    </table>
  </section>

  <div class="note warn">
    <p><strong>Not financial advice.</strong> These pages record what happened on a trading day —
    participation and volume direction. They are not forecasts or recommendations.</p>
  </div>
""")
    out.append(FOOT)
    return "".join(out)


def sitemap(dates: list) -> str:
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    static_pages = [("", "weekly", "1.0"), ("how-it-works/", "monthly", "0.8"), ("method/", "monthly", "0.8"),
          ("pricing/", "monthly", "0.9"), ("faq/", "monthly", "0.7"),
          ("terms/", "yearly", "0.3"), ("privacy/", "yearly", "0.3"),
          ("pulse/", "daily", "0.9")]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, freq, pri in static_pages:
        lines.append(f"  <url><loc>{SITE}/{path}</loc><lastmod>{today}</lastmod>"
                     f"<changefreq>{freq}</changefreq><priority>{pri}</priority></url>")
    for d in sorted(dates, reverse=True):
        lines.append(f"  <url><loc>{SITE}/pulse/{d}/</loc><lastmod>{d}</lastmod>"
                     f"<changefreq>never</changefreq><priority>0.6</priority></url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = P.latest(days=args.days)
    if not rows:
        print("  no pulse rows returned -- refusing to write empty pages")
        return 1
    by_date = collections.defaultdict(list)
    for r in rows:
        by_date[r["trade_date"]].append(r)
    dates = sorted(by_date)
    print(f"  {len(rows)} rows across {len(dates)} dates: {dates[0]} .. {dates[-1]}")

    if args.dry_run:
        for d in dates:
            lead, desc = headline(by_date[d])
            print(f"    {d}  {lead}")
        return 0

    written = 0
    for i, d in enumerate(dates):
        prev = dates[i - 1] if i > 0 else None
        nxt = dates[i + 1] if i + 1 < len(dates) else None
        out = os.path.join(ROOT, "pulse", d)
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "index.html"), "w") as f:
            f.write(day_page(d, by_date[d], prev, nxt))
        written += 1
    os.makedirs(os.path.join(ROOT, "pulse"), exist_ok=True)
    with open(os.path.join(ROOT, "pulse", "index.html"), "w") as f:
        f.write(index_page(by_date))
    with open(os.path.join(ROOT, "sitemap.xml"), "w") as f:
        f.write(sitemap(dates))
    print(f"  wrote {written} day pages + index + sitemap ({len(dates) + 8} URLs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
