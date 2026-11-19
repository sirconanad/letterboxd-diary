#!/usr/bin/env python3
"""Fetch a Letterboxd diary via the official API and backdate one git commit
per film.

- Pulls the diary through the authenticated Letterboxd API
  (``GET /log-entries`` with ``where=HasDiaryDate``), see letterboxd_api.py
  and https://api-docs.letterboxd.com/ for setup.
- Incremental: only pulls entries that are not yet committed. Already
  committed entries are skipped, everything else is new.
- Writes ``diary.json`` (a merged record of everything pulled so far, now
  including ``year`` and ``tags`` from the API) and one markdown file per new
  film under ``films/``.
- Creates one git commit per new film in chronological order, with both the
  author and committer dates set to noon (local time) on the day the film was
  watched, so GitHub's contribution graph shows it on that day.

Requires a git repo. Commits are authored as ``sirconanad
<sirconanad@cock.li>``.

Authentication (OAuth2):

    export LETTERBOXD_CLIENT_ID=...    # your registered API key/client id
    export LETTERBOXD_CLIENT_SECRET=...
    python3 -m letterboxd_api login    # one-time browser approval

Tags come straight from the API (``LogEntry.tags2``), so a data export is no
longer required; ``--export PATH`` remains as an optional fallback for entries
the API would otherwise leave untagged.
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

from letterboxd_api import LetterboxdData, LetterboxdError

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


# ----------------------------------------------------------------- fetching


def get_api() -> LetterboxdData:
    """Build an authenticated API client, resolving our own member LID."""
    api = LetterboxdData()
    return api


def member_lid(api: LetterboxdData) -> str:
    me = api.me()
    return me.get("id")


def fetch_new_entries(known: Counter, full: bool = False) -> list[Entry]:
    """Pull the whole diary via the API and keep entries not yet committed.

    ``known`` is a multiset of (date, base-title) already committed; each
    matching entry consumes one slot, exactly like the scraped builder.
    Returns only the entries with nothing left to consume, newest first (so
    ``main`` can reverse them into chronological order for committing).
    """
    api = get_api()
    lid = member_lid(api)
    raw = api.log_entries(member=lid, where=("HasDiaryDate",), sort="Date")
    print(f"api: {len(raw)} diary entry/entries for {USER}", file=sys.stderr)

    new_entries: list[Entry] = []
    missing_tags = 0
    for le in raw:
        diary = le.get("diaryDetails") or {}
        prod = le.get("production") or {}
        tags = [(t.get("displayTag") or t.get("code"))
                for t in (le.get("tags2") or [])]
        tags = [t for t in tags if t]
        if not tags:
            missing_tags += 1
        e = Entry(
            when=date.fromisoformat(diary.get("diaryDate")),
            title=(prod.get("name") or "?").strip(),
            rating=le.get("rating"),
            rewatch=bool(diary.get("rewatch")),
            liked=bool(le.get("like")),
            tags=tags,
            year=prod.get("releaseYear"),
        )
        key = key_of(e)
        if known.get(key, 0) > 0:
            known[key] -= 1
            continue  # already committed
        new_entries.append(e)

    if missing_tags:
        print(f"note: {missing_tags} entry/entries without tags from the API",
              file=sys.stderr)

    new_entries.sort(key=lambda e: e.when, reverse=True)  # newest first
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

    Fallback source of tags for entries the API did not tag.
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
    """Fill in export tags for entries that have none from the API."""
    matched = 0
    for e in entries:
        if e.tags:
            continue
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


def merge_diary(merged: list[dict], new_entries: list[Entry]):
    """Append API entries that aren't already represented in diary.json."""
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
    ap.add_argument("--user", default=USER)
    ap.add_argument("--no-commit", action="store_true",
                    help="fetch and write files but do not commit")
    ap.add_argument("--export", metavar="PATH",
                    help="optional Letterboxd backup zip or diary.csv as a "
                         "fallback source of tags")
    ap.add_argument("--full", action="store_true",
                    help="ignore committed state; treat every diary entry as new")
    args = ap.parse_args(argv)

    identity = git_identity() if not args.no_commit else {}
    repo = Path.cwd()
    films_dir = repo / "films"
    films_dir.mkdir(exist_ok=True)

    known, base_counts = committed_state()
    if args.full:
        known, base_counts = Counter(), {}
    print(f"already committed: {sum(known.values())} film(s)", file=sys.stderr)

    sys.path.insert(0, str(repo))
    try:
        new_entries = fetch_new_entries(known, full=args.full)
    except LetterboxdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not new_entries:
        print("no new diary entries to add", file=sys.stderr)
        return 0
    new_entries.reverse()  # newest-first -> chronological for commits
    print(f"{len(new_entries)} new film(s): "
          f"{new_entries[0].when} .. {new_entries[-1].when}", file=sys.stderr)

    # Optional export fallback for tags the API did not provide.
    export_path = Path(args.export) if args.export else find_export()
    if export_path is not None and export_path.exists():
        tagmap = load_export_tags(export_path)
        ntags = apply_tags(new_entries, tagmap)
        print(f"tags merged from {export_path.name}: "
              f"{ntags}/{len(new_entries)} entry(ies)", file=sys.stderr)
    else:
        print("note: no Letterboxd export found next to the repo; tags come "
              "from the API", file=sys.stderr)

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
