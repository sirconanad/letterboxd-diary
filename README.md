# letterboxd-diary

My Letterboxd diary as a git history: every film I log on
[Letterboxd](https://letterboxd.com/sirconanad/) is one commit, backdated
to the day it was watched. The green squares on GitHub's contribution graph
are days I watched a film.

## Layout

- `diary.json` — the raw diary as pulled from Letterboxd
  (`[{"when", "title", "rating", "rewatch", "liked", "tags"}, ...]`)
- `films/YYYY-MM-DD - Title.md` — one file per film, one commit per film,
  containing the watched date, rating, rewatch flag, whether it was liked,
  and the diary tags

## Syncing new diary entries

`build.py` is **incremental**: it only pulls entries that aren't already
committed. The diary is served newest-first, so it fetches pages until it
hits one where everything is already committed (everything older is too),
then creates a backdated commit for each new film.

```sh
python3 build.py          # pull new entries, write files, backdated commits
python3 build.py --no-commit   # pull + write files only (no commits)
python3 build.py --full        # ignore committed state, re-pull every page
python3 build.py --export PATH # read tags from a Letterboxd backup zip/CSV
```

Data sources: **likes** are scraped from the diary page; **tags** are *not*
shown there, so they come from your Letterboxd data export
(`diary.csv` / the `letterboxd-*-utc.zip`). `build.py` auto-detects the zip
next to the repo; pass `--export` otherwise. Re-download the export whenever
you want fresh tags.

Only edits to existing entries are *not* picked up (they're already
committed); a `--full` run rewrites the film files from the current pages.

## Notes

- Commits are backdated to **noon local time** on the watched date for both
  author and committer timestamps, so GitHub's contribution graph (US/Pacific
  calendar day logic) shows each commit on the right day.
- GitHub's *profile* graph only displays roughly the last year; older commits
  remain fully visible in this repository's commit history.
