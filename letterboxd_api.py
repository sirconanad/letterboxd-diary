#!/usr/bin/env python3
"""Authenticated client for the Letterboxd API.

Implements the OAuth2 authorization flows described at
https://api-docs.letterboxd.com/:

  * Client Credentials  - token for publicly available data.
  * Authorization Code  - token for member-authenticated data (the owner's
                          diary, tags, relationship state, …).
  * Refresh Token       - long-lived, machine-friendly sessions.

Tokens are cached in ``.letterboxd_tokens.json`` (gitignored) next to this
module; credentials are read from environment variables:

    LETTERBOXD_CLIENT_ID     (also: LETTERBOXD_CLIENT_KEY / apikey)
    LETTERBOXD_CLIENT_SECRET

Data endpoints documented in the API docs (base
``https://api.letterboxd.com/api/v0/``) used here:

    GET /me                     authenticated member
    GET /log-entries            diary/review log entries (cursored)
    GET /log-entry/{id}         single log entry
    GET /member/{id}/log-entry-tags
    GET /film/{id} , GET /films , GET /search

All requests carry ``Authorization: Bearer <access-token>``.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

BASE = "https://api.letterboxd.com/api/v0"
SITE = "https://letterboxd.com"

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_TOKEN_FILE = Path(__file__).with_name(".letterboxd_tokens.json")
DEFAULT_SCOPES = "user oauth:refresh"
MAX_PAGES = 100_000   # courtesy cap; API itself caps at 100k objects


class LetterboxdError(Exception):
    """Raised when the Letterboxd API returns an error."""


def read_env(*names) -> str | None:
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return None


def client_id_from_env() -> str | None:
    return read_env("LETTERBOXD_CLIENT_ID", "LETTERBOXD_CLIENT_KEY",
                    "LETTERBOXD_API_KEY", "apikey")


def client_secret_from_env() -> str | None:
    return read_env("LETTERBOXD_CLIENT_SECRET", "LETTERBOXD_API_SECRET")


def _as_bytes(data: str | bytes) -> bytes:
    return data.encode("utf-8") if isinstance(data, str) else data


class LetterboxdAPI:
    def __init__(self, client_id: str | None = None,
                 client_secret: str | None = None,
                 token_file: str | os.PathLike = DEFAULT_TOKEN_FILE):
        self.client_id = client_id or client_id_from_env()
        self.client_secret = client_secret or client_secret_from_env()
        if not self.client_id or not self.client_secret:
            raise LetterboxdError(
                "no Letterboxd API credentials. Set LETTERBOXD_CLIENT_ID and "
                "LETTERBOXD_CLIENT_SECRET (apply for a key at "
                "https://letterboxd.com/api/)")
        self.token_file = Path(token_file)
        self._token: dict | None = None

    # ------------------------------------------------------------ transport

    def _headers(self, bearer: str | None = None,
                 content_type: str | None = None) -> dict:
        h = dict(BROWSER_HEADERS)
        if bearer:
            h["Authorization"] = f"Bearer {bearer}"
        if content_type:
            h["Content-Type"] = content_type
        return h

    def _request_raw(self, method: str, path: str,
                     params: dict | None = None,
                     body: bytes | None = None,
                     headers: dict | None = None,
                     allow_unauth: bool = False):
        url = BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        req = urllib.request.Request(
            url, data=body, method=method,
            headers=headers or self._headers())
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            if exc.code in (401, 403) and not allow_unauth:
                raise LetterboxdError(
                    f"HTTP {exc.code} from {method} {path}: "
                    f"{raw[:300]!r}")
            if exc.code == 404:
                raise LetterboxdError(
                    f"HTTP 404 from {method} {path}: {raw[:300]!r}")
            raise LetterboxdError(
                f"HTTP {exc.code} from {method} {path}: {raw[:300]!r}")
        return json.loads(raw.decode("utf-8", "replace")) if raw else {}

    def request(self, method: str, path: str, params: dict | None = None,
                body: dict | None = None) -> dict:
        """Authenticated request to a data endpoint."""
        token = self.ensure_token()
        content_type = "application/json" if body is not None else None
        data = _as_bytes(json.dumps(body)) if body is not None else None
        try:
            return self._request_raw(
                method, path, params=params, body=data,
                headers=self._headers(bearer=token,
                                      content_type=content_type))
        except LetterboxdError as exc:
            if "HTTP 403" in str(exc):
                raise LetterboxdError(
                    f"{exc} (member access requires the authorization-code "
                    f"flow -- run `python3 -m letterboxd_api login`)") from exc
            raise

    def paginate(self, path: str, params: dict | None = None,
                 item_key: str = "items",
                 max_pages: int = MAX_PAGES) -> list[dict]:
        """Collect every page of a cursored endpoint."""
        params = dict(params or {})
        out: list[dict] = []
        for _ in range(max_pages):
            data = self.request("GET", path, params=params)
            out.extend(data.get(item_key) or [])
            nxt = data.get("next")
            if not nxt:
                break
            params["cursor"] = nxt
        return out

    # ---------------------------------------------------------------- auth

    def _load_token(self) -> dict | None:
        if self._token:
            return self._token
        if self.token_file and self.token_file.exists():
            try:
                self._token = json.loads(self.token_file.read_text())
                return self._token
            except (json.JSONDecodeError, OSError):
                self._token = None
        return self._token

    def _save_token(self, token: dict):
        self._token = token
        if self.token_file:
            self.token_file.write_text(
                json.dumps(token, indent=2) + "\n", encoding="utf-8")

    def token_is_valid(self) -> bool:
        tok = self._load_token()
        if not tok or not tok.get("access_token"):
            return False
        if tok.get("expires_at") and tok["expires_at"] > time.time() + 60:
            return True
        return False

    def _token_form(self, fields: dict) -> dict:
        """POST application/x-www-form-urlencoded to the token endpoint."""
        payload = urllib.parse.urlencode(fields).encode()
        data = self._request_raw(
            "POST", "/auth/token", body=payload,
            headers=self._headers(content_type="application/x-www-form-urlencoded"),
            allow_unauth=True)
        now = time.time()
        data["expires_at"] = now + int(data.get("expires_in", 3600))
        return data

    def client_credentials_token(self) -> dict:
        """Token with public-data access (Client Credentials flow)."""
        token = self._token_form({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        if not token.get("access_token"):
            raise LetterboxdError(f"client credentials rejected: {token!r}")
        self._save_token(token)
        return token

    def authorization_url(self, redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob",
                          scope: str = DEFAULT_SCOPES,
                          state: str = "") -> str:
        """URL the user must open in a browser to approve the app."""
        query = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
        })
        return f"{BASE}/auth/authorize?{query}"

    def exchange_code(self, code: str,
                      redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob") -> dict:
        """Exchange an authorization code for a token (Authorization Code)."""
        token = self._token_form({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        if not token.get("access_token"):
            raise LetterboxdError(f"authorization failed: {token!r}")
        self._save_token(token)
        return token

    def refresh_token(self) -> dict:
        """Refresh an expired access token using the stored refresh token."""
        tok = self._load_token()
        refresh = (tok or {}).get("refresh_token")
        if not refresh:
            raise LetterboxdError("no refresh token stored; re-authorize")
        token = self._token_form({
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        if not token.get("access_token"):
            raise LetterboxdError(f"refresh failed: {token!r}")
        # keep an unchanged refresh token if the server omits one
        saved = self._load_token() or {}
        token.setdefault("refresh_token",
                         token.get("refresh_token") or saved.get("refresh_token"))
        self._save_token(token)
        return token

    def ensure_token(self) -> str:
        """Return a valid access token, refreshing when possible.

        1. cached valid access token
        2. cached refresh token -> refresh
        3. client credentials (public data only)
        4. otherwise fail with instructions to run the auth-code flow
        """
        if self.token_is_valid():
            return self._load_token()["access_token"]
        tok = self._load_token()
        if tok and tok.get("refresh_token"):
            try:
                return self.refresh_token()["access_token"]
            except LetterboxdError:
                pass  # fall through to re-authorization
        try:
            return self.client_credentials_token()["access_token"]
        except LetterboxdError:
            pass
        raise LetterboxdError(
            "no usable access token. Run:\n\n"
            f"    python3 -m letterboxd_api login\n\n"
            "then open the printed address in a browser and paste back the "
            "authorization code.")


# ------------------------------------------------------------- data methods


def _lid_of_member(member: dict) -> str:
    return member["id"]


class LetterboxdData(LetterboxdAPI):
    """Convenience methods used by the diary/graph/tags/sneak scripts."""

    def me(self) -> dict:
        return self.request("GET", "/me")

    def member(self, ident: str) -> dict:
        return self.request("GET", f"/member/{urllib.parse.quote(ident)}")

    def member_lid(self, username: str) -> str:
        """Resolve a username to its Letterboxd ID (LID).

        1. If the authenticated member *is* this user, use ``/me``.
        2. The API docs recommend a HEAD request to the profile URL, whose
           response carries the ``x-letterboxd-identifier`` header.
        3. Last resort: ``GET /member/<username>``, which the API also
           accepts for the owner's own profile.
        """
        if username:
            try:
                me = self.me()
                if me.get("username") == username:
                    return me["id"]
            except LetterboxdError:
                pass
        try:
            req = urllib.request.Request(
                f"{SITE}/{urllib.parse.quote(username)}/", method="HEAD",
                headers=BROWSER_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                lid = resp.headers.get("x-letterboxd-identifier")
                if lid:
                    return lid
        except Exception:
            pass
        member = self.member(username)
        return member.get("id") or member.get("lid", "")

    def log_entries(self, member: str | None = None,
                    where=("HasDiaryDate",), sort: str | None = None,
                    tag_codes: list[str] | None = None,
                    exclude_tags: list[str] | None = None,
                    year: int | None = None, month: int | None = None,
                    day: int | None = None,
                    per_page: int = 100,
                    include_reviews: bool = False) -> list[dict]:
        """Cursored window over a member's diary / log entries."""
        params: dict = {"perPage": per_page}
        if member:
            params["member"] = member
        if include_reviews:
            where = [w for w in (where or ()) if w != "HasDiaryDate"]
        if where:
            params["where"] = list(where)
        if sort:
            params["sort"] = sort
        if tag_codes:
            params["includeTags"] = tag_codes
        if exclude_tags:
            params["excludeTags"] = exclude_tags
        if year:
            params["year"] = year
        if month:
            params["month"] = month
        if day:
            params["day"] = day
        return self.paginate("/log-entries", params)

    def member_log_entry_tags(self, member: str,
                              prefix: str | None = None) -> list[dict]:
        """A member's tags, ordered by frequency (MemberTag[])."""
        params = {} if prefix is None else {"input": prefix}
        data = self.request("GET",
                            f"/member/{urllib.parse.quote(member)}/log-entry-tags",
                            params=params)
        return data.get("items") or []

    def film(self, film_id: str) -> dict:
        return self.request("GET", f"/film/{urllib.parse.quote(film_id)}")

    def films(self, **params) -> list[dict]:
        return self.paginate("/films", params)

    def search(self, query: str, per_page: int = 20) -> list[dict]:
        data = self.request("GET", "/search", params={"input": query,
                                                      "perPage": per_page})
        return data.get("items") or []


