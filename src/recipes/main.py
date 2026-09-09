"""
main.py — FastAPI + Jinja2 + HTMX recipe website.
Includes a built-in APScheduler job that polls Dropbox every X minutes.

Run:  uvicorn recipes.main:app --host 0.0.0.0 --port 8000
"""

import logging
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from recipes.features.admin.controllers import router as admin_router
from recipes.features.auth.controllers import router as auth_router
from recipes.features.push.controllers import router as push_router
from recipes.features.push.controllers import set_scheduler as set_push_scheduler
from recipes.features.recipes.controllers import router as recipes_router
from recipes.features.shopping.controllers import router as shopping_router
from recipes.features.shopping.services import cleanup_expired_shopping_lists
from recipes.shared.auth import OIDC_ENABLED
from recipes.shared.db import init_db
from recipes.shared.i18n import (
    COOKIE_MAX_AGE,
    LANGUAGE_COOKIE,
    SUPPORTED_LANGUAGES,
)
from recipes.shared.poller import (
    IMAGES_DIR,
)
from recipes.shared.poller import run as poll_dropbox
from recipes.shared.web import (
    _base_context,
    _parse_account_param,
    _provenance_context,
    _resolve_request_lang,
    _shopping_list_user_id,
    _translate,
    current_lang,
    templates,
)

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
        except Exception:
            log.exception("Initial poll failed")

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
    log.info("Scheduler started — polling every %s min.", POLL_INTERVAL_MINUTES)

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


__all__ = [
    "_base_context",
    "_parse_account_param",
    "_provenance_context",
    "_resolve_request_lang",
    "_shopping_list_user_id",
    "_translate",
    "current_lang",
    "templates",
]


@app.middleware("http")
async def locale_middleware(request: Request, call_next: Any) -> Response:
    """Résout la langue et la rend disponible aux helpers de traduction Jinja."""
    lang = _resolve_request_lang(request)
    token = current_lang.set(lang)
    try:
        response: Response = await call_next(request)
        return response
    finally:
        current_lang.reset(token)


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


# _parse_account_param, _shopping_list_user_id et _provenance_context
# vivent dans shared.web ; ils restent importés ci-dessus
# pour compatibilité ascendante.
