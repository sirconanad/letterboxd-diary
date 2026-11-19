#!/usr/bin/env python3
"""Fetch a Letterboxd diary and backdate one git commit per film.

- Scrapes the public diary pages (Cloudflare-protected; uses curl_cffi
  browser impersonation, falls back to the curl binary).
- Incremental: only pulls entries that are not yet committed. The diary is
  served newest-first, so pages are fetched until one contains no new entry
  (everything older is already committed).
- Writes ``diary.json`` (a merged record of everything pulled so far) and
  one markdown file per new film under ``films/``.
- Creates one git commit per new film in chronological order, with both the
  author and committer dates set to noon (local time) on the day the film
  was watched, so GitHub's contribution graph shows it on that day.

Requires a git repo. Commits are authored as ``sirconanad
<sirconanad@cock.li>``.
Already-committed entries are never re-fetched beyond the first page that
falls entirely within the committed region.
"""

import argparse
import csv
import html
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dtime
from pathlib import Path

try:
    from curl_cffi import requests as cffi_requests
except Exception:  # optional dependency
    cffi_requests = None

USER = "sirconanad"
AUTHOR_NAME = "sirconanad"
AUTHOR_EMAIL = "sirconanad@cock.li"
BASE = f"https://letterboxd.com/{USER}/diary"
PAUSE = 1.2          # seconds between page fetches (be a good citizen)
MAX_PAGES = 200      # safety cap
COMMIT_HOUR = 12     # noon local, keeps US/Pacific day aligned

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}

MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"))}


# ---------------------------------------------------------------- fetching


def _fetch_curl(url: str, referer: str):
    cmd = ["curl", "-sS", "-L", "--max-time", "40"]
    for key, value in BROWSER_HEADERS.items():
        cmd += ["-H", f"{key}: {value}"]
    cmd += ["-H", f"Referer: {referer}"]
    cmd += ["-w", "\n__CODE__%{http_code}", url]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
    except Exception:
        return None
    out = proc.stdout.split(b"__CODE__")
    if len(out) != 2:
        return None
    try:
        status = int(out[1])
    except ValueError:
        return None
    return status, out[0]


def fetch_page(url: str, referer: str, retries: int = 5,
               base_sleep: float = 2.0):
    """Return (http_status, text). Retries with backoff on 403/429."""
    for attempt in range(retries):
        if cffi_requests is not None:
            try:
                r = cffi_requests.get(url, impersonate="chrome",
                                      headers=BROWSER_HEADERS,
                                      referer=referer, timeout=40)
                data = r.content
                status = r.status_code
                if status == 200 and b"Just a moment" in data[:20000]:
                    status = 403
                if status not in (403, 429):
                    return status, data
            except Exception:
                pass
        res = _fetch_curl(url, referer)
        if res is not None and res[0] not in (403, 429):
            return res[0], res[1]
        time.sleep(base_sleep * (attempt + 1))
    return 403, b""


# ----------------------------------------------------------------- parsing


@dataclass
class Entry:
    when: date
    title: str
    rating: float | None
    rewatch: bool
    liked: bool = False
    tags: list[str] = field(default_factory=list)


def key_of(e: Entry) -> tuple[str, str]:
    return (e.when.isoformat(), safe_filename(e.title).lower())


def parse_diary_page(text: str) -> tuple[list[Entry], bool]:
    """Parse one diary page -> (entries, has_next_page)."""
    entries: list[Entry] = []
    cur_month = cur_year = None
    start = text.find('id="diary-table"')
    end = text.find("</table>", start) if start != -1 else -1
    if start != -1 and end != -1:
        table = text[start:end]
        for row in re.findall(r'<tr class="diary-entry-row.*?</tr>',
                              table, re.S):
            ym = re.search(r'<a class="month"[^>]*>([A-Za-z]{3})</a>', row)
            yy = re.search(r'<a class="year"[^>]*>(\d{4})</a>', row)
            dd = re.search(r'<a class="daydate"[^>]*>(\d{1,2})</a>', row)
            if ym and yy:
                try:
                    cur_month = MONTHS[ym.group(1).lower()]
                except KeyError:
                    continue
                cur_year = int(yy.group(1))
            if not dd or cur_month is None or cur_year is None:
                continue
            try:
                when = date(cur_year, cur_month, int(dd.group(1)))
            except ValueError:
                continue
            m = re.search(r'data-item-full-display-name="([^"]+)"', row)
            title = html.unescape(
                re.sub(r"\s*\(\d{4}\)\s*$", "", m.group(1))) if m else "?"
            rating = None
            m = re.search(r'rateit-field[^>]*value="(\d+)"', row) \
                or re.search(r'rating rated-(\d+)', row)
            if m:
                rating = int(m.group(1)) / 2
                if rating <= 0:
                    rating = None
            rew = re.search(r'class="[^"]*js-td-rewatch[^"]*"', row)
            rewatch = rew is not None and "icon-status-off" not in rew.group(0)
            liked = "icon-liked" in row
            entries.append(Entry(when, title, rating, rewatch, liked))
    has_next = re.search(r'class="next" href="[^"]+"', text) is not None
    return entries, has_next


