"""Shopping feature — shopping list management endpoints."""

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

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
from recipes.shared.auth import get_user
from recipes.shared.db import get_db, get_recipe
from recipes.shared.i18n import gettext
from recipes.shared.models import JsonDict, RecipeIngredientsToShopping
from recipes.shared.tagger import classify_ingredients as classify_ingredients_llm
from recipes.shared.units import format_quantity_string
from recipes.shared.web import _resolve_request_lang, _shopping_list_user_id

router = APIRouter(tags=["shopping"])


def _get_anon_list_ids(request: Request) -> list[int]:
    """Get the list IDs stored in the session for anonymous users."""
    ids = request.session.get("shopping_list_ids", [])
    return [int(i) for i in ids]


def _add_anon_list_id(request: Request, list_id: int) -> None:
    """Add a list ID to the session for anonymous users."""
    ids = _get_anon_list_ids(request)
    if list_id not in ids:
        ids.append(list_id)
        request.session["shopping_list_ids"] = ids


def _can_edit_shopping_list(request: Request, shopping_list: JsonDict) -> bool:
    """Check if the current user can edit this shopping list.

    Anyone with the direct link (share token) can edit.
    """
    user = get_user(request)
    list_user_id = shopping_list.get("user_id")
    if list_user_id is None:
        return True
    if user is None:
        return False
    return bool(user["id"]) == int(str(list_user_id))


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
    else:
        lists = get_shopping_lists_by_ids(_get_anon_list_ids(request), conn=conn)

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

    user_id = _shopping_list_user_id(request)
    if user_id is None:
        _add_anon_list_id(request, list_id)

    items = get_shopping_list_items(list_id, lang=lang, conn=conn)
    departments = get_shopping_departments(lang=lang, conn=conn)

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

    user_id = _shopping_list_user_id(request)
    if user_id is None:
        _add_anon_list_id(request, list_id)

    items = get_shopping_list_items(list_id, lang=lang, conn=conn)
    departments = get_shopping_departments(lang=lang, conn=conn)

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
    departments = get_shopping_departments(lang=lang, conn=conn)

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
) -> RedirectResponse:
    """Create a new shopping list."""
    user_id = _shopping_list_user_id(request)
    lst = create_shopping_list(name, user_id=user_id, conn=conn)
    if user_id is None:
        _add_anon_list_id(request, int(str(lst["id"])))
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
        departments = get_shopping_departments(lang=lang, conn=conn)
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
    list_id_before = None
    if request.headers.get("hx-request"):
        row = conn.execute(
            "SELECT list_id FROM shopping_list_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row:
            list_id_before = int(str(row["list_id"]))

    removed = remove_shopping_list_item(item_id, conn=conn)
    if not removed:
        raise HTTPException(status_code=404, detail=gettext("error.item_not_found", lang))

    if request.headers.get("hx-request") and list_id_before:
        items = get_shopping_list_items(list_id_before, lang=lang, conn=conn)
        departments = get_shopping_departments(lang=lang, conn=conn)
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
