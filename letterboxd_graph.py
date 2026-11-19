#!/usr/bin/env python3
"""Letterboxd diary -> GitHub-style contribution graph.

Reads a fresh Letterboxd data export (``letterboxd-*.zip``, from
https://letterboxd.com/settings/data/) and renders the number of films
watched per day as a contribution graph, similar to GitHub's. No HTML
scraping and no Letterboxd API -- the export is the only data source.

Examples:
    python3 letterboxd_graph.py letterboxd-....zip                    # current year
    python3 letterboxd_graph.py letterboxd-....zip --year 2025 --year 2026
    python3 letterboxd_graph.py letterboxd-....zip --all             # entire diary
    python3 letterboxd_graph.py letterboxd-....zip --all --svg graph.svg
"""

import argparse
import csv
import io
import re
import sys
import zipfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

GITHUB_GREENS = {
    0: "#ebedf0",
    1: "#9be9a8",
    2: "#40c463",
    3: "#30a14e",
    4: "#216e39",
}
NO_COLOR_CHARS = {0: " ", 1: ".", 2: "+", 3: "*", 4: "#"}

# ---------------------------------------------------------------- fetching


def find_export() -> Path | None:
    """Locate a Letterboxd backup zip near the repo."""
    for d in (Path.cwd(), Path.cwd().parent):
        hits = sorted(d.glob("letterboxd-*.zip"))
        if hits:
            return hits[0]
    return None


def diary_rows(path: Path) -> list[dict]:
    if not zipfile.is_zipfile(path):
        raise ValueError(f"{path} is not a Letterboxd export zip")
    with zipfile.ZipFile(path) as zf:
        raw = zf.read("diary.csv").decode("utf-8", "replace")
    return list(csv.DictReader(io.StringIO(raw)))


def load_entries(path: Path, scopes: list[int | None]) -> list:
    """Fetch diary entries as [(date, title, rating, rewatch), ...].

    ``scopes`` holds years to fetch, or ``[None]`` for the whole diary.
    """
    entries = []
    for r in diary_rows(path):
        when_text = (r.get("Watched Date") or "").strip()
        title = (r.get("Name") or "?").strip()
        if not when_text or not title:
            continue
        try:
            when = date.fromisoformat(when_text)
        except ValueError:
            continue
        if None not in scopes and when.year not in scopes:
            continue
        rating_text = (r.get("Rating") or "").strip()
        rating = float(rating_text) / 2 if rating_text else None
        rewatch = (r.get("Rewatch") or "").strip().lower() == "yes"
        entries.append((when, title, rating, rewatch))
    entries.sort()
    print(f"{path.name}: {len(entries)} diary entry/entries",
          file=sys.stderr)
    return entries


# ---------------------------------------------------------------- rendering


def build_levels(counts: Counter) -> dict[date, int]:
    """Map each day with >0 watches to a 1-4 intensity level (ranked)."""
    uniq = sorted(set(counts.values()))
    if not uniq:
        return {}
    rank = {v: i for i, v in enumerate(uniq)}
    n = len(uniq)
    return {d: (rank[c] * 4 // n) + 1 for d, c in counts.items()}


def sunday_on_or_before(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)


def make_grid(start: date, end: date) -> list[list[date]]:
    """Columns (weeks, Sun..Sat) x rows (7 weekdays)."""
    weeks = []
    col = sunday_on_or_before(start)
    while col <= end:
        weeks.append([col + timedelta(days=i) for i in range(7)])
        col += timedelta(days=7)
    return weeks


WEEKDAY_LABELS = "SMTWTFS"  # Sunday first, like GitHub


def hex_to_rgb(hexc: str) -> str:
    hexc = hexc.lstrip("#")
    return ";".join(str(int(hexc[i:i + 2], 16)) for i in (0, 2, 4))


def render_terminal(levels: dict[date, int], counts: Counter, start: date,
                    end: date, color: bool) -> str:
    grid = make_grid(start, end)
    cw = 2 if color else 3

    def cell_for(day: date) -> str:
        if day < start or day > end:
            return " " * cw
        lvl = levels.get(day, 0)
        if color:
            return ("\x1b[48;2;" + hex_to_rgb(GITHUB_GREENS[lvl]) + "m"
                    + " " * cw + "\x1b[0m")
        return NO_COLOR_CHARS[lvl].center(cw)

    # Month labels placed exactly at column starts (no drift).
    width = 2 + len(grid) * cw
    month = [" "] * width
    for i, week in enumerate(grid):
        first = next((d for d in week if d.day == 1), None)
        if first is not None:
            x = 2 + i * cw
            for j, ch in enumerate(first.strftime("%b")):
                if x + j < width:
                    month[x + j] = ch
    lines = ["".join(month).rstrip()]
    for r in range(7):
        row = WEEKDAY_LABELS[r] + " " + "".join(cell_for(week[r])
                                               for week in grid)
        lines.append(row)
    return "\n".join(lines) + "\n"


def render_svg(levels: dict[date, int], counts: Counter, start: date,
               end: date) -> str:
    grid = make_grid(start, end)
    ncols = len(grid)
    cell, gap, pad = 11, 3, 12
    lane = cell + gap
    width = pad * 2 + ncols * lane - gap
    height = pad * 2 + 7 * lane
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
           f'height="{height}" viewBox="0 0 {width} {height}">']
    out.append('<rect width="100%" height="100%" fill="#ffffff"/>')

    for i, week in enumerate(grid):
        for day in week:
            if day.day == 1:
                out.append(f'<text x="{pad + i * lane}" y="{pad - 4}" '
                           f'font-family="sans-serif" font-size="11" '
                           f'fill="#767676">{day.strftime("%b")}</text>')
                break

    for r in range(7):
        if r % 2 == 0:
            out.append(f'<text x="{pad - 4}" y="{pad + r * lane + 9}" '
                       f'font-family="sans-serif" font-size="10" '
                       f'text-anchor="end" fill="#767676">'
                       f'{WEEKDAY_LABELS[r]}</text>')

    for wi, week in enumerate(grid):
        for r, day in enumerate(week):
            if day < start or day > end:
                continue
            x = pad + wi * lane
            y = pad + r * lane
            n = counts.get(day, 0)
            title = (f"{n} film{'s' if n != 1 else ''} watched on "
                     f"{day.strftime('%d %b %Y')}") if n else day.isoformat()
            out.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" '
                       f'rx="2.5" fill="{GITHUB_GREENS[levels.get(day, 0)]}">'
                       f'<title>{title}</title></rect>')

    out.append("</svg>")
    return "\n".join(out) + "\n"


