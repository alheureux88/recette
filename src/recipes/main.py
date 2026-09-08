"""
main.py — FastAPI + Jinja2 + HTMX recipe website.
Includes a built-in APScheduler job that polls Dropbox every X minutes.

Run:  uvicorn recipes.main:app --host 0.0.0.0 --port 8000
"""

import logging
import os
import sqlite3
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from recipes.features.admin.controllers import router as admin_router
from recipes.features.auth.controllers import router as auth_router
from recipes.features.push.controllers import router as push_router
from recipes.features.push.controllers import set_scheduler as set_push_scheduler
from recipes.features.recipes.controllers import router as recipes_router
from recipes.features.shopping.controllers import router as shopping_router
from recipes.shared.auth import (
    OIDC_ENABLED,
    get_user,
    is_admin,
    login_url,
    logout_url,
)
from recipes.shared.db import (
    DEFAULT_ACCOUNT_ID,
    cleanup_expired_shopping_lists,
    get_recipe_provenances,
    init_db,
)
from recipes.shared.i18n import (
    COOKIE_MAX_AGE,
    DEFAULT_LANGUAGE,
    LANGUAGE_COOKIE,
    SUPPORTED_LANGUAGES,
    available_languages,
    gettext,
    ngettext,
    resolve_language,
)
from recipes.shared.poller import (
    IMAGES_DIR,
)
from recipes.shared.poller import run as poll_dropbox

log = logging.getLogger(__name__)

POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "15"))
SESSION_SECRET = os.environ.get("SESSION_SECRET", "change-me-in-production")

_scheduler: BackgroundScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Starting initial Dropbox poll in background...")

    def _initial_poll() -> None:
        try:
            poll_dropbox()
        except Exception as e:
            log.error(f"Initial poll failed: {e}")

    threading.Thread(target=_initial_poll, daemon=True).start()

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        poll_dropbox,
        trigger="interval",
        minutes=POLL_INTERVAL_MINUTES,
        id="dropbox_poll",
        max_instances=1,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        cleanup_expired_shopping_lists,
        trigger="interval",
        hours=6,
        id="shopping_cleanup",
        max_instances=1,
        misfire_grace_time=300,
    )
    scheduler.start()
    log.info(f"Scheduler started — polling every {POLL_INTERVAL_MINUTES} min.")

    global _scheduler
    _scheduler = scheduler
    set_push_scheduler(scheduler)

    yield

    scheduler.shutdown(wait=False)
    _scheduler = None
    set_push_scheduler(None)
    log.info("Scheduler stopped.")


app = FastAPI(title="Recettes Merizzi", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/images", StaticFiles(directory=str(IMAGES_DIR), check_dir=False), name="images")

app.include_router(auth_router)
app.include_router(push_router)
app.include_router(recipes_router)
app.include_router(shopping_router)
app.include_router(admin_router)


@app.middleware("http")
async def pwa_headers(request: Request, call_next: Any) -> Response:
    response: Response = await call_next(request)
    path = request.url.path
    if path == "/static/sw.js":
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
    elif path == "/static/manifest.webmanifest":
        response.headers["Cache-Control"] = "no-cache"
    return response


def _resolve_request_lang(request: Request) -> str:
    return resolve_language(
        request.cookies.get(LANGUAGE_COOKIE),
        request.headers.get("accept-language"),
    )


def _translate(key: str, **values: object) -> str:
    """Helper exposé dans les templates comme `{{ _('key', **values) }}`.

    La langue courante est résolue depuis le contexte de la requête grâce à
    l'attribut `_lang_state` posé par le middleware `LocaleMiddleware`.
    """
    lang = getattr(_translate, "_lang_state", DEFAULT_LANGUAGE)
    return gettext(key, lang, **values)


def _ntranslate(singular: str, plural: str, n: int, **values: object) -> str:
    lang = getattr(_translate, "_lang_state", DEFAULT_LANGUAGE)
    return ngettext(singular, plural, n, lang, **values)


templates = Jinja2Templates(directory="templates")
templates.env.globals["_"] = _translate
templates.env.globals["ngettext"] = _ntranslate
templates.env.globals["supported_languages"] = SUPPORTED_LANGUAGES
templates.env.globals["default_language"] = DEFAULT_LANGUAGE


@app.middleware("http")
async def locale_middleware(request: Request, call_next: Any) -> Response:
    """Résout la langue et la rend disponible aux helpers de traduction Jinja."""
    lang = _resolve_request_lang(request)
    _translate._lang_state = lang  # type: ignore[attr-defined]
    response: Response = await call_next(request)
    return response


@app.post("/lang/{code}")
@app.get("/lang/{code}")
async def set_language(code: str, request: Request) -> RedirectResponse:
    """Set the language cookie and redirect back to the referring page."""
    candidate = code.strip().lower()
    if candidate not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=404, detail="Unsupported language")
    referer = request.headers.get("referer")
    target = referer if referer else "/"
    response = RedirectResponse(url=target, status_code=302)
    response.set_cookie(
        key=LANGUAGE_COOKIE,
        value=candidate,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        httponly=True,
    )
    return response


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc: Exception) -> RedirectResponse:
    if not OIDC_ENABLED:
        return RedirectResponse(url="/", status_code=302)
    return RedirectResponse(url="/auth/login", status_code=302)


def _base_context(request: Request, **extra: object) -> dict[str, object]:
    ctx: dict[str, object] = {
        "user": get_user(request),
        "auth_enabled": OIDC_ENABLED,
        "login_url": login_url(request),
        "logout_url": logout_url(request),
        "is_admin": is_admin(request),
        "lang": _resolve_request_lang(request),
        "available_languages": available_languages(),
    }
    ctx.update(extra)
    return ctx


def _provenance_context(conn: sqlite3.Connection) -> dict[str, object]:
    """Filtre de provenance : affiché seulement si plusieurs comptes ont des recettes."""
    provenances = get_recipe_provenances(conn=conn)
    return {"provenances": provenances, "show_provenance": len(provenances) > 1}


def _parse_account_param(raw: str | None) -> int | None:
    """'default' → DEFAULT_ACCOUNT_ID, entier → id de connexion, sinon None."""
    if raw is None or not raw.strip():
        return None
    if raw == "default":
        return DEFAULT_ACCOUNT_ID
    try:
        return int(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Shopping helpers (shared with shopping feature)
# ---------------------------------------------------------------------------


def _shopping_list_user_id(request: Request) -> int | None:
    """Return the user ID for shopping list ownership, or None for anonymous."""
    user = get_user(request)
    return user["id"] if user else None
