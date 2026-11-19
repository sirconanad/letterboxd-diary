#!/usr/bin/env python3
"""Letterboxd diary -> GitHub-style contribution graph.

Pulls a profile's diary through the authenticated Letterboxd API
(``GET /log-entries`` with ``where=HasDiaryDate``) and renders the number of
films watched per day as a contribution graph, similar to GitHub's. An
optional ``--source rss`` path uses the public RSS feed instead (recent
activity only, no authentication required).

Examples:
    python3 letterboxd_graph.py https://letterboxd.com/sirconanad/            # diary, current year
    python3 letterboxd_graph.py https://letterboxd.com/sirconanad/ --year 2025 --year 2026
    python3 letterboxd_graph.py https://letterboxd.com/sirconanad/ --all     # entire diary
    python3 letterboxd_graph.py https://letterboxd.com/sirconanad/ --source rss --all
    python3 letterboxd_graph.py https://letterboxd.com/sirconanad/ --svg graph.svg

Authentication (OAuth2, see letterboxd_api.py):

    export LETTERBOXD_CLIENT_ID=...        # registered API key / client id
    export LETTERBOXD_CLIENT_SECRET=...
    python3 -m letterboxd_api login        # one-time browser approval

The API token is cached in ``.letterboxd_tokens.json`` next to this repo.
"""

import argparse
import re
import sys
from collections import Counter
from datetime import date, timedelta
from xml.etree import ElementTree

import urllib.error
import urllib.request

from letterboxd_api import LetterboxdData, LetterboxdError

LETTERBOXD_NS = "https://letterboxd.com"

GITHUB_GREENS = {
    0: "#ebedf0",
    1: "#9be9a8",
    2: "#40c463",
    3: "#30a14e",
    4: "#216e39",
}
NO_COLOR_CHARS = {0: " ", 1: ".", 2: "+", 3: "*", 4: "#"}

RSS_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0"),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# ---------------------------------------------------------------- fetching


def fetch_rss(user: str) -> bytes:
    url = f"https://letterboxd.com/{user}/rss/"
    req = urllib.request.Request(url, headers=RSS_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"could not fetch RSS ({exc.code}); use the "
                           f"diary (API) source instead") from exc


def parse_rss(data: bytes) -> list[tuple[date, str, float | None, bool]]:
    """Parse diary entries from the RSS document."""
    root = ElementTree.fromstring(data)
    entries = []
    for item in root.iter("item"):
        watched = item.findtext(f"{{{LETTERBOXD_NS}}}watchedDate")
        if not watched or not watched.strip():
            continue
        title = item.findtext("title") or "<untitled>"
        rating = item.findtext(f"{{{LETTERBOXD_NS}}}memberRating")
        rating = float(rating) if rating else None
        rewatch = item.findtext(f"{{{LETTERBOXD_NS}}}rewatch") == "Yes"
        try:
            when = date.fromisoformat(watched.strip())
        except ValueError:
            continue
        entries.append((when, title.strip(), rating, rewatch))
    return entries


def username_from_url(url: str) -> str:
    m = re.search(r'letterboxd\.com/([^/?#]+)', url)
    if not m:
        raise ValueError(f"not a Letterboxd URL: {url}")
    return m.group(1)


def load_entries(source: str, user: str, scopes: list[int | None],
                 pause: float) -> list:
    """Fetch diary entries as [(date, title, rating, rewatch), ...].

    ``scopes`` holds years to fetch, or ``[None]`` for the whole diary.
    """
    if source == "rss":
        try:
            data = fetch_rss(user)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"could not fetch RSS: {exc}") from exc
        entries = parse_rss(data)
        return [(d, t, r, w) for d, t, r, w in entries
                if None in scopes or d.year in scopes]

    api = LetterboxdData()
    lid = api.member_lid(user)
    raw = api.log_entries(member=lid, where=("HasDiaryDate",), sort="Date")
    entries = []
    for le in raw:
        diary = le.get("diaryDetails") or {}
        prod = le.get("production") or {}
        when_text = diary.get("diaryDate")
        if not when_text:
            continue
        try:
            when = date.fromisoformat(when_text)
        except ValueError:
            continue
        if None not in scopes and when.year not in scopes:
            continue
        entries.append((when, (prod.get("name") or "?").strip(),
                        le.get("rating"), bool(diary.get("rewatch"))))
    print(f"api: {len(entries)} diary entries for {user}",
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
        description="Render a Letterboxd diary as a GitHub-style "
                    "contribution graph.")
    parser.add_argument("url", help="Letterboxd profile URL, e.g. "
                                    "https://letterboxd.com/USER/")
    parser.add_argument("--source", choices=("diary", "rss"),
                        default="diary",
                        help="diary = API (authenticated, full history); "
                             "rss = public recent-activity feed")
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
    parser.add_argument("--pause", type=float, default=2.0,
                        help="Ignored for the API source; kept for the RSS "
                             "path and CLI compatibility")
    parser.add_argument("--svg", metavar="FILE",
                        help="Also write an SVG render to FILE")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable ANSI colors in terminal output")
    args = parser.parse_args(argv)

    try:
        user = username_from_url(args.url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    today = date.today()
    scopes = [None] if args.all else sorted(args.year or [today.year])
    try:
        entries = load_entries(args.source, user, scopes, args.pause)
    except (RuntimeError, LetterboxdError) as exc:
        print(f"error: {exc}", file=sys.stderr)
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
