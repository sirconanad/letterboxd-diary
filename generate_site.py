#!/usr/bin/env python3
"""Generate a static highlights page from the Letterboxd diary.

Reads the repository's ``diary.json`` and the sneak results CSV and writes a
single self-contained ``site/index.html`` (inline CSS + inline contribution
graph SVG) suitable for GitHub Pages.

The repository layout expects sibling folders (see README):

    ../graphs/graph.py            canonical contribution-graph module
    ../sneak-preview/sneak-gap.csv   sneak-vs-Kinostart results

Examples:
    python3 generate_site.py            # write site/index.html
    python3 generate_site.py -o out.html
"""

import argparse
import csv
import html
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "graphs"))
import graph  # canonical graphs/graph.py

SNEAK_CSV_DEFAULT = PROJECT_ROOT / "sneak-preview" / "sneak-gap.csv"


def escape(s):  # already-unicode-safe short alias
    return html.escape(str(s), quote=False)


def load_diary(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_sneaks(path: Path) -> list[dict]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    for r in rows:
        r["gap_days"] = int(r["gap_days"]) if r.get("gap_days") else None
        r["kinostart"] = r.get("kinostart") or None
    return rows


SVG_BG = "#1f2430"     # blends with the dark theme instead of white
SVG_EMPTY = "#2c3240"  # no-activity day cells


def year_graph_svg(diary: list[dict], year: int) -> str:
    ds = [e for e in diary if e["when"].startswith(str(year))]
    if not ds:
        return ""
    counts = Counter(date.fromisoformat(e["when"]) for e in ds)
    start = date(year, 1, 1)
    end = date(year, 12, 31) if year < date.today().year else date.today()
    levels = graph.build_levels(counts)
    svg = graph.render_svg(levels, counts, start, end)
    svg = (svg.replace('fill="#ffffff"', f'fill="{SVG_BG}"')
              .replace('fill="#ebedf0"', f'fill="{SVG_EMPTY}"')
              # raise SVG text contrast for the dark theme
              .replace('fill="#767676"', 'fill="#9aa3b2"'))
    # accessible svg: keep the remaining width/height/viewBox attributes
    return svg.replace(
        '<svg xmlns="http://www.w3.org/2000/svg"',
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="Films watched per day in {year}"', 1)


def yearly_graphs_html(diary: list[dict]) -> str:
    years = sorted({int(e["when"][:4]) for e in diary}, reverse=True)
    blocks = []
    for year in years:
        svg = year_graph_svg(diary, year)
        if not svg:
            continue
        blocks.append(
            f'<div class="year">'
            f'<div class="year-label">{year}</div>{svg}</div>')
    return "\n".join(blocks)


def card(kind: str, value: str, label: str) -> str:
    return (f'<div class="stat"><div class="stat-value {kind}">'
            f"{value}</div><div class='stat-label'>{label}</div></div>")


def build_stat_cards(diary: list[dict], sneaks: list[dict]) -> str:
    n = len(diary)
    years = Counter(e["when"][:4] for e in diary)
    rated = [float(e["rating"]) for e in diary
             if e.get("rating") is not None]
    avg = sum(rated) / len(rated) if rated else 0
    liked = sum(1 for e in diary if e.get("liked"))
    rewatch = sum(1 for e in diary if e.get("rewatch"))
    with_gap = [s for s in sneaks if s.get("gap_days") is not None]
    avg_gap = (sum(s["gap_days"] for s in with_gap) / len(with_gap)
               if with_gap else 0)
    return "".join([
        card("accent", f"{n}", f"Films since {min(years)} ({len(years)} years)"),
        card("accent", f"{avg:.2f}", "Average rating (5-point scale)"),
        card("accent", f"{liked}", "Liked"),
        card("accent", f"{rewatch}", "Rewatches"),
        card("accent", f"{avg_gap:.1f} days", "Sneak before cinema release"),
    ])


def tags_html(diary: list[dict], top: int = 12) -> str:
    tag_count = Counter(t for e in diary for t in (e.get("tags") or []))
    items = tag_count.most_common(top)
    if not items:
        return "<p>No tags.</p>"
    mx = items[0][1]
    rows = []
    for tag, n in items:
        w = max(4, round(n / mx * 100))
        rows.append(
            '<div class="tag"><span class="tag-name">'
            f"{escape(tag)}</span><span class='tag-bar'><span "
            f"class='tag-fill' style='width:{w}%'></span></span>"
            f"<span class='tag-count'>{n}</span></div>")
    return "\n".join(rows)


def favorites_html(diary: list[dict], limit: int = 12) -> str:
    favs = (e for e in diary if e.get("rating") == 5 and e.get("liked"))
    grouped: dict[str, list[str]] = {}
    for e in favs:
        grouped.setdefault(e["title"], []).append(e["when"])
    if not grouped:
        return "<p>No favourites found.</p>"
    items = sorted(grouped.items(), key=lambda kv: max(kv[1]), reverse=True)
    cells = []
    for title, dates in items[:limit]:
        n = len(dates)
        label = escape(title) + (f" ×{n}" if n > 1 else "")
        cells.append(f'<span class="chip"><span class="chip-star">★</span>'
                     f"{label} <em>{max(dates)}</em></span>")
    return '\n'.join(cells) + \
        (f'<p class="subtle">… and {len(items) - limit} more '
         f"five-star film{'s' if len(items) - limit != 1 else ''}.</p>"
         if len(items) > limit else "")


def _nice_step(maxv: int) -> int:
    """Round step so the y axis ends up with <=6 ticks."""
    for s in (1, 2, 5, 10, 20, 50, 100, 200):
        if maxv / s <= 6:
            return s
    return 500


def monthly_line_svg(diary: list[dict]) -> str:
    """Line chart of films watched per month, from first entry to today.

    X axis is time (each month from the first diary entry up to the current
    month), Y axis is the number of films watched that month.
    """
    counts = Counter(e["when"][:7] for e in diary)
    if not counts:
        return ""
    sy, sm = map(int, min(counts).split("-"))
    today = date.today()
    months: list[str] = []
    y, m = sy, sm
    while (y, m) <= (today.year, today.month):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    series = [counts.get(mm, 0) for mm in months]
    n = len(months)
    maxv = max(series) or 1

    W, H = 1000, 240
    pad_l, pad_r, pad_t, pad_b = 44, 18, 16, 42
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b

    def x(i: int) -> float:
        return pad_l + plot_w * i / (n - 1) if n > 1 \
            else pad_l + plot_w / 2

    def y(v: int) -> float:
        return pad_t + plot_h * (1 - v / maxv)

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {W} {H}" role="img" '
           f'aria-label="Films watched per month, '
           f'{months[0]} to {months[-1]}">']
    step = _nice_step(maxv)
    for k in range(0, maxv + 1, step):
        gy = y(k)
        out.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{W - pad_r}" '
                   f'y2="{gy:.1f}" stroke="#252b38" stroke-width="1"/>')
        out.append(f'<text x="{pad_l - 8}" y="{gy + 3:.1f}" font-size="10" '
                   f'fill="#9aa3b2" text-anchor="end">{k}</text>')
    out.append(f'<line x1="{pad_l}" y1="{pad_t + plot_h:.1f}" '
               f'x2="{W - pad_r}" y2="{pad_t + plot_h:.1f}" '
               f'stroke="#3a4150" stroke-width="1"/>')

    label_step = max(1, (n - 1) // 24 + 1)
    for i, mm in enumerate(months):
        if i % label_step != 0:
            continue
        d = date.fromisoformat(mm + "-01")
        txt = d.strftime("%b %Y") if i == 0 or mm.endswith("-01") \
            else d.strftime("%b")
        out.append(f'<text x="{x(i):.1f}" y="{H - 22}" font-size="10" '
                   f'fill="#9aa3b2" text-anchor="middle">{txt}</text>')

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(series))
    out.append(f'<polyline points="{pts}" fill="none" stroke="#7ee08e" '
               f'stroke-width="2" stroke-linejoin="round"/>')
    for i, v in enumerate(series):
        if v == 0:
            continue
        d = date.fromisoformat(months[i] + "-01")
        word = "film" if v == 1 else "films"
        out.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3" '
                   f'fill="#7ee08e"><title>{d.strftime("%B %Y")}: '
                   f'{v} {word} watched</title></circle>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def sneak_rows_html(sneaks: list[dict]) -> str:
    ordered = sorted(sneaks, key=lambda s: s["watched"], reverse=True)
    rows = []
    for s in ordered:
        if s["kinostart"] and s["gap_days"] is not None:
            g = s["gap_days"]
            gap = (f"<span class='gap gap-ahead'>+{g}</span>" if g > 0
                   else f"<span class='gap gap-late'>{g}</span>")
            ks = s["kinostart"]
        else:
            gap = "<span class='gap-grey'>n/a</span>"
            ks = "–"
        rows.append(
            f"<tr><td class='t-title'>{escape(s['title'])}</td>"
            f"<td>{escape(s['year'])}</td>"
            f"<td>{s['watched']}</td><td>{ks}</td>"
            f"<td class='t-gap'>{gap}</td>"
            f"<td class='t-tags'>{escape(s.get('tags') or '')}</td></tr>")
    return "\n".join(rows)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sirconanad · Letterboxd Highlights</title>
