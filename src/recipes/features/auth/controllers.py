"""Auth feature — OIDC authentication endpoints."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from recipes.features.auth.services import get_or_create_user
from recipes.shared.auth import OIDC_ENABLED, authorize_redirect, fetch_token
from recipes.shared.db import get_db
from recipes.shared.i18n import gettext
from recipes.shared.web import _resolve_request_lang

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login", name="auth_login")
async def login(request: Request) -> RedirectResponse:
    if not OIDC_ENABLED:
        return RedirectResponse(url="/", status_code=302)
    return await authorize_redirect(request)


@router.get("/callback")
async def callback(
    request: Request, conn: sqlite3.Connection = Depends(get_db)
) -> RedirectResponse:
    if not OIDC_ENABLED:
        return RedirectResponse(url="/", status_code=302)
    token = await fetch_token(request)
    userinfo = token.get("userinfo", {})
    subject = userinfo.get("sub", "")
    if not subject:
        lang = _resolve_request_lang(request)
        raise HTTPException(status_code=401, detail=gettext("error.no_subject_in_token", lang))
    user_id = get_or_create_user(
        subject=subject,
        email=userinfo.get("email"),
        name=userinfo.get("name"),
        conn=conn,
    )
    groups = userinfo.get("groups", [])
    request.session["user"] = {
        "id": user_id,
        "sub": subject,
        "name": userinfo.get("name"),
        "groups": groups,
    }
    response = RedirectResponse(url="/", status_code=302)
    # Restaure les préférences d'affichage de l'usager (cookies) dès la
    # connexion : langue + thème. Les autres réglages (unités, impression,
    # ordre des départements) sont lus en base à chaque requête.
    from recipes.features.preferences.controllers import apply_theme_cookie
    from recipes.features.preferences.services import get_preferences
    from recipes.shared.i18n import COOKIE_MAX_AGE, LANGUAGE_COOKIE, SUPPORTED_LANGUAGES

    prefs = get_preferences(user_id, conn=conn)
    saved_lang = str(prefs.get("language", ""))
    if saved_lang in SUPPORTED_LANGUAGES:
        response.set_cookie(
            key=LANGUAGE_COOKIE,
            value=saved_lang,
            max_age=COOKIE_MAX_AGE,
            samesite="lax",
            httponly=True,
        )
    apply_theme_cookie(response, str(prefs.get("theme", "system")))
    return response


@router.get("/logout", name="auth_logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)
