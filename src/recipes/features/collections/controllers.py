"""Collections feature — public pages, user API, and admin promotion."""

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from recipes.features.collections.services import (
    add_recipe_to_collection,
    can_edit,
    can_view,
    create_collection,
    delete_collection,
    demote_to_user,
    get_collection_by_id,
    get_collection_by_slug,
    get_collection_by_token,
    get_collection_recipes,
    get_collections_for_recipe,
    is_recipe_in_collection,
    list_all_collections,
    list_site_collections,
    list_user_collections,
    promote_to_site,
    remove_recipe_from_collection,
    set_featured,
    update_collection,
)
from recipes.features.recipes.services import get_user_favorite_ids
from recipes.shared.auth import get_user, is_admin, require_admin, require_user
from recipes.shared.db import get_db, get_recipe_id_by_slug
from recipes.shared.i18n import gettext
from recipes.shared.models import CollectionCreate, CollectionRecipeAdd, CollectionUpdate, JsonDict

router = APIRouter(tags=["collections"])


def _user_id(request: Request) -> int | None:
    user = get_user(request)
    return int(str(user["id"])) if user else None


def _recipe_id_or_404(slug_or_id: str, lang: str, conn: sqlite3.Connection) -> int:
    """Resolve a recipe slug to its numeric ID (404 if unknown)."""
    recipe_id = get_recipe_id_by_slug(slug_or_id, conn=conn)
    if recipe_id is None:
        raise HTTPException(status_code=404, detail=gettext("recipe.not_found", lang))
    return recipe_id


def _collection_or_404(collection_id: int, lang: str, conn: sqlite3.Connection) -> JsonDict:
    collection = get_collection_by_id(collection_id, conn=conn)
    if collection is None:
        raise HTTPException(status_code=404, detail=gettext("collections.not_found", lang))
    return collection


def _require_edit(request: Request, collection: JsonDict, lang: str) -> None:
    """Raise 404 unless the current user may edit the collection.

    404 (instead of 403) avoids confirming the existence of other
    users' private collections by ID enumeration.
    """
    if not can_edit(collection, _user_id(request), is_admin(request)):
        raise HTTPException(status_code=404, detail=gettext("collections.not_found", lang))


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------


@router.get("/collections", response_class=HTMLResponse)
async def collections_page(
    request: Request, conn: sqlite3.Connection = Depends(get_db)
) -> HTMLResponse:
    """List site collections plus the viewer's own collections."""
    from recipes.shared.web import _base_context, templates

    site = list_site_collections(conn=conn)
    mine: list[JsonDict] = []
    uid = _user_id(request)
    if uid is not None:
        mine = list_user_collections(uid, conn=conn)
    return templates.TemplateResponse(
        request=request,
        name="collections.html",
        context=_base_context(request, site_collections=site, my_collections=mine),
    )


@router.get("/collections/partage/{token}", response_class=HTMLResponse)
async def collection_shared_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    token: str = Path(),
) -> Response:
    """Read-only view of a user collection via its share link."""
    from recipes.shared.errors import render_not_found
    from recipes.shared.web import (
        _base_context,
        _provenance_context,
        _resolve_request_lang,
        templates,
    )

    lang = _resolve_request_lang(request)
    collection = get_collection_by_token(token, conn=conn)
    if collection is None or int(str(collection.get("is_site", 0))):
        return render_not_found(
            request, variant="generic", detail=gettext("collections.not_found", lang)
        )
    uid = _user_id(request)
    if can_view(collection, uid, is_admin(request)):
        return RedirectResponse(url=f"/collections/{collection['slug']}", status_code=302)
    recipes = get_collection_recipes(int(str(collection["id"])), lang=lang, conn=conn)
    favorite_ids: set[int] = set()
    if uid is not None:
        favorite_ids = get_user_favorite_ids(uid, conn=conn)
    return templates.TemplateResponse(
        request=request,
        name="collection_detail.html",
        context=_base_context(
            request,
            collection=collection,
            recipes=recipes,
            favorite_ids=favorite_ids,
            can_edit_collection=False,
            is_shared_view=True,
            **_provenance_context(request, conn),
        ),
    )


