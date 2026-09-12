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
    get_collection_recipe_ids,
    get_collection_recipes,
    get_collections_for_recipe,
    is_recipe_in_collection,
    list_all_collections,
    list_site_collections,
    list_user_collections,
    promote_to_site,
    remove_recipe_from_collection,
    set_cover,
    set_featured,
    update_collection,
)
from recipes.features.recipes.services import get_user_favorite_ids
from recipes.shared.auth import get_user, is_admin, require_admin, require_user
from recipes.shared.db import get_db, get_recipe_id_by_slug
from recipes.shared.i18n import gettext
from recipes.shared.models import (
    CollectionCoverUpdate,
    CollectionCreate,
    CollectionRecipeAdd,
    CollectionUpdate,
    JsonDict,
)

router = APIRouter(tags=["collections"])


def _effective_user_id(request: Request, conn: sqlite3.Connection) -> int | None:
    """Return the viewer's numeric user ID, self-healing a stale session.

    The session may hold an ID with no matching `users` row (e.g. the
    database was recreated after login) : `owner_user_id` is a real
    foreign key, so writing with a dangling ID raises IntegrityError.
    Resolve via `subject` (stable OIDC identifier) and refresh the
    session so subsequent requests are consistent.
    """
    from recipes.features.auth.services import get_or_create_user

    session_user = get_user(request)
    if session_user is None:
        return None
    subject = session_user.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raw_id = session_user.get("id")
        try:
            return int(str(raw_id))
        except (TypeError, ValueError):
            return None
    email = session_user.get("email")
    name = session_user.get("name")
    fresh_id = get_or_create_user(
        subject=subject.strip(),
        email=str(email) if isinstance(email, str) else None,
        name=str(name) if isinstance(name, str) else None,
        conn=conn,
    )
    if session_user.get("id") != fresh_id:
        session_user["id"] = fresh_id
        request.session["user"] = session_user
    return fresh_id


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


def _require_edit(
    request: Request, collection: JsonDict, lang: str, conn: sqlite3.Connection
) -> None:
    """Raise 404 unless the current user may edit the collection.

    404 (instead of 403) avoids confirming the existence of other
    users' private collections by ID enumeration.
    """
    if not can_edit(collection, _effective_user_id(request, conn), is_admin(request)):
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
    uid = _effective_user_id(request, conn)
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
    uid = _effective_user_id(request, conn)
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
    uid = _effective_user_id(request, conn)
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
            **_filter_context(conn, lang),
            **_provenance_context(request, conn),
        ),
    )


def _filter_context(conn: sqlite3.Connection, lang: str) -> JsonDict:
    """Filter panel context (same facets as the homepage)."""
    from recipes.shared.db import get_all_categories, get_all_tags_grouped

    return {
        "all_tags": get_all_tags_grouped(lang=lang, conn=conn),
        "all_categories": get_all_categories(lang=lang, conn=conn),
        "active_tag_ids": [],
        "active_category_id": None,
        "query": "",
    }


@router.get("/collections/{slug}/search", response_class=HTMLResponse)
async def collection_search(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    slug: str = Path(),
    q: str = Query(default=""),
    tags: list[int] = Query(default=[]),
    category: str | None = Query(default=None),
    account: str | None = Query(default=None),
) -> HTMLResponse:
    """HTMX partial : homepage filters scoped to a collection's recipes."""
    from recipes.shared.auth import OIDC_ENABLED
    from recipes.shared.db import search_recipes
    from recipes.shared.web import (
        _parse_account_param,
        _provenance_context,
        _resolve_request_lang,
        templates,
    )

    lang = _resolve_request_lang(request)
    collection = get_collection_by_slug(slug, conn=conn)
    uid = _effective_user_id(request, conn)
    if collection is None or not can_view(collection, uid, is_admin(request)):
        raise HTTPException(status_code=404, detail=gettext("collections.not_found", lang))
    category_id: int | None = None
    if category and category.strip():
        try:
            category_id = int(category)
        except ValueError:
            raise HTTPException(
                status_code=422, detail=gettext("error.category_integer", lang)
            ) from None
    member_ids = set(get_collection_recipe_ids(int(str(collection["id"])), conn=conn))
    recipes = [
        recipe
        for recipe in search_recipes(
            query=q,
            tag_ids=tags,
            category_id=category_id,
            connection_id=_parse_account_param(account),
            lang=lang,
            conn=conn,
        )
        if int(str(recipe["id"])) in member_ids
    ]
    favorite_ids: set[int] = set()
    if uid is not None:
        favorite_ids = get_user_favorite_ids(uid, conn=conn)
    return templates.TemplateResponse(
        request=request,
        name="partials/recipe_cards.html",
        context={
            "recipes": recipes,
            "favorite_ids": favorite_ids,
            "user": get_user(request),
            "auth_enabled": OIDC_ENABLED,
            **_provenance_context(request, conn),
        },
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
    uid = _effective_user_id(request, conn)
    assert uid is not None
    return get_collections_for_recipe(uid, recipe_id, conn=conn)


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
        uid = _effective_user_id(request, conn)
        assert uid is not None
        return create_collection(uid, payload.name, payload.description, conn=conn)
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
    _require_edit(request, collection, lang, conn)
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


@router.put("/api/collections/{collection_id}/cover")
async def api_update_collection_cover(
    payload: CollectionCoverUpdate,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    collection_id: int = Path(gt=0),
    user: dict[str, Any] = Depends(require_user),
) -> JsonDict:
    """Pin the collection cover to a member recipe (null = automatic)."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    collection = _collection_or_404(collection_id, lang, conn)
    _require_edit(request, collection, lang, conn)
    if payload.recipe_id is not None and not is_recipe_in_collection(
        collection_id, payload.recipe_id, conn=conn
    ):
        raise HTTPException(status_code=422, detail=gettext("collections.cover_not_member", lang))
    if not set_cover(collection_id, payload.recipe_id, conn=conn):
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
    _require_edit(request, collection, lang, conn)
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
    _require_edit(request, collection, lang, conn)
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
    _require_edit(request, collection, lang, conn)
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
    uid = _effective_user_id(request, conn)
    assert uid is not None
    collections = get_collections_for_recipe(uid, recipe_id, conn=conn)
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
    _require_edit(request, collection, lang, conn)
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