<meta name="description" content="sirconanad's Letterboxd highlights: contribution graphs of films watched, monthly activity, most-used tags, favourites and sneak-preview viewings compared with their German cinema releases.">
<meta name="author" content="sirconanad">
<meta name="theme-color" content="#0f1115">
<link rel="canonical" href="https://sirconanad.github.io/">
<meta property="og:type" content="website">
<meta property="og:site_name" content="sirconanad · cinema">
<meta property="og:locale" content="en_GB">
<meta property="og:title" content="sirconanad · Letterboxd Highlights">
<meta property="og:description" content="Films watched, monthly activity, tags, favourites and sneak-preview viewings vs. German cinema releases.">
<meta property="og:url" content="https://sirconanad.github.io/">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="sirconanad · Letterboxd Highlights">
<meta name="twitter:description" content="Films watched, monthly activity, tags, favourites and sneak-preview viewings vs. German cinema releases.">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"WebSite","url":"https://sirconanad.github.io/","name":"sirconanad · cinema","description":"sirconanad's Letterboxd highlights: films watched, monthly activity, tags, favourites and sneak-preview viewings vs. German cinema releases."}}</script>
<style>
  :root {{ --bg:#0f1115; --panel:#171a21; --panel2:#1e222b;
          --text:#e7e9ee; --muted:#9aa3b2; --accent:#7ee08e;
          --accent2:#3fb950; --late:#f58d8d; --border:#2a2f3a;
          color-scheme:dark; }}
  * {{ box-sizing:border-box; }}
  html {{ font-size:16px; }}
  body {{ margin:0; font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,
         Arial,sans-serif; background:var(--bg); color:var(--text); }}
  :focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:28px 20px 60px; }}
  header {{ border-bottom:1px solid var(--border); padding-bottom:18px;
           margin-bottom:26px; }}
  h1 {{ margin:0 0 4px; font-size:28px; }}
  h1 .dot {{ color:var(--accent2); }}
  .sub {{ color:var(--muted); }}
  h2 {{ font-size:20px; margin:40px 0 14px; }}
  h2 small {{ color:var(--muted); font-weight:400; }}
  .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
           gap:12px; }}
  .stat {{ background:var(--panel); border:1px solid var(--border);
          border-radius:10px; padding:14px 16px; }}
  .stat-value {{ font-size:22px; font-weight:700; }}
  .stat-label {{ color:var(--muted); font-size:12.5px; margin-top:2px; }}
  .stat-value.accent {{ color:var(--accent); }}
  svg {{ width:100%; height:auto; }}
  .panel {{ background:var(--panel); border:1px solid var(--border);
           border-radius:10px; padding:16px 18px; }}
  .tags {{ display:grid; gap:7px; }}
  .tag {{ display:grid; grid-template-columns:1fr 60% 44px; gap:10px;
         align-items:center; }}
  .tag-name {{ font-size:13.5px; overflow:hidden; text-overflow:ellipsis;
              white-space:nowrap; }}
  .tag-bar {{ background:var(--panel2); border-radius:6px;
          height:10px; overflow:hidden; }}
  .tag-fill {{ display:block; height:100%;
         background:linear-gradient(90deg,var(--accent2),var(--accent));
         border-radius:6px; }}
  .tag-count {{ text-align:right; color:var(--muted);
         font-size:12.5px; }}
  .chips {{ display:flex; flex-wrap:wrap; gap:8px; }}
  .chip {{ background:var(--panel2); border:1px solid var(--border);
         padding:6px 12px; border-radius:999px; font-size:13.5px; }}
  .chip-star {{ color:#f7b32b; }} .chip em {{ color:var(--muted);
         font-style:normal; font-size:12px; }}
  .year {{ margin:6px 0 18px; }}
  .year-label {{ text-align:center; color:var(--muted); font-size:13px;
         margin-bottom:4px; letter-spacing:.08em; }}
  table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
  th, td {{ padding:7px 10px; border-bottom:1px solid var(--border);
         text-align:left; white-space:nowrap; }}
  th {{ position:sticky; top:0; background:var(--panel); }}
  .t-gap {{ text-align:right; }}
  .gap {{ font-weight:700; }} .gap-ahead {{ color:var(--accent); }}
  .gap-late {{ color:var(--late); }} .gap-grey {{ color:var(--muted); }}
  .t-title {{ white-space:normal; min-width:180px; }}
  .t-tags {{ color:var(--muted); white-space:normal; max-width:220px; }}
  .scroll {{ overflow:auto; max-height:520px; border:1px solid var(--border);
         border-radius:10px; }}
  .subtle {{ color:var(--muted); font-size:13px; }}
  footer {{ color:var(--muted); font-size:12.5px; margin-top:48px;
         border-top:1px solid var(--border); padding-top:14px; }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>sirconanad<span class="dot" aria-hidden="true">·</span>cinema</h1>
  <div class="sub">Letterboxd highlights · {generated}</div>
</header>

<h2>Overview</h2>
<div class="stats">{stat_cards}</div>

<h2>Cinema days <small>(films per day, per year)</small></h2>
<div class="panel">{year_graphs}</div>

<h2>Monthly activity <small>(films per month)</small></h2>
<div class="panel">{monthly}</div>

<h2>Most-used tags</h2>
<div class="panel"><div class="tags">{tags}</div></div>

<h2>Favourites <small>(5★ &amp; liked)</small></h2>
<div class="panel"><div class="chips">{favorites}</div></div>

<h2>Sneak / OV-sneak vs. German cinema release <small>({sneak_count} entries, on average {avg_gap} days early)</small></h2>
<div class="scroll"><table>
  <thead><tr><th scope="col">Film</th><th scope="col">Year</th>
  <th scope="col">Watched</th>
  <th scope="col">Cinema release (DE)</th><th scope="col" class="t-gap">Gap</th><th scope="col">Tags</th></tr></thead>
  <tbody>{sneak_rows}</tbody>
</table></div>

<footer>Generated by <code>generate_site.py</code> on {generated}.
  Data: Letterboxd diary (export from 2026-11-19), TMDB premiere dates.
  <br>All titles are © their respective copyright holders.</footer>

</div>
</body>
</html>
"""


def main(args: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default="site/index.html")
    ap.add_argument("--diary", default="diary.json")
    ap.add_argument("--sneaks", default=str(SNEAK_CSV_DEFAULT))
    ns = ap.parse_args(args)

    diary = load_diary(Path(ns.diary))
    sneaks = load_sneaks(Path(ns.sneaks))
    generated = date.today().isoformat()

    with_gap = [s for s in sneaks if s.get("gap_days") is not None]
    avg_gap = (sum(s["gap_days"] for s in with_gap) / len(with_gap)
               if with_gap else 0)

    out = Path(ns.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PAGE.format(
        generated=generated,
        stat_cards=build_stat_cards(diary, sneaks),
        year_graphs=yearly_graphs_html(diary),
        monthly=monthly_line_svg(diary),
        tags=tags_html(diary),
        favorites=favorites_html(diary),
        sneak_count=len(sneaks),
        avg_gap=f"{avg_gap:.1f}",
        sneak_rows=sneak_rows_html(sneaks),
    ), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main([]))