def fetch_new_entries(known: Counter, full: bool = False) -> list[Entry]:
    """Fetch diary pages (newest-first) until everything newer is known.

    ``known`` is a multiset of (date, base-title) already committed; each
    matching diary row consumes one entry. Returns only rows with nothing
    left to consume. Stops at the first page that contains no new entry (all
    older pages must be committed too), unless ``full`` is set.
    """
    new_entries: list[Entry] = []
    for page in range(1, MAX_PAGES + 1):
        url = BASE if page == 1 else f"{BASE}/page/{page}/"
        status, data = fetch_page(url, referer=BASE)
        if status != 200:
            print(f"error: blocked fetching {url} ({status})", file=sys.stderr)
            break
        text = data.decode("utf-8", "replace")
        page_entries, has_next = parse_diary_page(text)
        page_new = []
        for e in page_entries:
            key = key_of(e)
            if known.get(key, 0) > 0:
                known[key] -= 1
                continue
            page_new.append(e)
        new_entries.extend(page_new)
        word = "entry" if len(page_new) == 1 else "entries"
        print(f"page {page}: {len(page_entries)} total, "
              f"{len(page_new)} new {word}", file=sys.stderr)
        if not has_next or not page_entries:
            break
        if not full and not page_new:
            print("  stopping (rest already committed)", file=sys.stderr)
            break
        time.sleep(PAUSE)
    return new_entries


# ------------------------------------------------------------------- files


def find_export() -> Path | None:
    """Locate a Letterboxd backup zip (or loose diary.csv) near the repo."""
    patterns = ("letterboxd-*.zip",)
    for d in (Path.cwd(), Path.cwd().parent):
        for pat in patterns:
            hits = sorted(d.glob(pat))
            if hits:
                return hits[0]
    diary = Path.cwd().parent / "diary.csv"
    return diary if diary.exists() else None


def load_export_tags(path: Path) -> dict[tuple[str, str], deque]:
    """Map (watched-date, name) -> deque of tag lists from the backup.

    Letterboxd's diary page does not expose tags; they only come from the
    official data export (``diary.csv`` inside the backup zip).
    """
    result: dict[tuple[str, str], deque] = defaultdict(deque)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            raw = zf.read("diary.csv").decode("utf-8", "replace")
    else:
        raw = path.read_text(encoding="utf-8", errors="replace")
    for row in csv.DictReader(io.StringIO(raw)):
        when = (row.get("Watched Date") or "").strip()
        name = (row.get("Name") or "").strip()
        tags = [t.strip() for t in (row.get("Tags") or "").split(",")
                if t.strip()]
        if when and name:
            result[(when, name)].append(tags)
    return result


def apply_tags(entries: list[Entry], tagmap: dict[tuple[str, str], deque]) -> int:
    matched = 0
    for e in entries:
        bucket = tagmap.get((e.when.isoformat(), e.title))
        if bucket:
            e.tags = bucket.popleft()
            matched += 1
    return matched


def safe_filename(title: str) -> str:
    name = re.sub(r'[/\\:*?"<>|]', "-", title)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name or "untitled"


def film_body(e: Entry) -> str:
    lines = [f"# {e.title}", "", f"- **Date:** {e.when.isoformat()}"]
    if e.rating is not None:
        lines.append(f"- **Rating:** {e.rating:g}/5")
    if e.rewatch:
        lines.append("- **Rewatch:** yes")
    lines.append(f"- **Liked:** {'yes' if e.liked else 'no'}")
    if e.tags:
        lines.append(f"- **Tags:** {', '.join(e.tags)}")
    lines.append("")
    return "\n".join(lines) + "\n"


def film_doc(e: Entry, n: int) -> str:
    """Deterministic filename for entry e, with n = prior same-title count."""
    base = safe_filename(e.title)
    name = f"{base} ({n + 1})" if n else base
    return f"{e.when.isoformat()} - {name}.md"


def write_new_entries(entries: list[Entry], films_dir: Path,
                      base_counts: dict[str, int]) -> list[Path]:
    """Write files for new entries (chronological order); skip committed."""
    seen = dict(base_counts)  # continue duplicate numbering across runs
    paths: list[Path] = []
    for e in entries:
        base_lower = safe_filename(e.title).lower()
        n = seen.get(base_lower, 0)
        seen[base_lower] = n + 1
        path = films_dir / film_doc(e, n)
        path.write_text(film_body(e), encoding="utf-8")
        paths.append(path)
    return paths


