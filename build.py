#!/usr/bin/env python3
"""Build the Letterboxd diary history from a fresh data export.

Reads the official Letterboxd data export (a ``letterboxd-*-utc.zip``
backup, as downloadable from https://letterboxd.com/settings/data/) and
backdates one git commit per film:

- Reads every diary entry from the export's ``diary.csv`` (watched date,
  title, year, rating, rewatch and tags) and marks ``liked`` from
  ``likes/films.csv``. No HTML scraping, no Letterboxd API -- the only data
  source is your own export.
- Incremental: only pulls entries that are not yet committed. Already
  committed entries are skipped, everything else is new.
- Writes ``diary.json`` (a merged record of everything pulled so far) and
  one markdown file per new film under ``films/``.
- Creates one git commit per new film in chronological order, with both the
  author and committer dates set to noon (local time) on the day the film
  was watched, so GitHub's contribution graph shows it on that day.

Requires a git repo. Commits are authored as ``sirconanad
<sirconanad@cock.li>``.

Download a fresh export whenever you want new entries reflected:

    https://letterboxd.com/settings/data/   -> "Letterboxd data" zip

The export is auto-detected next to the repo (``letterboxd-*.zip``) or can
be passed explicitly with ``--export PATH``.
"""

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dtime
from pathlib import Path

USER = "sirconanad"
AUTHOR_NAME = "sirconanad"
AUTHOR_EMAIL = "sirconanad@cock.li"
COMMIT_HOUR = 12     # noon local, keeps US/Pacific day aligned


@dataclass
class Entry:
    when: date
    title: str
    rating: float | None
    rewatch: bool
    liked: bool = False
    tags: list[str] = field(default_factory=list)
    year: int | None = None


def key_of(e: Entry) -> tuple[str, str]:
    return (e.when.isoformat(), safe_filename(e.title).lower())


# ------------------------------------------------------------------ export


def find_export() -> Path | None:
    """Locate a Letterboxd backup zip near the repo."""
    for d in (Path.cwd(), Path.cwd().parent):
        hits = sorted(d.glob("letterboxd-*.zip"))
        if hits:
            return hits[0]
    return None


def read_zip(path: Path) -> dict[str, list[dict]]:
    """Read the export's diary and likes CSVs into lists of row dicts."""
    if not zipfile.is_zipfile(path):
        raise ValueError(f"{path} is not a Letterboxd export zip")
    with zipfile.ZipFile(path) as zf:
        def rows(name: str) -> list[dict]:
            try:
                raw = zf.read(name).decode("utf-8", "replace")
            except KeyError:
                return []
            return list(csv.DictReader(io.StringIO(raw)))

        return {"diary": rows("diary.csv"), "likes": rows("likes/films.csv")}


def liked_keys(like_rows: list[dict]) -> set[tuple[str, str]]:
    """Set of (name, year) the member has liked, from likes/films.csv."""
    out = set()
    for r in like_rows:
        name = (r.get("Name") or "").strip()
        year = (r.get("Year") or "").strip()
        if name and year:
            out.add((name, year))
    return out


def parse_diagram_row(r: dict) -> Entry | None:
    when = (r.get("Watched Date") or "").strip()
    title = (r.get("Name") or "").strip()
    if not when or not title:
        return None
    try:
        when_date = date.fromisoformat(when)
    except ValueError:
        return None
    rating = (r.get("Rating") or "").strip()
    rewatch = (r.get("Rewatch") or "").strip().lower() == "yes"
    year = None
    try:
        year = int((r.get("Year") or "").strip())
    except ValueError:
        pass
    tags = [t.strip() for t in (r.get("Tags") or "").split(",") if t.strip()]
    return Entry(when=when_date, title=title,
                 rating=float(rating) / 2 if rating else None,
                 rewatch=rewatch, tags=tags, year=year)


def export_entries(path: Path) -> tuple[list[Entry], set[tuple[str, str]]]:
    """Parse the export into (chronological entries, liked name/year set)."""
    data = read_zip(path)
    liked = liked_keys(data["likes"])
    entries = []
    for r in data["diary"]:
        e = parse_diagram_row(r)
        if e is None:
            continue
        if (e.title, str(e.year or "")) in liked:
            e.liked = True
        entries.append(e)
    entries.sort(key=lambda e: e.when)
    return entries, liked


def fetch_new_entries(export: Path, known: Counter, full: bool = False
                      ) -> list[Entry]:
    """Parse the export and keep entries not yet committed.

    ``known`` is a multiset of (date, base-title) already committed; each
    matching entry consumes one slot. Returns only the entries with nothing
    left to consume, newest first (so ``main`` can reverse them into
    chronological order for committing).
    """
    entries, _liked = export_entries(export)
    print(f"export {export.name}: {len(entries)} diary entry/entries",
          file=sys.stderr)
    uncommitted: list[Entry] = []
    for e in entries:
        key = key_of(e)
        if known.get(key, 0) > 0:
            known[key] -= 1
            continue  # already committed
        uncommitted.append(e)
    uncommitted.sort(key=lambda e: e.when, reverse=True)  # newest first
    return uncommitted


# ------------------------------------------------------------------- files


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


def merge_diary(merged: list[dict], new_entries: list[Entry]):
    """Append export entries that aren't already represented in diary.json."""
    merged_keys = {(e.get("when"), e.get("title")) for e in merged}
    for e in new_entries:
        row = asdict(e)
        row["when"] = e.when.isoformat()
        key = (row["when"], e["title"])
        if key not in merged_keys:
            merged.append(row)
            merged_keys.add(key)
    return merged


# -------------------------------------------------------------------- main


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export", metavar="PATH",
                    help="Letterboxd data export zip (auto-detected next to "
                         "the repo); a fresh download is required for new "
                         "entries")
    ap.add_argument("--no-commit", action="store_true",
                    help="parse and write files but do not commit")
    ap.add_argument("--full", action="store_true",
                    help="ignore committed state; treat every diary entry as new")
    args = ap.parse_args(argv)

    identity = git_identity() if not args.no_commit else {}
    repo = Path.cwd()
    films_dir = repo / "films"
    films_dir.mkdir(exist_ok=True)

    export = Path(args.export) if args.export else find_export()
    if export is None or not export.exists():
        print("error: no Letterboxd data export found; download a fresh "
              "export from https://letterboxd.com/settings/data/ and place "
              "the zip next to the repo, or pass --export PATH",
              file=sys.stderr)
        return 1

    known, base_counts = committed_state()
    if args.full:
        known, base_counts = Counter(), {}
    print(f"already committed: {sum(known.values())} film(s)", file=sys.stderr)

    try:
        new_entries = fetch_new_entries(export, known, full=args.full)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read export: {exc}", file=sys.stderr)
        return 1
    if not new_entries:
        print("no new diary entries to add", file=sys.stderr)
        return 0
    new_entries.reverse()  # newest-first -> chronological for commits
    print(f"{len(new_entries)} new film(s): "
          f"{new_entries[0].when} .. {new_entries[-1].when}", file=sys.stderr)

    # Merge into diary.json (kept as a complete chronological record).
    merged = load_diary_json(repo / "diary.json")
    merged = merge_diary(merged, new_entries)
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
        commit_files(identity, date.today(), "Import Letterboxd diary", [
            repo / "README.md", repo / "build.py", repo / "diary.json"])

    for e, path in zip(new_entries, film_paths):
        commit_files(identity, e.when, e.title, [path])
        print(f"  committed {e.when}: {e.title}", file=sys.stderr)

    print(f"done: {len(film_paths)} backdated commit(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
