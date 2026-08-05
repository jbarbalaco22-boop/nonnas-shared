"""QuickBooks Online OAuth 2.0 — token exchange/refresh/revocation.

Endpoints are fetched from Intuit's OAuth discovery document at import time
(and cached) rather than hardcoded, so they stay correct if Intuit changes
their infrastructure: https://developer.api.intuit.com/.well-known/openid_configuration

Where a consumer stores its refresh token (local file, GitHub Actions secret,
etc.) is up to that consumer; this module only talks to Intuit's OAuth server.
"""
import base64
import urllib.parse

from nonnas_shared.connectors.http import request as http_request

DISCOVERY_URL = "https://developer.api.intuit.com/.well-known/openid_configuration"
SCOPE = "com.intuit.quickbooks.accounting"

_discovery_cache: dict | None = None


def _discovery() -> dict:
    global _discovery_cache
    if _discovery_cache is None:
        resp = http_request("GET", DISCOVERY_URL, timeout=30)
        resp.raise_for_status()
        _discovery_cache = resp.json()
    return _discovery_cache


def api_base_url(environment: str = "production") -> str:
    if environment == "sandbox":
        return "https://sandbox-quickbooks.api.intuit.com"
    return "https://quickbooks.api.intuit.com"


def authorization_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{_discovery()['authorization_endpoint']}?{urllib.parse.urlencode(params)}"


def _basic_auth_header(client_id: str, client_secret: str) -> dict:
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return {
        "Authorization": f"Basic {basic}",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _token_request(client_id: str, client_secret: str, data: dict) -> dict:
    resp = http_request(
        "POST",
        _discovery()["token_endpoint"],
        headers=_basic_auth_header(client_id, client_secret),
        data=data,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def exchange_code_for_tokens(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    """One-time exchange of an authorization code for the first access/refresh token pair."""
    return _token_request(client_id, client_secret, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    })


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Returns a fresh {'access_token', 'refresh_token', 'expires_in', ...}.

    QBO rotates the refresh token on every use — the old one is invalidated
    immediately. Callers MUST persist the new refresh_token from the response
    for the next run, not just the access_token.
    """
    return _token_request(client_id, client_secret, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })


def revoke_token(client_id: str, client_secret: str, token: str) -> None:
    """Disconnects the app by revoking an access or refresh token.
    Revoking a refresh token also invalidates its associated access token."""
    resp = http_request(
        "POST",
        _discovery()["revocation_endpoint"],
        headers={
            **_basic_auth_header(client_id, client_secret),
            "Content-Type": "application/json",
        },
        json={"token": token},
        timeout=30,
    )
    resp.raise_for_status()