# -------------------------------------------------------------------- git


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def git_identity() -> dict[str, str]:
    return {"GIT_AUTHOR_NAME": AUTHOR_NAME, "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
            "GIT_COMMITTER_NAME": AUTHOR_NAME, "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL}


def author_env(identity: dict[str, str], when: date) -> dict[str, str]:
    local = datetime.combine(when, dtime(COMMIT_HOUR)).astimezone()
    stamp = local.strftime("%Y-%m-%d %H:%M:%S %z")
    env = dict(os.environ, **identity)
    env["GIT_AUTHOR_DATE"] = stamp
    env["GIT_COMMITTER_DATE"] = stamp
    return env


def commit_files(identity: dict[str, str], when: date, message: str,
                 files: list[Path]):
    """Stage and commit; no-ops if nothing changed (idempotent re-runs)."""
    git("add", "--", *[str(f) for f in files])
    if subprocess.run(["git", "diff", "--cached", "--quiet"],
                      capture_output=True).returncode == 0:
        return
    env = author_env(identity, when)
    subprocess.run(["git", "commit", "-m", message],
                   env=env, check=True, capture_output=True)


def committed_state() -> tuple[Counter, dict[str, int]]:
    """Return (known_counts, base_counts) from film files in HEAD's tree.

    Uses ``-z`` so non-ASCII filenames come back raw (not quoted/escaped).
    ``known_counts`` is a multiset: a film watched twice on one day yields
    two file names that must both be counted.
    """
    proc = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD", "--", "films/"],
        capture_output=True)
    if proc.returncode != 0:  # no HEAD yet -> nothing committed
        return Counter(), {}
    known: Counter = Counter()
    counts: dict[str, int] = {}
    for name in proc.stdout.split(b"\0"):
        if not name:
            continue
        line = name.decode("utf-8", "replace")
        if line.startswith("films/"):
            line = line[len("films/"):]
        if not line.endswith(".md"):
            continue
        m = re.match(r"^(\d{4}-\d{2}-\d{2}) - (.+)\.md$", line)
        if not m:
            continue
        base = re.sub(r" \(\d+\)$", "", m.group(2))
        base_lower = base.lower()
        known[(m.group(1), base_lower)] += 1
        counts[base_lower] = counts.get(base_lower, 0) + 1
    return known, counts


def load_diary_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


# -------------------------------------------------------------------- main


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", default=USER)
    ap.add_argument("--no-commit", action="store_true",
                    help="fetch and write files but do not commit")
    ap.add_argument("--export", metavar="PATH",
                    help="Path to Letterboxd backup zip or diary.csv "
                         "(source of tags)")
    ap.add_argument("--full", action="store_true",
                    help="ignore committed state; fetch every page")
    args = ap.parse_args(argv)

    identity = git_identity() if not args.no_commit else {}
    repo = Path.cwd()
    films_dir = repo / "films"
    films_dir.mkdir(exist_ok=True)

    known, base_counts = committed_state()
    if args.full:
        known, base_counts = Counter(), {}
    print(f"already committed: {sum(known.values())} film(s)", file=sys.stderr)

    # Newest-first from the site -> chronological for oldest-first commits.
    new_entries = fetch_new_entries(known, full=args.full)
    if not new_entries:
        print("no new diary entries to add", file=sys.stderr)
        return 0
    new_entries.reverse()
    print(f"{len(new_entries)} new film(s): "
          f"{new_entries[0].when} .. {new_entries[-1].when}", file=sys.stderr)

    # Likes are scraped from the diary page; tags come from the data export.
    export_path = Path(args.export) if args.export else find_export()
    if export_path is not None and export_path.exists():
        tagmap = load_export_tags(export_path)
        ntags = apply_tags(new_entries, tagmap)
        print(f"tags merged from {export_path.name}: "
              f"{ntags}/{len(new_entries)} entry(ies)", file=sys.stderr)
    else:
        print("note: no Letterboxd export found next to the repo; "
              "tags will be empty (pass --export PATH)", file=sys.stderr)

    # Merge into diary.json (kept as a complete chronological record).
    merged = load_diary_json(repo / "diary.json")
    merged_keys = {(e.get("when"), e.get("title")) for e in merged}
    for e in new_entries:
        key = (e.when.isoformat(), e.title)
        if key not in merged_keys:
            merged.append(asdict(e))
    (repo / "diary.json").write_text(
        json.dumps(merged, indent=2, default=str) + "\n", encoding="utf-8")

    film_paths = write_new_entries(new_entries, films_dir, base_counts)
    print(f"wrote {len(film_paths)} new film file(s)", file=sys.stderr)

    if args.no_commit:
        return 0

    # Root commit (only if the repo has no commits yet).
    has_head = bool(subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
        capture_output=True).returncode == 0)
    if not has_head:
        commit_files(identity, date.today(), "Import Letterboxd diary",
                     [repo / f for f in
                      ("README.md", "build.py", "diary.json")])

    for e, path in zip(new_entries, film_paths):
        commit_files(identity, e.when, e.title, [path])
        print(f"  committed {e.when}: {e.title}", file=sys.stderr)

    print(f"done: {len(film_paths)} backdated commit(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
