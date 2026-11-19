#!/usr/bin/env python3
"""Generate a static highlights page from the Letterboxd diary.

Reads the repository's ``diary.json`` and ``sneak-gap.csv`` and writes a
single self-contained ``site/index.html`` (inline CSS + inline contribution
graph SVG) suitable for GitHub Pages.

Examples:
    python3 generate_site.py            # write site/index.html
    python3 generate_site.py -o out.html
"""

import argparse
import csv
import html
import json
from collections import Counter
from datetime import date
from pathlib import Path

import letterboxd_graph as graph


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


def contribution_svg(diary: list[dict]) -> str:
    counts = Counter(e["when"] for e in diary)
    start = date.fromisoformat(min(counts))
    end = date.today()
    levels = graph.build_levels(counts)
    return graph.render_svg(levels, counts, start, end)


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
        card("accent", f"{n}", f"Filme seit {min(years)} ({len(years)} Jahre)"),
        card("accent", f"{avg:.2f}", "Ø Bewertung (5-Skala)"),
        card("accent", f"{liked}", "geherzt / geliked"),
        card("accent", f"{rewatch}", "Rewatches"),
        card("accent", f"{avg_gap:.1f} Tage", "Sneak vor Kinostart"),
        card("accent", f"{len(with_gap)}/{len(sneaks)}", "Sneaks mit Kinostart"),
    ])


def tags_html(diary: list[dict], top: int = 12) -> str:
    tag_count = Counter(t for e in diary for t in (e.get("tags") or []))
    items = tag_count.most_common(top)
    if not items:
        return "<p>Keine Tags.</p>"
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
    favs = sorted(
        (e for e in diary if e.get("rating") == 5 and e.get("liked")),
        key=lambda e: e["when"], reverse=True)
    if not favs:
        return "<p>Keine Favoriten gefunden.</p>"
    cells = []
    for e in favs[:limit]:
        cells.append(f'<span class="chip"><span class="chip-star">★</span>'
                     f"{escape(e['title'])} <em>{e['when']}</em></span>")
    return '\n'.join(cells) + \
        (f'<p class="subtle">… und {len(favs) - limit} weitere '
         f"5-Sterne-Filme.</p>" if len(favs) > limit else "")


def monthly_html(diary: list[dict]) -> str:
    months = Counter(e["when"][:7] for e in diary)
    ordered = sorted(months)
    if not ordered:
        return ""
    mx = max(months.values())
    rows = []
    for m in ordered:
        n = months[m]
        w = max(3, round(n / mx * 100))
        rows.append(
            f'<div class="month"><span class="month-name">{m}</span>'
            f'<span class="month-bar"><span class="month-fill" '
            f"style='width:{w}%'></span></span>"
            f"<span class='month-count'>{n}</span></div>")
    return "\n".join(rows)


def sneak_rows_html(sneaks: list[dict]) -> str:
    ordered = sorted(sneaks,
                     key=lambda s: (s["gap_days"] is None,
                                    -(s["gap_days"] or 0)))
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
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sirconanad · Letterboxd Highlights</title>
<style>
  :root {{ --bg:#0f1115; --panel:#171a21; --panel2:#1e222b;
          --text:#e7e9ee; --muted:#9aa3b2; --accent:#7ee08e;
          --accent2:#3fb950; --late:#f58d8d; --border:#2a2f3a; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,
         Arial,sans-serif; background:var(--bg); color:var(--text); }}
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
  .tag-bar, .month-bar {{ background:var(--panel2); border-radius:6px;
         height:10px; overflow:hidden; }}
  .tag-fill, .month-fill {{ display:block; height:100%;
         background:linear-gradient(90deg,var(--accent2),var(--accent));
         border-radius:6px; }}
  .tag-count, .month-count {{ text-align:right; color:var(--muted);
         font-size:12.5px; }}
  .chips {{ display:flex; flex-wrap:wrap; gap:8px; }}
  .chip {{ background:var(--panel2); border:1px solid var(--border);
         padding:6px 12px; border-radius:999px; font-size:13.5px; }}
  .chip-star {{ color:#f7b32b; }} .chip em {{ color:var(--muted);
         font-style:normal; font-size:12px; }}
  .months {{ display:grid; gap:4px; }}
  .month {{ display:grid; grid-template-columns:70px 1fr 40px; gap:10px;
         align-items:center; font-size:12.5px; }}
  .month-name, .month-count {{ color:var(--muted); }}
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
  <h1>sirconanad<span class="dot">·</span>cinema</h1>
  <div class="sub">Letterboxd-Highlights · {generated}</div>
</header>

<h2>Im Überblick</h2>
<div class="stats">{stat_cards}</div>

<h2>Kinotage <small>(Filme pro Tag seit Anfang des Diaries)</small></h2>
<div class="panel">{graph_svg}</div>

<h2>Monatliche Aktivität</h2>
<div class="panel"><div class="months">{monthly}</div></div>

<h2>Beliebteste Tags</h2>
<div class="panel"><div class="tags">{tags}</div></div>

<h2>Favoriten <small>(5★ &amp; herz)</small></h2>
<div class="panel"><div class="chips">{favorites}</div></div>

<h2>Sneak / OV-Sneak vs. deutscher Kinostart <small>({sneak_count} Einträge, Ø {avg_gap} Tage vorher)</small></h2>
<div class="scroll"><table>
  <thead><tr><th>Film</th><th>Jahr</th><th>Gesehen</th>
  <th>Kinostart (DE)</th><th class="t-gap">Gap</th><th>Tags</th></tr></thead>
  <tbody>{sneak_rows}</tbody>
</table></div>

<footer>Generiert von <code>generate_site.py</code> am {generated}.
  Daten: Letterboxd-Diary (Export vom 2026-11-19), TMDB-Premieredaten.
  <br>Alle Titel sind © ihrer jeweiligen Rechteinhaber.</footer>
</div>
</body>
</html>
"""


def main(args: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default="site/index.html")
    ap.add_argument("--diary", default="diary.json")
    ap.add_argument("--sneaks", default="sneak-gap.csv")
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
        graph_svg=contribution_svg(diary),
        monthly=monthly_html(diary),
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