@router.get("/collections/{slug}", response_class=HTMLResponse)
async def collection_detail_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    slug: str = Path(),
) -> Response:
    """Detail page of a site collection or of the viewer's own collection."""
    from recipes.shared.errors import render_not_found
    from recipes.shared.web import (
        _base_context,
        _provenance_context,
        _resolve_request_lang,
        templates,
    )

    lang = _resolve_request_lang(request)
    collection = get_collection_by_slug(slug, conn=conn)
    uid = _user_id(request)
    admin = is_admin(request)
    if collection is None or not can_view(collection, uid, admin):
        return render_not_found(
            request, variant="generic", detail=gettext("collections.not_found", lang)
        )
    recipes = get_collection_recipes(int(str(collection["id"])), lang=lang, conn=conn)
    favorite_ids: set[int] = set()
    if uid is not None:
        favorite_ids = get_user_favorite_ids(uid, conn=conn)
    return templates.TemplateResponse(
        request=request,
        name="collection_detail.html",
        context=_base_context(
            request,
            collection=collection,
            recipes=recipes,
            favorite_ids=favorite_ids,
            can_edit_collection=can_edit(collection, uid, admin),
            is_shared_view=False,
            **_provenance_context(request, conn),
        ),
    )


# ---------------------------------------------------------------------------
# User JSON API (login required)
# ---------------------------------------------------------------------------


@router.get("/api/collections/for-recipe")
async def api_collections_for_recipe(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    recipe_id: int = Query(gt=0),
    user: dict[str, Any] = Depends(require_user),
) -> list[JsonDict]:
    """Return the viewer's collections with membership flags for a recipe."""
    return get_collections_for_recipe(int(str(user["id"])), recipe_id, conn=conn)


@router.post("/api/collections", status_code=201)
async def api_create_collection(
    payload: CollectionCreate,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    user: dict[str, Any] = Depends(require_user),
) -> JsonDict:
    """Create a private collection for the viewer."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail=gettext("error.name_required", lang))
    try:
        return create_collection(int(str(user["id"])), payload.name, payload.description, conn=conn)
    except ValueError:
        raise HTTPException(status_code=422, detail=gettext("error.name_required", lang)) from None


@router.put("/api/collections/{collection_id}")
async def api_update_collection(
    payload: CollectionUpdate,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    collection_id: int = Path(gt=0),
    user: dict[str, Any] = Depends(require_user),
) -> JsonDict:
    """Rename / re-describe a collection the viewer may edit."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    collection = _collection_or_404(collection_id, lang, conn)
    _require_edit(request, collection, lang)
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail=gettext("error.name_required", lang))
    try:
        updated = update_collection(collection_id, payload.name, payload.description, conn=conn)
    except ValueError:
        raise HTTPException(status_code=422, detail=gettext("error.name_required", lang)) from None
    if not updated:
        raise HTTPException(status_code=404, detail=gettext("collections.not_found", lang))
    result = get_collection_by_id(collection_id, conn=conn)
    assert result is not None
    return result


@router.delete("/api/collections/{collection_id}")
async def api_delete_collection(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    collection_id: int = Path(gt=0),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, bool]:
    """Delete a collection the viewer may edit."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    collection = _collection_or_404(collection_id, lang, conn)
    _require_edit(request, collection, lang)
    delete_collection(collection_id, conn=conn)
    return {"deleted": True}


@router.post("/api/collections/{collection_id}/recipes", status_code=201)
async def api_add_recipe(
    payload: CollectionRecipeAdd,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    collection_id: int = Path(gt=0),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, bool]:
    """Add a recipe to a collection the viewer may edit."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    collection = _collection_or_404(collection_id, lang, conn)
    _require_edit(request, collection, lang)
    added = add_recipe_to_collection(collection_id, payload.recipe_id, conn=conn)
    if not added:
        raise HTTPException(status_code=404, detail=gettext("recipe.not_found", lang))
    return {"added": True}


