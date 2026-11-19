# letterboxd

My Letterboxd diary as a git history: every film I log on
[Letterboxd](https://letterboxd.com/sirconanad/) is one commit, backdated
to the day it was watched. The green squares on GitHub's contribution graph
are days I watched a film.

All data comes from **official Letterboxd data exports** (a `letterboxd-*.zip`
backup, as downloadable from <https://letterboxd.com/settings/data/>) — no
HTML scraping and no Letterboxd API. The only external API used is The Movie
Database (TMDB) for German cinema-release dates.

> Note: Letterboxd's API is request-only and is not granted for personal or
> data-visualization projects, which is exactly what this project is — so the
> scripts deliberately rely on the export instead.

## Repository layout

```
letterboxd/
├── .env                      # TMDB_API_KEY (gitignored)
├── graphs/graph.py           # contribution graph (from a data export)
├── tags/tags.py              # diary tag analysis (from a data export)
├── sneak-preview/            # sneak vs. German release analysis + results
│   ├── sneak-gap.py
│   ├── sneak-gap.csv / sneak-gap.md      (generated)
│   └── sneak-cache.json      (generated, gitignored)
├── diary/                    # git history: build.py, diary.json, films/,
│   │                         #   generate_site.py, site/
│   └── ... (this git repo)
├── sirconanad.github.io/     # deployed GitHub Pages copy of site/index.html
└── letterboxd-*.zip          # the data export (download fresh copies here)
```

## Refreshing the data

Download a **fresh** export whenever you want new or edited entries
reflected (`letterboxd.com/settings/data/` → "Letterboxd data" zip) and drop
the zip next to the repo (or pass `--export PATH`). Entry dates, tags,
ratings, rewatch flags and likes are read straight out of `diary.csv` and
`likes/films.csv` inside that zip. Scripts auto-pick the **newest** zip.

## Backdating the git history (`diary/`)

`build.py` is **incremental**: it reads the export and only commits entries
that aren't already committed, creating one backdated commit per new film.

```sh
cd diary
python3 build.py               # read the export, write files, backdated commits
python3 build.py --no-commit   # read + write files only (no commits)
python3 build.py --full        # ignore committed state, treat everything as new
```

## Sneak vs. German cinema release (`sneak-preview/`)

`sneak-gap.py` measures how far in advance each **sneak** / **ov sneak**
screening happened compared to the German theatrical release (Kinostart).

```sh
cd sneak-preview
python3 sneak-gap.py                     # export data + TMDB lookups
python3 sneak-gap.py --csv sneak-gap.csv --markdown sneak-gap.md
python3 sneak-gap.py --no-lookup         # just list the sneak films
```

- The watch dates, years and tags come from the data export; the German
  premiere comes from TMDB (country `DE`, theatrical release). **The TMDB API
  key is read from the repo's `.env` file** (`TMDB_API_KEY=...`), from the
  `TMDB_API_KEY` environment variable, or `--api-key` — it is never stored in
  the scripts. `.env` is gitignored.
- Lookup results are cached in `sneak-cache.json` (gitignored) so re-runs
  cost no further TMDB calls.

## Contribution graph (`graphs/`)

`graph.py` renders the diary export as a GitHub-style contribution graph,
on the terminal or as an SVG.

```sh
cd graphs
python3 graph.py                         # current year (export auto-detected)
python3 graph.py /path/to/letterboxd-....zip --all
python3 graph.py --year 2025 --year 2026 --svg graph.svg
```

## Tag analysis (`tags/`)

`tags.py` analyses diary tags (rankings, trends, co-occurrence) from the
export.

```sh
cd tags
python3 tags.py /path/to/letterboxd-....zip
python3 tags.py --top 20 --svg tags.svg --json tags.json
python3 tags.py --tag schauburg --tag cinema
```

## Highlights page (`diary/`)

`generate_site.py` builds a single self-contained `site/index.html` from
`diary.json` and `../sneak-preview/sneak-gap.csv`: stat cards, per-year
contribution graphs, monthly activity, top tags, favourites, and the
sneak-vs-Kinostart table. It's meant to be deployed to GitHub Pages
(`sirconanad.github.io`).

```sh
cd diary
python3 generate_site.py            # writes site/index.html
cp site/index.html ../sirconanad.github.io/index.html
```

## Notes

- Commits are backdated to **noon local time** on the watched date for both
  author and committer timestamps, so GitHub's contribution graph (US/Pacific
  calendar day logic) shows each commit on the right day.
- GitHub's *profile* graph only displays roughly the last year; older commits
  remain fully visible in the diary repository's commit history.

## Data license

Letterboxd data is © Letterboxd and respective rights holders; it is used
here for personal record-keeping only. The exported data must be refreshed
from your own account, not redistributed.
