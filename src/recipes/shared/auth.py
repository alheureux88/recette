"""
auth.py — OIDC authentication via Authelia + session helpers.
"""

import os
from typing import Any

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import HTTPException, Request
from starlette.responses import RedirectResponse

from recipes.shared.i18n import LANGUAGE_COOKIE, gettext, resolve_language

OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
OIDC_REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "http://localhost:8000/auth/callback")
ADMIN_GROUP = os.environ.get("ADMIN_GROUP", "owner")

#: Clé `app_settings` listant les groupes OIDC super-users (CSV).
SUPERUSER_GROUPS_KEY = "superuser_groups"

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


def _request_lang(request: Request) -> str:
    """Langue de la requête (résolue sans dépendre de shared.web)."""
    return resolve_language(
        request.cookies.get(LANGUAGE_COOKIE),
        request.headers.get("accept-language"),
    )


def require_user(request: Request) -> dict[str, Any]:
    user = get_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail=gettext("error.not_authenticated", _request_lang(request)),
        )
    return user


def is_admin(request: Request) -> bool:
    user = get_user(request)
    if not user:
        return False
    groups = user.get("groups", [])
    return ADMIN_GROUP in groups


def _parse_group_list(raw: str | None) -> list[str]:
    """Parse une liste de groupes séparés par des virgules."""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _env_superuser_groups() -> list[str]:
    """Groupes super-users définis via l'environnement (valeur par défaut)."""
    raw = os.environ.get("SUPERUSER_GROUPS", os.environ.get("SUPERUSER_GROUP", ""))
    return _parse_group_list(raw)


def get_superuser_groups(conn: Any | None = None) -> list[str]:
    """Groupes OIDC super-users : union env (`SUPERUSER_GROUPS`) + réglage DB.

    La partie DB est éditable depuis l'administration système
    (`app_settings.superuser_groups`, CSV). Toute erreur de lecture DB
    (table absente, DB non initialisée) retombe sur la valeur env.
    """
    groups = list(_env_superuser_groups())
    try:
        from recipes.shared.db import get_setting

        stored = get_setting(SUPERUSER_GROUPS_KEY, "", conn=conn)
        for name in _parse_group_list(stored):
            if name not in groups:
                groups.append(name)
    except Exception:
        pass
    return groups


def is_superuser(request: Request) -> bool:
    """True si l'usager est dans un groupe OIDC configuré comme super-user."""
    user = get_user(request)
    if not user:
        return False
    user_groups = user.get("groups", [])
    if not isinstance(user_groups, list):
        return False
    return any(group in user_groups for group in get_superuser_groups())


def can_admin_content(request: Request) -> bool:
    """True si l'usager peut administrer le contenu (admin complet ou super-user)."""
    return is_admin(request) or is_superuser(request)


def require_admin(request: Request) -> dict[str, Any]:
    user = require_user(request)
    if not is_admin(request):
        raise HTTPException(
            status_code=403,
            detail=gettext("error.admin_required", _request_lang(request)),
        )
    return user


def require_content_admin(request: Request) -> dict[str, Any]:
    """Garde pour l'administration du contenu (recettes, épicerie, collections).

    Accepte les admins complets ET les super-users ; l'administration
    système (`/admin/config`) reste réservée à `require_admin`.
    """
    user = require_user(request)
    if not can_admin_content(request):
        raise HTTPException(
            status_code=403,
            detail=gettext("error.admin_required", _request_lang(request)),
        )
    return user


def login_url(request: Request) -> str:
    return str(request.url_for("auth_login"))


def logout_url(request: Request) -> str:
    return str(request.url_for("auth_logout"))


async def authorize_redirect(request: Request) -> RedirectResponse:
    if not oauth.authelia:
        raise HTTPException(
            status_code=503,
            detail=gettext("error.oidc_not_configured", _request_lang(request)),
        )
    result: RedirectResponse = await oauth.authelia.authorize_redirect(request, OIDC_REDIRECT_URI)
    return result


async def fetch_token(request: Request) -> dict[str, Any]:
    if not oauth.authelia:
        raise HTTPException(
            status_code=503,
            detail=gettext("error.oidc_not_configured", _request_lang(request)),
        )
    try:
        token: dict[str, Any] = await oauth.authelia.authorize_access_token(request)
    except OAuthError as exc:
        raise HTTPException(
            status_code=401,
            detail=gettext("error.auth_failed", _request_lang(request)),
        ) from exc
    return token
