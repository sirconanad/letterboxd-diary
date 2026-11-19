# letterboxd-diary

My Letterboxd diary as a git history: every film I log on
[Letterboxd](https://letterboxd.com/sirconanad/) is one commit, backdated
to the day it was watched. The green squares on GitHub's contribution graph
are days I watched a film.

All data is pulled through the official, **authenticated** Letterboxd API
(see [api-docs.letterboxd.com](https://api-docs.letterboxd.com/)), no page
scraping.

## Layout

- `diary.json` — the raw diary as pulled from the Letterboxd API
  (`[{"when", "title", "year", "rating", "rewatch", "liked", "tags"}, ...]`,
  with `year` being the film's release year)
- `films/YYYY-MM-DD - Title.md` — one file per film, one commit per film,
  containing the watched date, rating, rewatch flag, whether it was liked,
  and the diary tags
- `letterboxd_api.py` — the shared OAuth2 client for the Letterboxd API

## API setup (one time)

The scripts talk to `https://api.letterboxd.com/api/v0/` using OAuth2.
You need a registered API client (`client_id`/`client_secret`) from
[letterboxd.com/api/](https://letterboxd.com/api/), then authorize yourself
once with the Authorization Code flow:

```sh
export LETTERBOXD_CLIENT_ID=YOUR_CLIENT_ID
export LETTERBOXD_CLIENT_SECRET=YOUR_CLIENT_SECRET
python3 -m letterboxd_api login     # open the URL, approve, paste the code
python3 -m letterboxd_api whoami    # verify the cached token
```

The access/refresh token is cached in `.letterboxd_tokens.json` (gitignored)
and refreshed automatically. Without credentials, the scripts fail with
setup instructions (or, where sensible, fall back to a local data export).

## Syncing new diary entries

`build.py` is **incremental**: it pulls the whole diary through the API
(`GET /log-entries`, `where=HasDiaryDate`) and only commits entries that
aren't already committed, then creates a backdated commit for each new film.

```sh
python3 build.py               # pull new entries via the API, write files, commit
python3 build.py --no-commit   # pull + write files only (no commits)
python3 build.py --full        # ignore committed state, treat everything as new
python3 build.py --export PATH # optional fallback source of tags (backup zip/CSV)
```

`likes`, `rewatch`, `year` and `tags` all come straight from the API
(`LogEntry.like`, `diaryDetails.rewatch`, `production.releaseYear` and
`tags2`), so no data export is needed. Only edits to existing entries are
*not* picked up (they're already committed); a `--full` run rewrites the film
files from the current API state.

## Sneak vs. Kinostart

`sneak-gap.py` measures how far in advance each **sneak** / **ov sneak**
screening happened compared to the German theatrical release (Kinostart).

```sh
export TMDB_API_KEY=...        # free key from themoviedb.org settings/api
python3 sneak-gap.py                     # letterboxd data from the API + TMDB lookups
python3 sneak-gap.py --csv sneak-gap.csv --markdown sneak-gap.md
python3 sneak-gap.py --no-lookup         # just list the sneak films
python3 sneak-gap.py --export path.zip   # force using a local export instead
```

- The watch dates, release years and tags come from the Letterboxd API (or a
  local `letterboxd-*-utc.zip` export, if the API isn't configured).
  The German premiere comes from TMDB (country `DE`, theatrical release).
- The TMDB key is never stored or committed — pass it via the `TMDB_API_KEY`
  env var, `--api-key`, or a `~/.tmdb_api_key` file.
- Lookup results are cached in `sneak-cache.json` (gitignored) so re-runs
  cost no further TMDB calls.
- `sneak-gap.csv` and `sneak-gap.md` are the generated result exports.

## Contribution graph

`letterboxd_graph.py` renders the diary as a GitHub-style contribution
graph, either on the terminal or as an SVG, using the API by default.

```sh
python3 letterboxd_graph.py https://letterboxd.com/sirconanad/            # this year
python3 letterboxd_graph.py https://letterboxd.com/sirconanad/ --all     # whole diary
python3 letterboxd_graph.py https://letterboxd.com/sirconanad/ --year 2025 --year 2026
python3 letterboxd_graph.py https://letterboxd.com/sirconanad/ --all --svg graph.svg
python3 letterboxd_graph.py <url> --source rss                          # public feed, no auth
```

## Tag analysis

`letterboxd_tags.py` analyses diary tags (rankings, trends, co-occurrence)
using `GET /member/{id}/log-entry-tags` and per-tag `GET /log-entries`.

```sh
python3 letterboxd_tags.py https://letterboxd.com/sirconanad/
python3 letterboxd_tags.py <url> --top 20 --svg tags.svg --json tags.json
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