# -------------------------------------------------------------------- main


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Render a Letterboxd diary export as a GitHub-style "
                    "contribution graph.")
    parser.add_argument("export", nargs="?", help="Path to a Letterboxd "
                        "data export zip (auto-detected next to the repo)")
    parser.add_argument("--year", action="append", type=int, metavar="YYYY",
                        help="Restrict to year(s); repeatable. "
                             "Default: current year.")
    parser.add_argument("--all", action="store_true",
                        help="Whole diary, every year")
    parser.add_argument("--start", help="Graph window start (YYYY-MM-DD)")
    parser.add_argument("--end", help="Graph window end (YYYY-MM-DD); "
                                      "default: today")
    parser.add_argument("--days", type=int,
                        help="Graph window: last N days")
    parser.add_argument("--months", type=int,
                        help="Graph window: last N months")
    parser.add_argument("--svg", metavar="FILE",
                        help="Also write an SVG render to FILE")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable ANSI colors in terminal output")
    args = parser.parse_args(argv)

    path = Path(args.export) if args.export else find_export()
    if path is None or not path.exists():
        print("error: no Letterboxd data export found; download one from "
              "https://letterboxd.com/settings/data/ and pass its path or "
              "place it next to the repo", file=sys.stderr)
        return 1

    today = date.today()
    scopes = [None] if args.all else sorted(args.year or [today.year])
    try:
        entries = load_entries(path, scopes)
    except (ValueError, OSError, KeyError, csv.Error) as exc:
        print(f"error: could not read export: {exc}", file=sys.stderr)
        return 1

    if not entries:
        print("no diary entries found for the requested scope",
              file=sys.stderr)
        return 1

    counts = Counter(d for d, _, _, _ in entries)
    dates = sorted(counts)

    window_given = bool(args.end or args.days or args.months)
    end = args.end and date.fromisoformat(args.end) or today
    if args.days:
        start = end - timedelta(days=args.days - 1)
    elif args.months:
        start = end - timedelta(days=int(args.months * 30.4375))
    elif args.start:
        start = date.fromisoformat(args.start)
    elif args.all:
        start = dates[0]
    else:
        # Snap to the requested year(s) unless an explicit window was given.
        start = date(min(scopes), 1, 1)
        if not window_given:
            finish = date(max(scopes), 12, 31)
            if finish < today:
                end = finish

    if start > end:
        print("error: start date is after end date", file=sys.stderr)
        return 1

    levels = build_levels(counts)
    color = not args.no_color and sys.stdout.isatty()
    graph = render_terminal(levels, counts, start, end, color)
    print(graph)

    busiest = counts.most_common(1)[0]
    rated = [r for _, _, r, _ in entries if r is not None]
    rewatched = sum(1 for _, _, _, w in entries if w)
    avg = sum(rated) / len(rated) if rated else 0
    print(f"Films watched: {len(entries)} across {len(dates)} day(s) "
          f"({rewatched} rewatch)")
    print(f"Busiest day: {busiest[0]} ({busiest[1]})")
    if rated:
        print(f"Avg rating: {avg:.2f} ({len(rated)} rated)")
    print(f"Shown window: {start} .. {end}")

    if args.svg:
        try:
            with open(args.svg, "w") as fh:
                fh.write(render_svg(levels, counts, start, end))
            print(f"Wrote SVG: {args.svg}")
        except OSError as exc:
            print(f"error: could not write SVG: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
