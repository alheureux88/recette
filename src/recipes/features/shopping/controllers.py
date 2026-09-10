"""Shopping feature — shopping list management endpoints."""

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from recipes.features.preferences.controllers import ordered_departments_for_user
from recipes.features.shopping.services import (
    add_shopping_list_item,
    create_shopping_list,
    delete_shopping_list,
    get_shopping_departments,
    get_shopping_list_by_id,
    get_shopping_list_by_token,
    get_shopping_list_items,
    get_shopping_lists_by_ids,
    get_user_shopping_lists,
    remove_shopping_list_item,
    rename_shopping_list,
    toggle_shopping_list_item,
    update_shopping_list_item,
)
from recipes.features.shopping.template_services import (
    get_template_by_id,
    get_user_templates,
    seed_list_from_template,
)
from recipes.shared.auth import get_user
from recipes.shared.db import get_db, get_recipe
from recipes.shared.i18n import gettext
from recipes.shared.models import JsonDict, RecipeIngredientsToShopping
from recipes.shared.tagger import classify_ingredients as classify_ingredients_llm
from recipes.shared.units import format_quantity_string
from recipes.shared.web import (
    _resolve_request_lang,
    _shopping_list_user_id,
    add_anon_shopping_list_id,
    get_anon_shopping_list_ids,
)

router = APIRouter(tags=["shopping"])


def _get_anon_list_ids(request: Request) -> list[int]:
    """Get the list IDs stored in the session for anonymous users."""
    return get_anon_shopping_list_ids(request)


def _add_anon_list_id(request: Request, list_id: int) -> None:
    """Add a list ID to the session for anonymous users."""
    add_anon_shopping_list_id(request, list_id)


def _can_edit_shopping_list(request: Request, shopping_list: JsonDict) -> bool:
    """Check if the current user can view/edit this shopping list.

    - Owned lists (user_id set): only the owner.
    - Anonymous lists (user_id None): only an anonymous session that owns
      the ID (created it or opened it via its share token).
    """
    user = get_user(request)
    owner_id = shopping_list.get("user_id")
    if owner_id is not None:
        if user is None:
            return False
        try:
            return int(str(user.get("id"))) == int(str(owner_id))
        except (TypeError, ValueError):
            return False
    if user is not None:
        return False
    try:
        list_id = int(str(shopping_list.get("id")))
    except (TypeError, ValueError):
        return False
    return list_id in get_anon_shopping_list_ids(request)


def _require_shopping_access(request: Request, shopping_list: JsonDict, lang: str) -> None:
    """Raise 404 unless the current user may access the shopping list.

    404 (instead of 403) avoids confirming the existence of other users'
    lists by ID enumeration.
    """
    if not _can_edit_shopping_list(request, shopping_list):
        raise HTTPException(status_code=404, detail=gettext("error.shopping_list_not_found", lang))


@router.get("/shopping", response_class=HTMLResponse)
async def shopping_lists_page(
    request: Request, conn: sqlite3.Connection = Depends(get_db)
) -> HTMLResponse:
    """Show all shopping lists for the current user (or anonymous)."""
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    user_id = _shopping_list_user_id(request)

    if user_id is not None:
        lists = get_user_shopping_lists(user_id, conn=conn)
        shopping_templates = get_user_templates(user_id, conn=conn)
    else:
        lists = get_shopping_lists_by_ids(_get_anon_list_ids(request), conn=conn)
        shopping_templates = []

    lists_with_counts = []
    for lst in lists:
        items = get_shopping_list_items(int(str(lst["id"])), lang=lang, conn=conn)
        lst["item_count"] = len(items)
        done_count = sum(1 for i in items if i["is_done"])
        lst["done_count"] = done_count
        lists_with_counts.append(lst)

    return templates.TemplateResponse(
        request=request,
        name="shopping_lists.html",
        context=_base_context(
            request,
            lists=lists_with_counts,
            user_id=user_id,
            shopping_templates=shopping_templates,
        ),
    )


