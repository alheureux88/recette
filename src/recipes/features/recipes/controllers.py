"""Recipes feature — public recipe browsing endpoints."""

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from recipes.shared.auth import get_user, require_user
from recipes.shared.db import (
    add_favorite,
    get_all_categories,
    get_all_tags_grouped,
    get_db,
    get_favorite_recipes,
    get_recipe,
    get_recipe_provenances,
    get_user_favorite_ids,
    get_user_shopping_lists,
    is_favorite,
    remove_favorite,
    search_recipes,
)
from recipes.shared.i18n import DEFAULT_LANGUAGE, gettext
from recipes.shared.units import format_ingredient

router = APIRouter(tags=["recipes"])

SYSTEMES_UNITES = ("original", "metric", "imperial")


def _parse_servings_param(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _parse_multiplier_param(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw.strip().replace(",", "."))
    except ValueError:
        return None
    return value if value > 0 else None


def _ingredient_context(
    recipe: dict[str, object],
    servings: int | None,
    units: str,
    multiplier: float | None = None,
    lang: str = DEFAULT_LANGUAGE,
) -> dict[str, object]:
    """Construit le contexte d'affichage des ingrédients."""
    brut = recipe.get("servings")
    base_servings: float | None = None
    if isinstance(brut, (int, float)) and not isinstance(brut, bool) and brut > 0:
        base_servings = float(brut)

    if base_servings and servings and servings > 0:
        multiplicateur = servings / base_servings
    elif not base_servings and multiplier and multiplier > 0:
        multiplicateur = multiplier
    else:
        multiplicateur = 1.0

    systeme = units if units in SYSTEMES_UNITES else "original"

    ingredients_bruts = recipe.get("ingredients")
    items = ingredients_bruts if isinstance(ingredients_bruts, list) else []
    display_ingredients = [
        format_ingredient(item, multiplicateur, systeme, lang=lang) for item in items
    ]

    current_servings: int | float | None
    if servings and servings > 0:
        current_servings = servings
    elif base_servings is not None:
        current_servings = (
            int(base_servings) if base_servings == int(base_servings) else base_servings
        )
    else:
        current_servings = None

    return {
        "base_servings": base_servings,
        "current_servings": current_servings,
        "current_multiplier": multiplicateur,
        "units_system": systeme,
        "display_ingredients": display_ingredients,
        "ingredients_structures": any(isinstance(item, dict) for item in items),
    }


def _parse_account_param(raw: str | None) -> int | None:
    """'default' → DEFAULT_ACCOUNT_ID, entier → id de connexion, sinon None."""
    from recipes.shared.db import DEFAULT_ACCOUNT_ID

    if raw is None or not raw.strip():
        return None
    if raw == "default":
        return DEFAULT_ACCOUNT_ID
    try:
        return int(raw)
    except ValueError:
        return None


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    tags: list[int] = Query(default=[]),
) -> HTMLResponse:
    from recipes.main import _base_context, _provenance_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    all_tags = get_all_tags_grouped(lang=lang, conn=conn)
    all_categories = get_all_categories(lang=lang, conn=conn)
    recipes = search_recipes(tag_ids=tags, lang=lang, conn=conn)
    user = get_user(request)
    favorite_ids: set[int] = set()
    if user:
        favorite_ids = get_user_favorite_ids(user["id"], conn=conn)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=_base_context(
            request,
            recipes=recipes,
            all_tags=all_tags,
            all_categories=all_categories,
            query="",
            active_tag_ids=tags,
            active_category_id=None,
            favorite_ids=favorite_ids,
            **_provenance_context(conn),
        ),
    )


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    q: str = Query(default=""),
    tags: list[int] = Query(default=[]),
    category: str | None = Query(default=None),
    account: str | None = Query(default=None),
) -> HTMLResponse:
    from recipes.main import _provenance_context, _resolve_request_lang, templates
    from recipes.shared.auth import OIDC_ENABLED

    lang = _resolve_request_lang(request)
    category_id: int | None = None
    if category and category.strip():
        try:
            category_id = int(category)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="category must be a valid integer"
            ) from None
    recipes = search_recipes(
        query=q,
        tag_ids=tags,
        category_id=category_id,
        connection_id=_parse_account_param(account),
        lang=lang,
        conn=conn,
    )
    user = get_user(request)
    favorite_ids: set[int] = set()
    if user:
        favorite_ids = get_user_favorite_ids(user["id"], conn=conn)
    return templates.TemplateResponse(
        request=request,
        name="partials/recipe_cards.html",
        context={
            "recipes": recipes,
            "favorite_ids": favorite_ids,
            "user": user,
            "auth_enabled": OIDC_ENABLED,
            **_provenance_context(conn),
        },
    )


