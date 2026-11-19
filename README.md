# letterboxd-diary

My Letterboxd diary as a git history: every film I log on
[Letterboxd](https://letterboxd.com/sirconanad/) is one commit, backdated
to the day it was watched. The green squares on GitHub's contribution graph
are days I watched a film.

All data comes from **official Letterboxd data exports** (a `letterboxd-*.zip`
backup, as downloadable from <https://letterboxd.com/settings/data/>) — no
HTML scraping and no Letterboxd API. The only external API used is The Movie
Database (TMDB) for German cinema-release dates.

> Note: Letterboxd's API is request-only and is not granted for personal or
> data-visualization projects, which is exactly what this repo is — so the
> scripts deliberately rely on the export instead.

## Layout

- `diary.json` — the raw diary as parsed from the latest export
  (`[{"when", "title", "year", "rating", "rewatch", "liked", "tags"}, ...]`)
- `films/YYYY-MM-DD - Title.md` — one file per film, one commit per film,
  containing the watched date, rating, rewatch flag, whether it was liked,
  and the diary tags
- `sneak-gap.csv` / `sneak-gap.md` — sneak-preview vs. German release results

## Refreshing the data

Download a **fresh** export whenever you want new or edited entries
reflected (`letterboxd.com/settings/data/` → "Letterboxd data" zip), then
place the zip next to the repo (or pass `--export PATH`). The entries, tags,
ratings, rewatch flags and likes are all read straight out of `diary.csv`
and `likes/films.csv` inside that zip.

## Backdating the git history

`build.py` is **incremental**: it reads the export and only commits entries
that aren't already committed, creating one backdated commit per new film.

```sh
python3 build.py               # read the export, write files, backdated commits
python3 build.py --no-commit   # read + write files only (no commits)
python3 build.py --full        # ignore committed state, treat everything as new
python3 build.py --export path.zip   # use a specific export
```

## Sneak vs. German cinema release

`sneak-gap.py` measures how far in advance each **sneak** / **ov sneak**
screening happened compared to the German theatrical release (Kinostart).

```sh
python3 sneak-gap.py                     # export data + TMDB lookups (key embedded)
python3 sneak-gap.py --csv sneak-gap.csv --markdown sneak-gap.md
python3 sneak-gap.py --no-lookup         # just list the sneak films
TMDB_API_KEY=... python3 sneak-gap.py    # override the default TMDB key
```

- The watch dates, years and tags come from the data export; the German
  premiere comes from TMDB (country `DE`, theatrical release). A default TMDB
  key is embedded; override it with `TMDB_API_KEY` / `--api-key` if needed.
- Lookup results are cached in `sneak-cache.json` (gitignored) so re-runs
  cost no further TMDB calls.

## Contribution graph

`letterboxd_graph.py` renders the diary export as a GitHub-style
contribution graph, on the terminal or as an SVG.

```sh
python3 letterboxd_graph.py                         # current year (export auto-detected)
python3 letterboxd_graph.py letterboxd-....zip --all
python3 letterboxd_graph.py --year 2025 --year 2026 --svg graph.svg
```

## Tag analysis

`letterboxd_tags.py` analyses diary tags (rankings, trends, co-occurrence)
from the export.

```sh
python3 letterboxd_tags.py letterboxd-....zip
python3 letterboxd_tags.py --top 20 --svg tags.svg --json tags.json
python3 letterboxd_tags.py --tag schauburg --tag cinema
```

## Highlights page

`generate_site.py` builds a single self-contained `site/index.html` from
`diary.json` and `sneak-gap.csv`: stat cards, per-year contribution graphs,
monthly activity, top tags, favourites, and the sneak-vs-Kinostart table.
It's meant to be deployed to GitHub Pages (`sirconanad.github.io`).

```sh
python3 generate_site.py            # writes site/index.html
python3 generate_site.py -o ~/sirconanad.github.io/index.html
```

## Notes

- Commits are backdated to **noon local time** on the watched date for both
  author and committer timestamps, so GitHub's contribution graph (US/Pacific
  calendar day logic) shows each commit on the right day.
- GitHub's *profile* graph only displays roughly the last year; older commits
  remain fully visible in this repository's commit history.

## Data license

Letterboxd data is © Letterboxd and respective rights holders; it is used
here for personal record-keeping only. The exported data must be refreshed
from your own account, not redistributed.
