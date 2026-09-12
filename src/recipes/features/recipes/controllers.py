"""Recipes feature — public recipe browsing endpoints."""

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from recipes.features.admin.services import get_recipe_provenances
from recipes.features.preferences.controllers import (
    print_prefs_for_user,
    show_step_ingredients_for_user,
    units_for_user,
)
from recipes.features.recipes.services import (
    add_favorite,
    get_favorite_recipes,
    get_user_favorite_ids,
    is_favorite,
    remove_favorite,
)
from recipes.features.shopping.services import get_user_shopping_lists
from recipes.shared.auth import get_user, is_admin, require_user
from recipes.shared.db import (
    get_all_categories,
    get_all_tags_grouped,
    get_db,
    get_recipe,
    get_recipe_id_by_slug,
    get_recipe_images,
    search_recipes,
)
from recipes.shared.duration import format_duration
from recipes.shared.i18n import DEFAULT_LANGUAGE, gettext
from recipes.shared.models import JsonDict
from recipes.shared.units import format_ingredient
from recipes.shared.web import _parse_account_param

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
    recipe: JsonDict,
    servings: int | None,
    units: str,
    multiplier: float | None = None,
    lang: str = DEFAULT_LANGUAGE,
) -> JsonDict:
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


def _build_steps_with_ingredients(
    steps_raw: object,
    multiplicateur: float = 1.0,
    systeme: str = "original",
    lang: str = DEFAULT_LANGUAGE,
) -> list[JsonDict]:
    """Enrichit chaque étape avec ses ingrédients fournis par le LLM.

    Chaque étape stockée peut contenir `ingredients` : une liste de dicts
    `{food, quantity_min, quantity_max, unit}` avec la quantité utilisée
    dans cette étape. Les libellés sont mis à l'échelle (portions) et
    convertis (unités) comme la liste principale.
    """
    enriched: list[JsonDict] = []
    if not isinstance(steps_raw, list):
        return enriched
    for step in steps_raw:
        if not isinstance(step, dict):
            continue
        has_timer = bool(step.get("timer_seconds"))
        duration = int(step["timer_seconds"]) if step.get("timer_seconds") else 0
        raw_ings = step.get("ingredients")
        step_ings: list[str] = []
        if isinstance(raw_ings, list):
            for ing in raw_ings:
                label = format_ingredient(ing, multiplicateur, systeme, lang=lang)
                if label.strip():
                    step_ings.append(label)
        enriched.append(
            {
                "text": str(step.get("text") or ""),
                "has_timer": has_timer,
                "duration_seconds": duration,
                "duration_display": format_duration(duration),
                "step_ingredients": step_ings,
            }
        )
    return enriched


def _resolved_steps(
    recipe: JsonDict,
    servings: str | None,
    units: str | None,
    multiplier: str | None,
    request: Request,
    conn: sqlite3.Connection,
    lang: str = DEFAULT_LANGUAGE,
) -> tuple[JsonDict, list[JsonDict]]:
    """Résout portions/unités et construit les étapes enrichies.

    Factorise le prologue commun aux pages recette, cuisine et au
    partial HTMX ingrédients : mêmes portions, mêmes libellés d'étapes
    et mêmes minuteurs partout.
    """
    resolved_units = units if units in SYSTEMES_UNITES else units_for_user(request, conn)
    ingredient_ctx = _ingredient_context(
        recipe,
        _parse_servings_param(servings),
        resolved_units,
        _parse_multiplier_param(multiplier),
        lang=lang,
    )
    mult_raw = ingredient_ctx.get("current_multiplier", 1.0)
    mult = float(mult_raw) if isinstance(mult_raw, (int, float, str)) else 1.0
    sys_raw = ingredient_ctx.get("units_system", "original")
    systeme = sys_raw if isinstance(sys_raw, str) else "original"
    steps = _build_steps_with_ingredients(recipe.get("steps"), mult, systeme, lang=lang)
    return ingredient_ctx, steps


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    tags: list[int] = Query(default=[]),
) -> HTMLResponse:
    from recipes.shared.web import (
        _base_context,
        _provenance_context,
        _resolve_request_lang,
        templates,
    )

    lang = _resolve_request_lang(request)
    all_tags = get_all_tags_grouped(lang=lang, conn=conn)
    all_categories = get_all_categories(lang=lang, conn=conn)
    recipes = search_recipes(tag_ids=tags, lang=lang, conn=conn)
    user = get_user(request)
    favorite_ids: set[int] = set()
    if user:
        favorite_ids = get_user_favorite_ids(user["id"], conn=conn)
    from recipes.features.collections.services import list_featured_collections

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
            featured_collections=list_featured_collections(conn=conn),
            **_provenance_context(request, conn),
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
    from recipes.shared.auth import OIDC_ENABLED
    from recipes.shared.web import _provenance_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    category_id: int | None = None
    if category and category.strip():
        try:
            category_id = int(category)
        except ValueError:
            raise HTTPException(
                status_code=422, detail=gettext("error.category_integer", lang)
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
            **_provenance_context(request, conn),
        },
    )


