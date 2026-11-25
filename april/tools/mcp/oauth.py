"""OAuth token acquisition for HTTP MCP servers.

Two grants: ``client_credentials`` (headless) and ``authorization_code`` (a
local callback server captures the code, then exchanges it). Tokens are cached
until shortly before expiry.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread


class OAuthError(Exception):
    pass


def validate_oauth(oauth) -> list[str]:
    if not isinstance(oauth, dict):
        return ["oauth must be an object"]
    errors: list[str] = []
    grant = oauth.get("grant")
    if grant not in ("client_credentials", "authorization_code"):
        errors.append("oauth.grant must be client_credentials or authorization_code")
    for key in ("client_id", "token_url"):
        if not str(oauth.get(key) or "").strip():
            errors.append(f"oauth.{key} is required")
    if grant == "client_credentials" and not str(oauth.get("client_secret") or "").strip():
        errors.append("oauth.client_secret is required for client_credentials")
    if grant == "authorization_code":
        for key in ("authorization_url", "redirect_uri"):
            if not str(oauth.get(key) or "").strip():
                errors.append(f"oauth.{key} is required")
    scopes = oauth.get("scopes")
    if scopes is not None and not (
        isinstance(scopes, list) and all(isinstance(scope, str) for scope in scopes)
    ):
        errors.append("oauth.scopes must be a list of strings")
    return errors


class TokenProvider:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, float]] = {}

    def token(self, record: dict) -> str:
        oauth = record.get("oauth")
        if not isinstance(oauth, dict) or not oauth:
            return ""
        key = json.dumps(oauth, sort_keys=True)
        cached = self._cache.get(key)
        if cached and cached[1] > time.time():
            return cached[0]
        grant = str(oauth.get("grant") or "")
        if grant == "client_credentials":
            return self._request_token(oauth, key, {"grant_type": "client_credentials"})
        if grant == "authorization_code":
            code = str(oauth.get("code") or "")
            if not code:
                raise OAuthError(
                    "authorization_code requires a code; run authorization_code_flow first"
                )
            return self._request_token(
                oauth,
                key,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": str(oauth.get("redirect_uri") or ""),
                },
            )
        raise OAuthError(f"unsupported oauth grant: {grant!r}")

    def authorization_url(
        self, oauth: dict, state: str = "april", redirect_uri: str | None = None
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": oauth.get("client_id") or "",
            "redirect_uri": redirect_uri or oauth.get("redirect_uri") or "",
            "scope": " ".join(oauth.get("scopes") or []),
            "state": state,
        }
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v})
        return f"{oauth['authorization_url']}?{query}"

    def authorization_code_flow(
        self,
        oauth: dict,
        redirect_uri: str | None = None,
        open_browser: bool = True,
        timeout: float = 120.0,
    ) -> str:
        redirect = str(
            redirect_uri or oauth.get("redirect_uri") or "http://127.0.0.1:8765/callback"
        )
        parsed = urllib.parse.urlparse(redirect)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8765
        captured: dict[str, str] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if "code" in query:
                    captured["code"] = query["code"][0]
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"APRIL: authorization received; close this tab.")
                else:
                    self.send_response(400)
                    self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer((host, port), Handler)
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()
        url = self.authorization_url(oauth, redirect_uri=redirect)
        if open_browser:
            import webbrowser

            webbrowser.open(url)
        thread.join(timeout)
        server.server_close()
        code = captured.get("code")
        if not code:
            raise OAuthError("authorization flow timed out or was denied")
        key = json.dumps({**oauth, "code": code}, sort_keys=True)
        return self._request_token(
            oauth,
            key,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect,
            },
        )

    def _request_token(self, oauth: dict, key: str, extra: dict) -> str:
        data = {
            "client_id": oauth.get("client_id") or "",
            "client_secret": oauth.get("client_secret") or "",
            "scope": " ".join(oauth.get("scopes") or []),
            **extra,
        }
        body = urllib.parse.urlencode(
            {k: v for k, v in data.items() if v is not None and v != ""}
        ).encode()
        request = urllib.request.Request(
            str(oauth["token_url"]),
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except Exception as exc:
            raise OAuthError(f"token request failed: {exc}") from exc
        token = payload.get("access_token")
        if not token:
            raise OAuthError("token response had no access_token")
        expires = float(payload.get("expires_in") or 3600)
        self._cache[key] = (str(token), time.time() + max(1.0, expires - 30))
        return str(token)