# ------------------------------------------------------------------- CLI


def _cli_login_flow(scope: str = DEFAULT_SCOPES):
    api = LetterboxdData()
    redirect = "urn:ietf:wg:oauth:2.0:oob"
    url = api.authorization_url(redirect_uri=redirect, scope=scope)
    print("1) Open this address in a browser and log in as yourself:")
    print(f"\n   {url}\n")
    print("2) Approve the app; you will be shown an authorization code.")
    code = input("3) Paste the authorization code: ").strip()
    token = api.exchange_code(code, redirect_uri=redirect)
    me = None
    try:
        me = api.request("GET", "/me")
    except LetterboxdError:
        pass
    who = f" ({me.get('username')})" if me else ""
    print(f"\nSaved access token for member{who} to {api.token_file}")
    print("Token expires in %s s; refresh token retained." % token.get("expires_in"))


def _cli_whoami():
    api = LetterboxdData()
    me = api.request("GET", "/me")
    out = {
        "id": me.get("id"),
        "username": me.get("username"),
        "displayName": me.get("displayName"),
        "private": me.get("privateAccount", False),
    }
    print(json.dumps(out, indent=2))


def _cli_main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="run the Authorization Code flow and cache a token")
    sub.add_parser("whoami", help="print the authenticated member")
    cred = sub.add_parser("creds", help="fetch a Client Credentials (public) token")
    cred.add_argument("--out", default=str(DEFAULT_TOKEN_FILE))
    ns = ap.parse_args(argv)
    if ns.command == "login":
        _cli_login_flow()
    elif ns.command == "whoami":
        _cli_whoami()
    elif ns.command == "creds":
        api = LetterboxdData(token_file=ns.out)
        api.client_credentials_token()
        print(f"wrote public-data token to {api.token_file}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_cli_main(sys.argv[1:]))
    except LetterboxdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
