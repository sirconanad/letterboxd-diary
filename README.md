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

## Sneak vs. Kinostart

`sneak-gap.py` measures how far in advance each **sneak** / **ov sneak**
screening happened compared to the German theatrical release (Kinostart).

```sh
export TMDB_API_KEY=...        # free key from themoviedb.org settings/api
python3 sneak-gap.py                     # fetch lookups, print table
python3 sneak-gap.py --csv sneak-gap.csv --markdown sneak-gap.md
python3 sneak-gap.py --no-lookup         # just list the sneak films
```

- The watch dates and tags come from the Letterboxd data export (same
  `letterboxd-*-utc.zip` as `build.py`); the German premiere comes from TMDB
  (country `DE`, theatrical release).
- The API key is never stored or committed — pass it via the `TMDB_API_KEY`
  env var, `--api-key`, or a `~/.tmdb_api_key` file.
- Lookup results are cached in `sneak-cache.json` (gitignored) so re-runs
  cost no further API calls.
- `sneak-gap.csv` and `sneak-gap.md` are the generated result exports.

## Contribution graph

`letterboxd_graph.py` renders the diary as a GitHub-style contribution
graph, either on the terminal or as an SVG.

```sh
python3 letterboxd_graph.py https://letterboxd.com/sirconanad/            # this year
python3 letterboxd_graph.py https://letterboxd.com/sirconanad/ --all     # whole diary
python3 letterboxd_graph.py https://letterboxd.com/sirconanad/ --year 2025 --year 2026
python3 letterboxd_graph.py https://letterboxd.com/sirconanad/ --all --svg graph.svg
```

It scrapes the diary pages directly (same Cloudflare handling as `build.py`);
use `--pause` to be gentle on rate limits, or `--source rss` for recent
activity only.

## Notes

- Commits are backdated to **noon local time** on the watched date for both
  author and committer timestamps, so GitHub's contribution graph (US/Pacific
  calendar day logic) shows each commit on the right day.
- GitHub's *profile* graph only displays roughly the last year; older commits
  remain fully visible in this repository's commit history.