def _get_recipe_by_slug(
    slug: str, lang: str, conn: sqlite3.Connection
) -> tuple[int, JsonDict] | None:
    """Résout un slug d'URL vers (id numérique, recette localisée).

    Retourne None si le slug est inconnu ou la recette masquée : les
    appelants répondent alors 404.
    """
    recipe_id = get_recipe_id_by_slug(slug, conn=conn)
    if recipe_id is None:
        return None
    recipe = get_recipe(recipe_id, lang=lang, conn=conn)
    if not recipe:
        return None
    return recipe_id, recipe


@router.get("/recipe/{slug}", response_class=HTMLResponse)
async def recipe_detail(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    slug: str = Path(),
    servings: str | None = Query(default=None),
    units: str | None = Query(default=None),
    multiplier: str | None = Query(default=None),
) -> Response:
    from recipes.shared.errors import render_not_found
    from recipes.shared.web import (
        _base_context,
        _resolve_request_lang,
        _shopping_list_user_id,
        templates,
    )

    lang = _resolve_request_lang(request)
    resolved = _get_recipe_by_slug(slug, lang, conn)
    if resolved is None:
        return render_not_found(request, variant="recipe", detail=gettext("recipe.not_found", lang))
    recipe_id, recipe = resolved
    user = get_user(request)
    is_fav = bool(user and is_favorite(user["id"], recipe_id, conn=conn))
    ingredient_ctx_early, steps_list = _resolved_steps(
        recipe, servings, units, multiplier, request, conn, lang=lang
    )

    user_id = _shopping_list_user_id(request)
    if user_id is not None:
        user_shopping_lists = get_user_shopping_lists(user_id, include_done=False, conn=conn)
        from recipes.features.shopping.template_services import get_user_templates

        user_templates = get_user_templates(user_id, conn=conn)
    else:
        from recipes.features.shopping.services import get_shopping_lists_by_ids
        from recipes.shared.web import get_anon_shopping_list_ids

        anon_lists = get_shopping_lists_by_ids(get_anon_shopping_list_ids(request), conn=conn)
        user_shopping_lists = [lst for lst in anon_lists if not lst.get("all_done_at")]
        user_templates = []
    shopping_lists_data = [
        {"id": int(str(lst["id"])), "name": str(lst["name"])} for lst in user_shopping_lists
    ]
    shopping_templates_data = [
        {"id": int(str(tpl["id"])), "name": str(tpl["name"])} for tpl in user_templates
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

    all_images: list[JsonDict] = []
    if is_admin(request):
        primary_marked = False
        for img in get_recipe_images(recipe_id, conn=conn, include_hidden=True):
            marked = dict(img)
            marked["is_primary"] = not primary_marked and not int(str(img.get("is_hidden", 0)))
            if marked["is_primary"]:
                primary_marked = True
            all_images.append(marked)

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
            shopping_templates=shopping_templates_data,
            ingredients_json=json.dumps(ingredients_for_json, ensure_ascii=False),
            print_prefs=print_prefs_for_user(request, conn),
            show_step_ingredients=show_step_ingredients_for_user(request, conn),
            all_images=all_images,
            **ingredient_ctx_early,
        ),
    )


@router.get("/recipe/{slug}/cook", response_class=HTMLResponse)
async def recipe_cook(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    slug: str = Path(),
    servings: str | None = Query(default=None),
    units: str | None = Query(default=None),
    multiplier: str | None = Query(default=None),
) -> Response:
    """Mode cuisine : vue épurée (ingrédients + étapes) avec cases à cocher."""
    from recipes.shared.errors import render_not_found
    from recipes.shared.push import VAPID_PUBLIC_KEY
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    resolved = _get_recipe_by_slug(slug, lang, conn)
    if resolved is None:
        return render_not_found(request, variant="recipe", detail=gettext("recipe.not_found", lang))
    _, recipe = resolved
    ingredient_ctx, steps = _resolved_steps(
        recipe, servings, units, multiplier, request, conn, lang=lang
    )
    display_for_cook = ingredient_ctx.get("display_ingredients", [])
    return templates.TemplateResponse(
        request=request,
        name="recipe_cook.html",
        context=_base_context(
            request,
            recipe=recipe,
            display_ingredients=display_for_cook,
            steps=steps,
            show_step_ingredients=show_step_ingredients_for_user(request, conn),
            vapid_public_key=VAPID_PUBLIC_KEY,
        ),
    )