@router.get("/shopping/{list_id}", response_class=HTMLResponse, response_model=None)
async def shopping_list_detail(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    list_id: int = Path(gt=0),
    mode: str = Query(default="edit"),
) -> HTMLResponse | RedirectResponse:
    """Show a shopping list detail page."""
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    shopping_list = get_shopping_list_by_id(list_id, conn=conn)
    if not shopping_list:
        raise HTTPException(status_code=404, detail=gettext("error.shopping_list_not_found", lang))

    _require_shopping_access(request, shopping_list, lang)

    items = get_shopping_list_items(list_id, lang=lang, conn=conn)
    departments = ordered_departments_for_user(request, lang, conn)

    grouped: dict[int, dict[str, Any]] = {}
    for dept in departments:
        grouped[int(str(dept["id"]))] = {
            "department": dept,
            "item_list": [],
        }
    for item in items:
        dept_id = int(str(item["department_id"]))
        if dept_id in grouped:
            grouped[dept_id]["item_list"].append(item)

    return templates.TemplateResponse(
        request=request,
        name="shopping_list_detail.html",
        context=_base_context(
            request,
            shopping_list=shopping_list,
            departments=departments,
            grouped_departments=list(grouped.values()),
            mode=mode,
            can_edit=True,
        ),
    )


@router.get("/shopping/{list_id}/cook", response_class=HTMLResponse)
async def shopping_list_cook(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    list_id: int = Path(gt=0),
) -> HTMLResponse:
    """Shopping mode - cook-like view for checking off items while shopping."""
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    shopping_list = get_shopping_list_by_id(list_id, conn=conn)
    if not shopping_list:
        raise HTTPException(status_code=404, detail=gettext("error.shopping_list_not_found", lang))

    _require_shopping_access(request, shopping_list, lang)

    items = get_shopping_list_items(list_id, lang=lang, conn=conn)
    departments = ordered_departments_for_user(request, lang, conn)

    grouped: dict[int, dict[str, Any]] = {}
    for dept in departments:
        grouped[int(str(dept["id"]))] = {
            "department": dept,
            "item_list": [],
        }
    for item in items:
        dept_id = int(str(item["department_id"]))
        if dept_id in grouped:
            grouped[dept_id]["item_list"].append(item)

    total_items = len(items)
    done_items = sum(1 for item in items if item.get("is_done"))

    return templates.TemplateResponse(
        request=request,
        name="shopping_cook.html",
        context=_base_context(
            request,
            shopping_list=shopping_list,
            grouped_departments=list(grouped.values()),
            total_items=total_items,
            done_items=done_items,
            item_label="article" if lang == "fr" else "item",
        ),
    )


@router.get("/shopping/shared/{token}", response_class=HTMLResponse)
async def shopping_list_shared(
    request: Request,
    token: str,
    conn: sqlite3.Connection = Depends(get_db),
    mode: str = Query(default="shopping"),
) -> HTMLResponse:
    """View a shared shopping list. Anyone with the link can edit."""
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    shopping_list = get_shopping_list_by_token(token, conn=conn)
    if not shopping_list:
        raise HTTPException(status_code=404, detail=gettext("error.shopping_list_not_found", lang))

    user_id = _shopping_list_user_id(request)
    if user_id is None:
        _add_anon_list_id(request, int(str(shopping_list["id"])))

    items = get_shopping_list_items(int(str(shopping_list["id"])), lang=lang, conn=conn)
    departments = ordered_departments_for_user(request, lang, conn)

    grouped: dict[int, dict[str, Any]] = {}
    for dept in departments:
        grouped[int(str(dept["id"]))] = {
            "department": dept,
            "item_list": [],
        }
    for item in items:
        dept_id = int(str(item["department_id"]))
        if dept_id in grouped:
            grouped[dept_id]["item_list"].append(item)

    return templates.TemplateResponse(
        request=request,
        name="shopping_list_detail.html",
        context=_base_context(
            request,
            shopping_list=shopping_list,
            departments=departments,
            grouped_departments=list(grouped.values()),
            mode=mode,
            can_edit=True,
            is_shared=True,
        ),
    )


@router.post("/shopping/lists")
async def shopping_list_create(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    name: str = Form(...),
    template_id: int | None = Form(None),
) -> RedirectResponse:
    """Create a new shopping list, optionally seeded from a template."""
    user_id = _shopping_list_user_id(request)
    lst = create_shopping_list(name, user_id=user_id, conn=conn)
    list_id = int(str(lst["id"]))
    if user_id is None:
        _add_anon_list_id(request, list_id)
    elif template_id:
        template = get_template_by_id(template_id, conn=conn)
        if template is not None and int(str(template.get("user_id"))) == user_id:
            seed_list_from_template(list_id, template_id, conn=conn)
    return RedirectResponse(url=f"/shopping/{lst['id']}", status_code=303)


