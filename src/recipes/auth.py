"""
auth.py — OIDC authentication via Authelia + session helpers.
"""

import os
from typing import Any

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import HTTPException, Request
from starlette.responses import RedirectResponse

OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
OIDC_REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "http://localhost:8000/auth/callback")

OIDC_ENABLED = bool(OIDC_ISSUER and OIDC_CLIENT_ID)

oauth = OAuth()

if OIDC_ENABLED:
    oauth.register(
        name="authelia",
        client_id=OIDC_CLIENT_ID,
        client_secret=OIDC_CLIENT_SECRET,
        server_metadata_url=f"{OIDC_ISSUER}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile groups"},
    )


def get_user(request: Request) -> dict[str, Any] | None:
    return request.session.get("user")


def require_user(request: Request) -> dict[str, Any]:
    user = get_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def login_url(request: Request) -> str:
    return str(request.url_for("auth_login"))


def logout_url(request: Request) -> str:
    return str(request.url_for("auth_logout"))


async def authorize_redirect(request: Request) -> RedirectResponse:
    if not oauth.authelia:
        raise HTTPException(status_code=503, detail="OIDC not configured")
    result: RedirectResponse = await oauth.authelia.authorize_redirect(request, OIDC_REDIRECT_URI)
    return result


async def fetch_token(request: Request) -> dict[str, Any]:
    if not oauth.authelia:
        raise HTTPException(status_code=503, detail="OIDC not configured")
    try:
        token: dict[str, Any] = await oauth.authelia.authorize_access_token(request)
    except OAuthError as exc:
        raise HTTPException(status_code=401, detail="Authentication failed") from exc
    return token