@router.get("/recipe/{slug}/cook/slides", response_class=HTMLResponse)
async def recipe_cook_slides(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    slug: str = Path(),
    servings: str | None = Query(default=None),
    units: str | None = Query(default=None),
    multiplier: str | None = Query(default=None),
) -> Response:
    """Mode cuisine mobile : slideshow (slide ingrédients + une slide par étape)."""
    from recipes.shared.errors import render_not_found
    from recipes.shared.push import VAPID_PUBLIC_KEY
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    resolved = _get_recipe_by_slug(slug, lang, conn)
    if resolved is None:
        return render_not_found(request, variant="recipe", detail=gettext("recipe.not_found", lang))
    _, recipe = resolved
    ingredient_ctx, steps = _resolved_steps(
        recipe, servings, units, multiplier, request, conn, lang=lang
    )
    display_for_cook = ingredient_ctx.get("display_ingredients", [])
    return templates.TemplateResponse(
        request=request,
        name="recipe_cook_slides.html",
        context=_base_context(
            request,
            recipe=recipe,
            display_ingredients=display_for_cook,
            steps=steps,
            show_step_ingredients=show_step_ingredients_for_user(request, conn),
            vapid_public_key=VAPID_PUBLIC_KEY,
        ),
    )


@router.get("/recipe/{slug}/ingredients", response_class=HTMLResponse)
async def recipe_ingredients(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    slug: str = Path(),
    servings: str | None = Query(default=None),
    units: str | None = Query(default=None),
    multiplier: str | None = Query(default=None),
) -> Response:
    """Partial HTMX : ingrédients + ingrédients d'étape (OOB).

    La section ingrédients est le swap principal ; les libellés
    d'ingrédients de chaque étape suivent via des swaps hors-bande
    (`steps_oob.html`), sans reconstruire les minuteurs.
    """
    from recipes.shared.errors import render_not_found
    from recipes.shared.web import _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    resolved = _get_recipe_by_slug(slug, lang, conn)
    if resolved is None:
        return render_not_found(request, variant="recipe", detail=gettext("recipe.not_found", lang))
    _, recipe = resolved
    ingredient_ctx, steps_list = _resolved_steps(
        recipe, servings, units, multiplier, request, conn, lang=lang
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/ingredients_update.html",
        context={
            "recipe": recipe,
            "steps_list": steps_list,
            **ingredient_ctx,
        },
    )


@router.post("/favorites/{slug}")
async def toggle_favorite(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    slug: str = Path(),
    user: dict[str, Any] = Depends(require_user),
) -> HTMLResponse:
    from recipes.shared.web import _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    resolved = _get_recipe_by_slug(slug, lang, conn)
    if resolved is None:
        raise HTTPException(status_code=404, detail=gettext("recipe.not_found", lang))
    recipe_id, _recipe = resolved
    currently_fav = is_favorite(user["id"], recipe_id, conn=conn)
    if currently_fav:
        remove_favorite(user["id"], recipe_id, conn=conn)
    else:
        add_favorite(user["id"], recipe_id, conn=conn)

    return templates.TemplateResponse(
        request=request,
        name="partials/favorite_button.html",
        context={
            "slug": slug,
            "is_favorite": not currently_fav,
        },
    )


@router.get("/favorites", response_model=None)
async def favorites_list(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    from recipes.shared.auth import OIDC_ENABLED
    from recipes.shared.web import (
        _base_context,
        _provenance_context,
        _resolve_request_lang,
        templates,
    )

    if not OIDC_ENABLED:
        return RedirectResponse(url="/", status_code=302)
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=302)
    lang = _resolve_request_lang(request)
    recipes = get_favorite_recipes(user["id"], lang=lang, conn=conn)
    favorite_ids = get_user_favorite_ids(user["id"], conn=conn)
    return templates.TemplateResponse(
        request=request,
        name="favorites.html",
        context=_base_context(
            request,
            recipes=recipes,
            favorite_ids=favorite_ids,
            **_provenance_context(request, conn),
        ),
    )