@router.post("/shopping/lists/{list_id}/delete")
async def shopping_list_delete(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    list_id: int = Path(gt=0),
) -> RedirectResponse:
    """Delete a shopping list."""
    lang = _resolve_request_lang(request)
    shopping_list = get_shopping_list_by_id(list_id, conn=conn)
    if not shopping_list:
        raise HTTPException(status_code=404, detail=gettext("error.shopping_list_not_found", lang))
    if not _can_edit_shopping_list(request, shopping_list):
        raise HTTPException(status_code=403, detail=gettext("error.not_authorized", lang))
    delete_shopping_list(list_id, conn=conn)
    return RedirectResponse(url="/shopping", status_code=303)


@router.post("/shopping/lists/{list_id}/rename", response_model=None)
async def shopping_list_rename(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    list_id: int = Path(gt=0),
    name: str = Form(...),
) -> RedirectResponse | HTMLResponse:
    """Rename a shopping list."""
    from recipes.shared.web import _base_context, templates

    lang = _resolve_request_lang(request)
    shopping_list = get_shopping_list_by_id(list_id, conn=conn)
    if not shopping_list:
        raise HTTPException(status_code=404, detail=gettext("error.shopping_list_not_found", lang))
    if not _can_edit_shopping_list(request, shopping_list):
        raise HTTPException(status_code=403, detail=gettext("error.not_authorized", lang))

    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail=gettext("error.name_required", lang))
    rename_shopping_list(list_id, name, conn=conn)

    if request.headers.get("hx-request"):
        shopping_list = get_shopping_list_by_id(list_id, conn=conn)
        return templates.TemplateResponse(
            request=request,
            name="partials/shopping_list_header.html",
            context=_base_context(
                request,
                shopping_list=shopping_list,
                can_edit=True,
            ),
        )
    return RedirectResponse(url=f"/shopping/{list_id}", status_code=303)


