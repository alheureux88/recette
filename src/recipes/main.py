"""
main.py — FastAPI + Jinja2 + HTMX recipe website.
Includes a built-in APScheduler job that polls Dropbox every X minutes.

Run:  uvicorn recipes.main:app --host 0.0.0.0 --port 8000
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from recipes.db import (
    get_all_categories,
    get_all_tags_grouped,
    get_recipe,
    init_db,
    search_recipes,
)
from recipes.poller import run as poll_dropbox

log = logging.getLogger(__name__)

POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "15"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()

    log.info("Running initial Dropbox poll on startup...")
    try:
        poll_dropbox()
    except Exception as e:
        log.error(f"Initial poll failed: {e}")

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
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    all_tags = get_all_tags_grouped()
    all_categories = get_all_categories()
    recipes = search_recipes()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "recipes": recipes,
            "all_tags": all_tags,
            "all_categories": all_categories,
            "query": "",
            "active_tag_ids": [],
            "active_category_id": None,
        },
    )


@app.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = Query(""),
    tags: list[int] = Query(default=[]),
    category: str | None = Query(default=None),
) -> HTMLResponse:
    category_id = int(category) if category else None
    recipes = search_recipes(query=q, tag_ids=tags, category_id=category_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/recipe_cards.html",
        context={
            "recipes": recipes,
        },
    )


@app.get("/recipe/{recipe_id}", response_class=HTMLResponse)
async def recipe_detail(request: Request, recipe_id: int) -> HTMLResponse:
    recipe = get_recipe(recipe_id)
    if not recipe:
        return HTMLResponse("<h1>Recette introuvable</h1>", status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="recipe.html",
        context={
            "recipe": recipe,
        },
    )
