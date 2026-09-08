"""Auth feature — OIDC authentication endpoints."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from recipes.shared.auth import OIDC_ENABLED, authorize_redirect, fetch_token
from recipes.shared.db import get_db, get_or_create_user

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
        raise HTTPException(status_code=401, detail="No subject in token")
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
    return RedirectResponse(url="/", status_code=302)


@router.get("/logout", name="auth_logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)