@router.delete("/api/collections/{collection_id}/recipes/{recipe_id}")
async def api_remove_recipe(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    collection_id: int = Path(gt=0),
    recipe_id: int = Path(gt=0),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, bool]:
    """Remove a recipe from a collection the viewer may edit."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    collection = _collection_or_404(collection_id, lang, conn)
    _require_edit(request, collection, lang)
    remove_recipe_from_collection(collection_id, recipe_id, conn=conn)
    return {"removed": True}


@router.get("/api/recipes/{slug}/collections")
async def api_recipe_membership(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    slug: str = Path(),
    user: dict[str, Any] = Depends(require_user),
) -> list[JsonDict]:
    """Return the viewer's collections with membership flags for a recipe slug."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    recipe_id = _recipe_id_or_404(slug, lang, conn)
    collections = get_collections_for_recipe(int(str(user["id"])), recipe_id, conn=conn)
    return [
        {
            "id": c["id"],
            "name": c["name"],
            "slug": c["slug"],
            "in_collection": c["in_collection"],
        }
        for c in collections
    ]


@router.post("/api/recipes/{slug}/collections/{collection_id}")
async def api_toggle_recipe_membership(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    slug: str = Path(),
    collection_id: int = Path(gt=0),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, bool]:
    """Toggle a recipe's membership in one of the viewer's collections."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    recipe_id = _recipe_id_or_404(slug, lang, conn)
    collection = _collection_or_404(collection_id, lang, conn)
    _require_edit(request, collection, lang)
    if is_recipe_in_collection(collection_id, recipe_id, conn=conn):
        remove_recipe_from_collection(collection_id, recipe_id, conn=conn)
        return {"in_collection": False}
    added = add_recipe_to_collection(collection_id, recipe_id, conn=conn)
    if not added:
        raise HTTPException(status_code=404, detail=gettext("recipe.not_found", lang))
    return {"in_collection": True}


# ---------------------------------------------------------------------------
# Admin (promotion + featured)
# ---------------------------------------------------------------------------


@router.get("/admin/collections", response_class=HTMLResponse)
async def admin_collections_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    """Admin overview of every collection with promote/feature actions."""
    from recipes.shared.web import _base_context, templates

    return templates.TemplateResponse(
        request=request,
        name="admin_collections.html",
        context=_base_context(request, collections=list_all_collections(conn=conn)),
    )


@router.post("/admin/collections/{collection_id}/promote")
async def admin_promote_collection(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    collection_id: int = Path(gt=0),
    user: dict[str, Any] = Depends(require_admin),
) -> RedirectResponse:
    """Upgrade a user collection to a site collection."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    if not promote_to_site(collection_id, conn=conn):
        raise HTTPException(status_code=404, detail=gettext("collections.not_found", lang))
    return RedirectResponse(url="/admin/collections", status_code=303)


@router.post("/admin/collections/{collection_id}/demote")
async def admin_demote_collection(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    collection_id: int = Path(gt=0),
    user: dict[str, Any] = Depends(require_admin),
) -> RedirectResponse:
    """Revert a site collection to a private user collection."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    if not demote_to_user(collection_id, conn=conn):
        raise HTTPException(status_code=404, detail=gettext("collections.not_found", lang))
    return RedirectResponse(url="/admin/collections", status_code=303)


@router.post("/admin/collections/{collection_id}/feature")
async def admin_feature_collection(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    collection_id: int = Path(gt=0),
    user: dict[str, Any] = Depends(require_admin),
) -> RedirectResponse:
    """Toggle the homepage-carousel flag of a site collection."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    collection = _collection_or_404(collection_id, lang, conn)
    featured = not bool(int(str(collection.get("is_featured", 0))))
    if not set_featured(collection_id, featured, conn=conn):
        raise HTTPException(status_code=404, detail=gettext("collections.not_found", lang))
    return RedirectResponse(url="/admin/collections", status_code=303)


@router.post("/admin/collections/{collection_id}/delete")
async def admin_delete_collection(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    collection_id: int = Path(gt=0),
    user: dict[str, Any] = Depends(require_admin),
) -> RedirectResponse:
    """Delete any collection."""
    delete_collection(collection_id, conn=conn)
    return RedirectResponse(url="/admin/collections", status_code=303)