@router.post("/shopping/lists/{list_id}/items", response_model=None)
async def shopping_list_add_item(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    list_id: int = Path(gt=0),
    department_id: int = Form(...),
    text: str = Form(...),
    quantity: str | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    """Add an item to a shopping list."""
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    shopping_list = get_shopping_list_by_id(list_id, conn=conn)
    if not shopping_list:
        raise HTTPException(status_code=404, detail=gettext("error.shopping_list_not_found", lang))
    if not _can_edit_shopping_list(request, shopping_list):
        raise HTTPException(status_code=403, detail=gettext("error.not_authorized", lang))

    text = text.strip()
    quantity = quantity.strip() if quantity else None

    if not text:
        raise HTTPException(status_code=400, detail=gettext("error.text_required", lang))
    if not department_id:
        raise HTTPException(status_code=400, detail=gettext("error.department_required", lang))

    add_shopping_list_item(list_id, department_id, text, quantity, conn=conn)

    if request.headers.get("hx-request"):
        items = get_shopping_list_items(list_id, lang=lang, conn=conn)
        departments = ordered_departments_for_user(request, lang, conn)
        grouped: dict[int, dict[str, Any]] = {}
        for dept in departments:
            grouped[int(str(dept["id"]))] = {"department": dept, "item_list": []}
        for item in items:
            dept_id_val = int(str(item["department_id"]))
            if dept_id_val in grouped:
                grouped[dept_id_val]["item_list"].append(item)
        mode = request.query_params.get("mode", "edit")
        return templates.TemplateResponse(
            request=request,
            name="partials/shopping_list_items.html",
            context=_base_context(
                request,
                grouped_departments=list(grouped.values()),
                mode=mode,
                can_edit=True,
                shopping_list=shopping_list,
            ),
        )
    return RedirectResponse(url=f"/shopping/{list_id}", status_code=303)


@router.post("/shopping/items/{item_id}/toggle", response_model=None)
async def shopping_item_toggle(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    item_id: int = Path(gt=0),
) -> HTMLResponse | RedirectResponse:
    """Toggle an item's done status."""
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    row = conn.execute(
        "SELECT list_id FROM shopping_list_items WHERE id = ?", (item_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=gettext("error.item_not_found", lang))
    shopping_list = get_shopping_list_by_id(int(str(row["list_id"])), conn=conn)
    if not shopping_list:
        raise HTTPException(status_code=404, detail=gettext("error.item_not_found", lang))
    _require_shopping_access(request, shopping_list, lang)

    item = toggle_shopping_list_item(item_id, conn=conn)
    if not item:
        raise HTTPException(status_code=404, detail=gettext("error.item_not_found", lang))

    if request.headers.get("hx-request"):
        mode = request.query_params.get("mode", "edit")
        return templates.TemplateResponse(
            request=request,
            name="partials/shopping_item_row.html",
            context=_base_context(
                request,
                item=item,
                mode=mode,
                can_edit=True,
                lang=lang,
            ),
        )
    list_id = int(str(item["list_id"]))
    return RedirectResponse(url=f"/shopping/{list_id}", status_code=303)


@router.post("/shopping/items/{item_id}/remove", response_model=None)
async def shopping_item_remove(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    item_id: int = Path(gt=0),
) -> HTMLResponse | RedirectResponse:
    """Remove an item from a shopping list."""
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    row = conn.execute(
        "SELECT list_id FROM shopping_list_items WHERE id = ?", (item_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=gettext("error.item_not_found", lang))
    list_id_before = int(str(row["list_id"]))
    shopping_list_owner = get_shopping_list_by_id(list_id_before, conn=conn)
    if not shopping_list_owner:
        raise HTTPException(status_code=404, detail=gettext("error.item_not_found", lang))
    _require_shopping_access(request, shopping_list_owner, lang)

    removed = remove_shopping_list_item(item_id, conn=conn)
    if not removed:
        raise HTTPException(status_code=404, detail=gettext("error.item_not_found", lang))

    if request.headers.get("hx-request") and list_id_before:
        items = get_shopping_list_items(list_id_before, lang=lang, conn=conn)
        departments = ordered_departments_for_user(request, lang, conn)
        grouped: dict[int, dict[str, Any]] = {}
        for dept in departments:
            grouped[int(str(dept["id"]))] = {"department": dept, "item_list": []}
        for item in items:
            dept_id_val = int(str(item["department_id"]))
            if dept_id_val in grouped:
                grouped[dept_id_val]["item_list"].append(item)
        shopping_list = get_shopping_list_by_id(list_id_before, conn=conn)
        mode = request.query_params.get("mode", "edit")
        return templates.TemplateResponse(
            request=request,
            name="partials/shopping_list_items.html",
            context=_base_context(
                request,
                grouped_departments=list(grouped.values()),
                mode=mode,
                can_edit=True,
                shopping_list=shopping_list,
            ),
        )
    if list_id_before:
        return RedirectResponse(url=f"/shopping/{list_id_before}", status_code=303)
    return RedirectResponse(url="/shopping", status_code=303)


@router.post("/shopping/items/{item_id}/update", response_model=None)
async def shopping_item_update(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    item_id: int = Path(gt=0),
    text: str = Form(...),
    quantity: str | None = Form(None),
    department_id: int | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    """Update an item's text, quantity, and/or department."""
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    owner_row = conn.execute(
        "SELECT list_id FROM shopping_list_items WHERE id = ?", (item_id,)
    ).fetchone()
    if not owner_row:
        raise HTTPException(status_code=404, detail=gettext("error.item_not_found", lang))
    owner_list = get_shopping_list_by_id(int(str(owner_row["list_id"])), conn=conn)
    if not owner_list:
        raise HTTPException(status_code=404, detail=gettext("error.item_not_found", lang))
    _require_shopping_access(request, owner_list, lang)

    text = text.strip()
    quantity = quantity.strip() if quantity else None
    updated = update_shopping_list_item(item_id, text, quantity, department_id, conn=conn)

    if not updated:
        raise HTTPException(status_code=404, detail=gettext("error.item_not_found", lang))

    if request.headers.get("hx-request"):
        row = conn.execute(
            "SELECT list_id FROM shopping_list_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row:
            list_id = int(str(row["list_id"]))
            lang = _resolve_request_lang(request)
            items = get_shopping_list_items(list_id, lang=lang, conn=conn)
            updated_item = None
            for item in items:
                if int(str(item["id"])) == item_id:
                    updated_item = item
                    break
            if not updated_item:
                raise HTTPException(status_code=404, detail=gettext("error.item_not_found", lang))
            mode = request.query_params.get("mode", "edit")
            return templates.TemplateResponse(
                request=request,
                name="partials/shopping_item_row.html",
                context=_base_context(
                    request,
                    item=updated_item,
                    mode=mode,
                    can_edit=True,
                    lang=lang,
                ),
            )
    return RedirectResponse(url="/shopping", status_code=303)


@router.post("/api/shopping/from-recipe")
async def shopping_add_from_recipe(
    request: Request,
    data: RecipeIngredientsToShopping,
    conn: sqlite3.Connection = Depends(get_db),
) -> JsonDict:
    """Add selected ingredients from a recipe to a shopping list."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    user_id = _shopping_list_user_id(request)

    if data.list_id:
        shopping_list = get_shopping_list_by_id(data.list_id, conn=conn)
        if not shopping_list:
            raise HTTPException(
                status_code=404, detail=gettext("error.shopping_list_not_found", lang)
            )
        if not _can_edit_shopping_list(request, shopping_list):
            raise HTTPException(status_code=403, detail=gettext("error.not_authorized", lang))
        list_id = data.list_id
    elif data.new_list_name:
        lst = create_shopping_list(data.new_list_name, user_id=user_id, conn=conn)
        list_id = int(str(lst["id"]))
        if user_id is None:
            _add_anon_list_id(request, list_id)
        elif data.template_id:
            template = get_template_by_id(data.template_id, conn=conn)
            if template is not None and int(str(template.get("user_id"))) == user_id:
                seed_list_from_template(list_id, data.template_id, conn=conn)
    else:
        raise HTTPException(status_code=400, detail=gettext("error.list_id_or_name_required", lang))

    recipe_id_param = request.query_params.get("recipe_id")
    if not recipe_id_param:
        raise HTTPException(status_code=400, detail=gettext("error.recipe_id_required", lang))
    recipe_id = int(recipe_id_param)
    recipe = get_recipe(recipe_id, lang=lang, conn=conn)
    if not recipe:
        raise HTTPException(status_code=404, detail=gettext("recipe.not_found", lang))

    ingredients = recipe.get("ingredients", [])
    if not isinstance(ingredients, list):
        ingredients = []

    departments = get_shopping_departments(lang=lang, conn=conn)
    dept_map = {d["name"]: int(str(d["id"])) for d in departments}

    for idx in data.ingredient_indices:
        if idx < 0 or idx >= len(ingredients):
            continue
        ing = ingredients[idx]
        if not isinstance(ing, dict):
            continue

        food = str(ing.get("food", ""))
        if not food:
            continue

        qmin = ing.get("quantity_min")
        qmax = ing.get("quantity_max")
        has_quantity = (qmin is not None and qmin != "") or (qmax is not None and qmax != "")

        if has_quantity:
            multiplier = data.multiplier if data.multiplier else 1.0
            units = data.units if data.units else "original"
            quantity_str = format_quantity_string(
                ing, multiplicateur=multiplier, systeme=units, lang=lang
            )
        else:
            quantity_str = ""

        dept_name = ing.get("department")
        if not dept_name or dept_name not in dept_map:
            dept_name = "autre"
        dept_id = dept_map.get(str(dept_name), dept_map.get("autre", 1))

        add_shopping_list_item(list_id, dept_id, food, quantity_str, conn=conn)

    return {"ok": True, "list_id": list_id}


@router.post("/api/shopping/classify")
async def shopping_classify_ingredients(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> JsonDict:
    """Classify ingredient names into departments using the LLM."""
    from recipes.shared.web import _resolve_request_lang

    body = await request.json()
    ingredients = body.get("ingredients", [])
    if not isinstance(ingredients, list) or not ingredients:
        return {"departments": []}

    lang = _resolve_request_lang(request)
    ingredient_names = [str(i) for i in ingredients]
    dept_keys = classify_ingredients_llm(ingredient_names, lang=lang)

    departments = get_shopping_departments(lang=lang, conn=conn)
    dept_map = {d["name"]: dict(d) for d in departments}

    result = []
    for key in dept_keys:
        dept = dept_map.get(key, dept_map.get("autre", {}))
        result.append(dept)

    return {"departments": result}
