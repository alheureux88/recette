"""
auth.py — OIDC authentication via Authelia + session helpers.
"""

import logging
import os
from typing import Any

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import HTTPException, Request
from starlette.responses import RedirectResponse

from recipes.shared.i18n import LANGUAGE_COOKIE, gettext, resolve_language

logger = logging.getLogger(__name__)

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


def log_oidc_config() -> None:
    """Loggue la config OIDC effective au démarrage (sans aucun secret)."""
    logger.info(
        "auth.config enabled=%s issuer=%s client_id=%s redirect_uri=%s "
        "admin_group=%s env_superuser_groups=%s",
        OIDC_ENABLED,
        OIDC_ISSUER,
        OIDC_CLIENT_ID,
        OIDC_REDIRECT_URI,
        ADMIN_GROUP,
        _env_superuser_groups(),
    )


def is_admin(request: Request) -> bool:
    user = get_user(request)
    if not user:
        return False
    groups = user.get("groups", [])
    result = ADMIN_GROUP in groups
    logger.debug(
        "auth.is_admin sub=%s groups=%s admin_group=%s -> %s",
        user.get("sub"),
        groups,
        ADMIN_GROUP,
        result,
    )
    return result


def extract_display_name(userinfo: dict[str, Any]) -> str | None:
    """Nom d'affichage robuste à partir des claims OIDC.

    Authelia (et les providers OIDC en général) ne garantissent pas le
    claim `name` : selon la config ce peut être `preferred_username`,
    `nickname`, voire uniquement `email`. On essaie dans l'ordre et on
    retombe sur la partie locale de l'email. Retourne None si rien
    d'exploitable (l'appelant affichera alors `sub`).
    """
    for key in ("name", "display_name", "preferred_username", "nickname", "username"):
        raw = userinfo.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    email = userinfo.get("email")
    if isinstance(email, str) and email.strip() and "@" in email:
        return email.strip().split("@")[0]
    return None


def extract_groups(userinfo: dict[str, Any]) -> list[str]:
    """Groupes OIDC robustes (Authelia envoie `groups`, parfois string CSV)."""
    raw = userinfo.get("groups", [])
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


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
        logger.debug("auth.is_superuser groups non-liste: %r", user_groups)
        return False
    configured = get_superuser_groups()
    result = any(group in user_groups for group in configured)
    logger.debug(
        "auth.is_superuser sub=%s groups=%s configured=%s -> %s",
        user.get("sub"),
        user_groups,
        configured,
        result,
    )
    return result


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
    logger.debug(
        "auth.authorize_redirect issuer=%s client_id=%s redirect_uri=%s",
        OIDC_ISSUER,
        OIDC_CLIENT_ID,
        OIDC_REDIRECT_URI,
    )
    result: RedirectResponse = await oauth.authelia.authorize_redirect(request, OIDC_REDIRECT_URI)
    return result


def _claim_summary(userinfo: dict[str, Any]) -> str:
    """Résumé loggable des claims (noms de groupes OK, jamais de secrets)."""
    groups = userinfo.get("groups")
    return (
        f"keys={sorted(str(key) for key in userinfo)} "
        f"sub={userinfo.get('sub')!r} "
        f"name={userinfo.get('name')!r} "
        f"preferred_username={userinfo.get('preferred_username')!r} "
        f"email={'set' if userinfo.get('email') else 'missing'} "
        f"groups={groups!r}"
    )


async def fetch_token(request: Request) -> dict[str, Any]:
    """Échange le code contre le token et fusionne les claims OIDC.

    `token["userinfo"]` (parsé depuis le `id_token`) peut être minimal
    selon le provider : on interroge aussi le endpoint userinfo et on
    fusionne (le endpoint gagne en cas de conflit). Les logs ne
    contiennent jamais de secrets (tokens, codes, secrets) : résumé
    des claims seulement.
    """
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
    logger.debug(
        "auth.token keys=%s has_id_token=%s",
        sorted(str(key) for key in token),
        bool(token.get("id_token")),
    )
    raw_id_claims = token.get("userinfo") or {}
    id_claims = dict(raw_id_claims)
    logger.debug("auth.id_token claims %s", _claim_summary(id_claims))
    endpoint_claims: dict[str, Any] = {}
    try:
        fetched = await oauth.authelia.userinfo(token=token)
        endpoint_claims = dict(fetched or {})
        logger.debug("auth.userinfo-endpoint claims %s", _claim_summary(endpoint_claims))
    except Exception:
        logger.exception("auth.userinfo endpoint failed — fallback id_token only")
    userinfo = {**id_claims, **endpoint_claims}
    if not endpoint_claims:
        logger.warning(
            "auth.userinfo endpoint vide/indisponible ; id_token seul : %s",
            _claim_summary(id_claims),
        )
    logger.info("auth.callback userinfo merged %s", _claim_summary(userinfo))
    token["userinfo"] = userinfo
    return token
