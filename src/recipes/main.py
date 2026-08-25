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
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from recipes.auth import (
    OIDC_ENABLED,
    authorize_redirect,
    fetch_token,
    get_user,
    login_url,
    logout_url,
    require_user,
)
from recipes.db import (
    add_favorite,
    get_all_categories,
    get_all_tags_grouped,
    get_favorite_recipes,
    get_or_create_user,
    get_recipe,
    get_user_favorite_ids,
    init_db,
    remove_favorite,
    search_recipes,
)
from recipes.poller import IMAGES_DIR
from recipes.poller import run as poll_dropbox

log = logging.getLogger(__name__)

POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "15"))
SESSION_SECRET = os.environ.get("SESSION_SECRET", "change-me-in-production")


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
    scheduler.start()
    log.info(f"Scheduler started — polling every {POLL_INTERVAL_MINUTES} min.")

    yield

    scheduler.shutdown(wait=False)
    log.info("Scheduler stopped.")


app = FastAPI(title="Recettes Merizzi", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/images", StaticFiles(directory=str(IMAGES_DIR), check_dir=False), name="images")

templates = Jinja2Templates(directory="templates")


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
    }
    ctx.update(extra)
    return ctx


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    all_tags = get_all_tags_grouped()
    all_categories = get_all_categories()
    recipes = search_recipes()
    user = get_user(request)
    favorite_ids: set[int] = set()
    if user:
        favorite_ids = get_user_favorite_ids(user["id"])
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=_base_context(
            request,
            recipes=recipes,
            all_tags=all_tags,
            all_categories=all_categories,
            query="",
            active_tag_ids=[],
            active_category_id=None,
            favorite_ids=favorite_ids,
        ),
    )


@app.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = Query(default=""),
    tags: list[int] = Query(default=[]),
    category: str | None = Query(default=None),
) -> HTMLResponse:
    category_id: int | None = None
    if category and category.strip():
        try:
            category_id = int(category)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="category must be a valid integer"
            ) from None
    recipes = search_recipes(query=q, tag_ids=tags, category_id=category_id)
    user = get_user(request)
    favorite_ids: set[int] = set()
    if user:
        favorite_ids = get_user_favorite_ids(user["id"])
    return templates.TemplateResponse(
        request=request,
        name="partials/recipe_cards.html",
        context={
            "recipes": recipes,
            "favorite_ids": favorite_ids,
            "user": user,
            "auth_enabled": OIDC_ENABLED,
        },
    )


@app.get("/recipe/{recipe_id}", response_class=HTMLResponse)
async def recipe_detail(request: Request, recipe_id: int = Path(gt=0)) -> HTMLResponse:
    recipe = get_recipe(recipe_id)
    if not recipe:
        return HTMLResponse("<h1>Recette introuvable</h1>", status_code=404)
    user = get_user(request)
    is_fav = False
    if user:
        is_fav = user["id"] in {
            rid for rid in get_user_favorite_ids(user["id"]) if rid == recipe_id
        }
    return templates.TemplateResponse(
        request=request,
        name="recipe.html",
        context=_base_context(
            request,
            recipe=recipe,
            is_favorite=is_fav,
        ),
    )


@app.get("/auth/login")
async def auth_login(request: Request) -> RedirectResponse:
    if not OIDC_ENABLED:
        return RedirectResponse(url="/", status_code=302)
    return await authorize_redirect(request)


@app.get("/auth/callback")
async def auth_callback(request: Request) -> RedirectResponse:
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
    )
    request.session["user"] = {"id": user_id, "sub": subject, "name": userinfo.get("name")}
    return RedirectResponse(url="/", status_code=302)


@app.get("/auth/logout")
async def auth_logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)


@app.post("/favorites/{recipe_id}")
async def toggle_favorite(
    request: Request,
    recipe_id: int = Path(gt=0),
    user: dict[str, Any] = Depends(require_user),
) -> HTMLResponse:
    from recipes.db import is_favorite

    currently_fav = is_favorite(user["id"], recipe_id)
    if currently_fav:
        remove_favorite(user["id"], recipe_id)
    else:
        add_favorite(user["id"], recipe_id)

    recipe = get_recipe(recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    return templates.TemplateResponse(
        request=request,
        name="partials/favorite_button.html",
        context={
            "recipe_id": recipe_id,
            "is_favorite": not currently_fav,
            "user": get_user(request),
        },
    )


@app.get("/favorites", response_model=None)
async def favorites_page(request: Request) -> RedirectResponse | HTMLResponse:
    if not OIDC_ENABLED:
        return RedirectResponse(url="/", status_code=302)
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=302)
    recipes = get_favorite_recipes(user["id"])
    favorite_ids = get_user_favorite_ids(user["id"])
    return templates.TemplateResponse(
        request=request,
        name="favorites.html",
        context=_base_context(
            request,
            recipes=recipes,
            favorite_ids=favorite_ids,
        ),
    )