@router.get("/recipe/{recipe_id}", response_class=HTMLResponse)
async def recipe_detail(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    recipe_id: int = Path(gt=0),
    servings: str | None = Query(default=None),
    units: str = Query(default="original"),
    multiplier: str | None = Query(default=None),
) -> HTMLResponse:
    from recipes.main import _base_context, _resolve_request_lang, _shopping_list_user_id, templates

    lang = _resolve_request_lang(request)
    recipe = get_recipe(recipe_id, lang=lang, conn=conn)
    if not recipe:
        not_found_msg = gettext("recipe.not_found", lang)
        return HTMLResponse(f"<h1>{not_found_msg}</h1>", status_code=404)
    user = get_user(request)
    is_fav = bool(user and is_favorite(user["id"], recipe_id, conn=conn))
    steps_raw = recipe.get("steps") or []
    steps_list: list[dict[str, object]] = []
    if isinstance(steps_raw, list):
        for step in steps_raw:
            if isinstance(step, dict):
                steps_list.append(
                    {
                        "text": str(step.get("text") or ""),
                        "has_timer": bool(step.get("timer_seconds")),
                        "duration_seconds": int(step["timer_seconds"])
                        if step.get("timer_seconds")
                        else 0,
                    }
                )

    user_id = _shopping_list_user_id(request)
    user_shopping_lists = get_user_shopping_lists(user_id, include_done=False, conn=conn)
    shopping_lists_data = [
        {"id": int(str(lst["id"])), "name": str(lst["name"])} for lst in user_shopping_lists
    ]

    ingredients_raw = recipe.get("ingredients") or []
    ingredients_for_json = []
    if isinstance(ingredients_raw, list):
        for _idx, ing in enumerate(ingredients_raw):
            if isinstance(ing, dict):
                display = format_ingredient(ing, lang=lang)
                ingredients_for_json.append(
                    {
                        "display": display,
                        "food": ing.get("food", ""),
                        "department": ing.get("department", "autre"),
                    }
                )

    return templates.TemplateResponse(
        request=request,
        name="recipe.html",
        context=_base_context(
            request,
            recipe=recipe,
            is_favorite=is_fav,
            show_provenance=len(get_recipe_provenances(conn=conn)) > 1,
            steps_list=steps_list,
            shopping_lists=shopping_lists_data,
            ingredients_json=json.dumps(ingredients_for_json, ensure_ascii=False),
            **_ingredient_context(
                recipe,
                _parse_servings_param(servings),
                units,
                _parse_multiplier_param(multiplier),
                lang=lang,
            ),
        ),
    )


@router.get("/recipe/{recipe_id}/cook", response_class=HTMLResponse)
async def recipe_cook(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    recipe_id: int = Path(gt=0),
    servings: str | None = Query(default=None),
    units: str = Query(default="original"),
    multiplier: str | None = Query(default=None),
) -> HTMLResponse:
    """Mode cuisine : vue épurée (ingrédients + étapes) avec cases à cocher."""
    from recipes.main import _base_context, _resolve_request_lang, templates
    from recipes.shared.push import VAPID_PUBLIC_KEY

    lang = _resolve_request_lang(request)
    recipe = get_recipe(recipe_id, lang=lang, conn=conn)
    if not recipe:
        not_found_msg = gettext("recipe.not_found", lang)
        return HTMLResponse(f"<h1>{not_found_msg}</h1>", status_code=404)
    ingredient_ctx = _ingredient_context(
        recipe,
        _parse_servings_param(servings),
        units,
        _parse_multiplier_param(multiplier),
        lang=lang,
    )
    steps_raw = recipe.get("steps") or []
    steps: list[dict[str, object]] = []
    if isinstance(steps_raw, list):
        for step in steps_raw:
            if isinstance(step, dict):
                steps.append(
                    {
                        "text": str(step.get("text") or ""),
                        "has_timer": bool(step.get("timer_seconds")),
                        "duration_seconds": int(step["timer_seconds"])
                        if step.get("timer_seconds")
                        else 0,
                    }
                )
    return templates.TemplateResponse(
        request=request,
        name="recipe_cook.html",
        context=_base_context(
            request,
            recipe=recipe,
            display_ingredients=ingredient_ctx.get("display_ingredients", []),
            steps=steps,
            vapid_public_key=VAPID_PUBLIC_KEY,
        ),
    )


@router.get("/recipe/{recipe_id}/ingredients", response_class=HTMLResponse)
async def recipe_ingredients(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    recipe_id: int = Path(gt=0),
    servings: str | None = Query(default=None),
    units: str = Query(default="original"),
    multiplier: str | None = Query(default=None),
) -> HTMLResponse:
    """Partial HTMX : la section ingrédients avec portions/multiplicateur et unités."""
    from recipes.main import _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    recipe = get_recipe(recipe_id, lang=lang, conn=conn)
    if not recipe:
        not_found_msg = gettext("recipe.not_found", lang)
        return HTMLResponse(f"<h1>{not_found_msg}</h1>", status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="partials/ingredients.html",
        context={
            "recipe": recipe,
            **_ingredient_context(
                recipe,
                _parse_servings_param(servings),
                units,
                _parse_multiplier_param(multiplier),
                lang=lang,
            ),
        },
    )


@router.post("/favorites/{recipe_id}")
async def toggle_favorite(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    recipe_id: int = Path(gt=0),
    user: dict[str, Any] = Depends(require_user),
) -> HTMLResponse:
    from recipes.main import _resolve_request_lang, templates

    currently_fav = is_favorite(user["id"], recipe_id, conn=conn)
    if currently_fav:
        remove_favorite(user["id"], recipe_id, conn=conn)
    else:
        add_favorite(user["id"], recipe_id, conn=conn)

    recipe = get_recipe(recipe_id, lang=_resolve_request_lang(request), conn=conn)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    return templates.TemplateResponse(
        request=request,
        name="partials/favorite_button.html",
        context={
            "recipe_id": recipe_id,
            "is_favorite": not currently_fav,
        },
    )


@router.get("/favorites", response_model=None)
async def favorites_list(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    from recipes.main import _base_context, _resolve_request_lang, templates
    from recipes.shared.auth import OIDC_ENABLED

    if not OIDC_ENABLED:
        return RedirectResponse(url="/", status_code=302)
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=302)
    lang = _resolve_request_lang(request)
    recipes = get_favorite_recipes(user["id"], lang=lang, conn=conn)
    return templates.TemplateResponse(
        request=request,
        name="favorites.html",
        context=_base_context(request, recipes=recipes),
    )
