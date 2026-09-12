"""Admin feature — administration endpoints for recipes, config, and shopping."""

import json
import logging
import os
import secrets
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from recipes.features.admin.services import (
    add_dropbox_connection,
    blacklist_and_delete_recipe,
    delete_dropbox_connection,
    delete_setting,
    get_blacklisted_files,
    get_dropbox_connection_credentials,
    get_dropbox_connections,
    get_failed_files,
    get_setting,
    is_default_account_active,
    is_default_account_visible,
    remove_failed_file,
    remove_from_blacklist,
    set_default_account_active,
    set_default_account_visible,
    set_dropbox_connection_active,
    set_dropbox_connection_visible,
    set_setting,
)
from recipes.features.recipes.services import (
    bulk_update_category,
    bulk_update_tags,
    get_all_recipes_admin,
    retag_recipe,
)
from recipes.features.shopping.services import (
    delete_shopping_list,
    get_all_shopping_lists,
    get_shopping_departments,
    get_shopping_list_by_id,
    get_shopping_list_items,
)
from recipes.shared.auth import require_admin
from recipes.shared.db import (
    get_all_categories,
    get_db,
    get_existing_tags_for_prompt,
    get_orphaned_recipes,
    get_recipe,
    get_tag_families,
    set_recipe_force_visible,
    sync_recipe_tags,
    update_recipe_category,
    update_recipe_manual,
    update_recipe_tags,
)
from recipes.shared.i18n import gettext
from recipes.shared.models import (
    BulkCategoryUpdate,
    BulkRetagUpdate,
    BulkTagsUpdate,
    InlineCategoryUpdate,
    InlineTagsUpdate,
    JsonDict,
)
from recipes.shared.poller import (
    DROPBOX_FOLDER,
    build_oauth_authorize_url,
    create_pkce_pair,
    exchange_authorization_code,
    forget_connection_client,
    has_env_dropbox_credentials,
    verify_connection_credentials,
)
from recipes.shared.units import parse_quantity

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

TAG_FAMILIES = ("origin", "diet", "protein", "cooking_method")


def _admin_table_context(request: Request, conn: sqlite3.Connection) -> JsonDict:
    """Context for the admin table page."""
    from recipes.shared.web import _base_context, _resolve_request_lang

    lang = _resolve_request_lang(request)
    return _base_context(
        request,
        recipes=get_all_recipes_admin(lang=lang, conn=conn),
        blacklisted=get_blacklisted_files(conn=conn, lang=lang),
        failed=get_failed_files(conn=conn, lang=lang),
        orphans=get_orphaned_recipes(lang=lang, conn=conn),
        all_categories=get_all_categories(only_used=False, lang=lang, conn=conn),
        all_tags=get_existing_tags_for_prompt(lang=lang, conn=conn),
        all_tag_families=get_tag_families(lang=lang, conn=conn),
    )


def _recipe_row(recipe: JsonDict) -> JsonDict:
    """Flatten a recipe for the admin table (Tabulator)."""
    from recipes.shared.tagger import TAGGER_VERSION

    raw_category = recipe.get("category")
    category = raw_category if isinstance(raw_category, dict) else None
    raw_tags = recipe.get("tags")
    tags = [t for t in raw_tags if isinstance(t, dict)] if isinstance(raw_tags, list) else []
    raw_version = recipe.get("tagger_version")
    tagger_version = int(str(raw_version)) if raw_version is not None else None
    return {
        "id": int(str(recipe["id"])),
        "slug": str(recipe.get("slug") or recipe["id"]),
        "title": str(recipe["title"]),
        "source_url": str(recipe.get("source_url") or ""),
        "source": str(recipe.get("source") or ""),
        "date": str(recipe.get("date") or ""),
        "provenance": str(recipe["provenance"]) if recipe.get("provenance") else "",
        "created_at": str(recipe["created_at"]) if recipe.get("created_at") else "",
        "file_modified_at": str(recipe["file_modified_at"])
        if recipe.get("file_modified_at")
        else "",
        "category_name": str(category["name"]) if category else "",
        "category_display_name": str(category["display_name"]) if category else "",
        "tags": [
            {
                "family": str(t["family"]),
                "name": str(t["name"]),
                "display_name": str(t["display_name"]),
            }
            for t in tags
        ],
        "manually_edited": bool(recipe.get("manually_edited")),
        "source_missing": bool(recipe.get("source_missing")),
        "force_visible": bool(recipe.get("force_visible")),
        "favorite_count": int(str(recipe["favorite_count"])) if recipe.get("favorite_count") else 0,
        "tagger_version": tagger_version,
        "tagger_model": str(recipe.get("tagger_model") or ""),
        "tagger_outdated": tagger_version is None or tagger_version != TAGGER_VERSION,
    }


def _parse_tag_keys(keys: list[str]) -> dict[str, list[str]]:
    """Parse 'origin:francais' -> {"origin": ["francais"]}; unknown families ignored."""
    result: dict[str, list[str]] = {}
    for key in keys:
        family, _, name = key.partition(":")
        if family in TAG_FAMILIES and name:
            result.setdefault(family, []).append(name)
    return result


def _admin_config_context(
    request: Request, conn: sqlite3.Connection, message: tuple[str, str] | None = None
) -> JsonDict:
    """Context for the admin config page. `message` = (kind, text)."""
    from recipes.shared.web import _base_context

    return _base_context(
        request,
        connections=get_dropbox_connections(conn=conn),
        env_dropbox_enabled=has_env_dropbox_credentials(),
        default_active=is_default_account_active(conn=conn),
        default_visible=is_default_account_visible(conn=conn),
        dropbox_folder=DROPBOX_FOLDER,
        llm_model=get_setting("llm_model", "", conn=conn),
        llm_model_default=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
        message=message,
    )


def _dropbox_redirect_uri(request: Request) -> str:
    """Construit le redirect_uri OAuth Dropbox.

    Respecte ``DROPBOX_REDIRECT_URI`` / ``PUBLIC_URL`` / ``APP_BASE_URL`` si
    défini (utile derrière un reverse-proxy), sinon le schéma vu par le
    client via ``X-Forwarded-Proto`` — car ``request.base_url`` vaut
    ``http://`` derrière un proxy nginx sans ce header, ce que Dropbox
    refuse (``Invalid redirect_uri ... must start with "https://"``).
    """
    override = (
        os.environ.get("DROPBOX_REDIRECT_URI")
        or os.environ.get("PUBLIC_URL")
        or os.environ.get("APP_BASE_URL")
    )
    if override:
        return f"{override.rstrip('/')}/admin/config/dropbox/callback"
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    # X-Forwarded-Proto peut valoir "https, http" : on garde la première.
    scheme = scheme.split(",")[0].strip().lower() or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.url.hostname or ""
    # Préserve un port non-standard éventuel.
    port = request.url.port
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc = host
    if port and not default_port and ":" not in host:
        netloc = f"{host}:{port}"
    elif not netloc:
        # Repli : base_url d'origine.
        return f"{str(request.base_url).rstrip('/')}/admin/config/dropbox/callback"
    return f"{scheme}://{netloc}/admin/config/dropbox/callback"


def _config_template_name(request: Request) -> str:
    """Full page if browser navigation, partial if HTMX swap."""
    return "partials/admin_config.html" if "HX-Request" in request.headers else "admin.html"


def _admin_config_oauth_context(
    request: Request, conn: sqlite3.Connection, refresh_token: str, account_label: str
) -> JsonDict:
    ctx = _admin_config_context(
        request,
        conn,
        (
            "ok",
            "Compte Dropbox autorise. Choisissez un nom pour finaliser la connexion.",
        ),
    )
    ctx["oauth_refresh_token"] = refresh_token
    ctx["oauth_account_label"] = account_label
    return ctx


def _toggle_response(
    request: Request,
    conn: sqlite3.Connection,
    label: str,
    active: bool,
    visible: bool | None = None,
) -> HTMLResponse:
    from recipes.shared.web import templates

    if visible is None:
        etat = "demarree" if active else "arretee"
    else:
        etat = "visible" if visible else "masquee"
    return templates.TemplateResponse(
        request=request,
        name=_config_template_name(request),
        context=_admin_config_context(request, conn, ("ok", f"Synchronisation '{label}' {etat}.")),
    )


def _ingredients_from_form(form: Any) -> list[JsonDict]:
    """Build ingredients list from form rows (ing_min/ing_max/ing_unit/ing_food)."""
    foods = form.getlist("ing_food")
    mins = form.getlist("ing_min")
    maxs = form.getlist("ing_max")
    units = form.getlist("ing_unit")

    ingredients: list[JsonDict] = []
    for i, food in enumerate(foods):
        qmin = parse_quantity(mins[i]) if i < len(mins) else None
        qmax = parse_quantity(maxs[i]) if i < len(maxs) else None
        unit = units[i].strip() if i < len(units) else ""
        name = food.strip()
        if not name and qmin is None:
            continue
        ingredients.append(
            {
                "food": name,
                "quantity_min": qmin,
                "quantity_max": qmax,
                "unit": unit or None,
            }
        )
    return ingredients


def _tags_from_form(form: Any) -> dict[str, list[str]]:
    return {
        family: [name for name in form.getlist(f"tags_{family}") if name] for family in TAG_FAMILIES
    }


# ---------------------------------------------------------------------------
# Admin page routes
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    from recipes.shared.web import templates

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context=_admin_table_context(request, conn),
    )


@router.get("/config", response_class=HTMLResponse)
async def admin_config_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    from recipes.shared.web import templates

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context=_admin_config_context(request, conn),
    )


@router.get("/recipes.json")
async def admin_recipes_data(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> JsonDict:
    """Data for the admin table: recipes, categories, and tags."""
    from recipes.shared.tagger import TAGGER_VERSION
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    return {
        "recipes": [_recipe_row(r) for r in get_all_recipes_admin(lang=lang, conn=conn)],
        "categories": get_all_categories(only_used=False, lang=lang, conn=conn),
        "tags": get_existing_tags_for_prompt(lang=lang, conn=conn),
        "tagger_version": TAGGER_VERSION,
    }


@router.post("/retag/{recipe_id}")
async def admin_retag_recipe(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    recipe_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> JsonDict:
    """Relance le tagger actuel sur une recette (re-download Dropbox)."""
    from recipes.shared.tagger import TAGGER_VERSION
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    try:
        result = retag_recipe(recipe_id, conn=conn)
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=gettext("recipe.not_found", lang)) from None
        log.exception("Retag failed for recipe #%s", recipe_id)
        raise HTTPException(status_code=502, detail=str(e)) from None
    except Exception as e:
        log.exception("Retag failed for recipe #%s", recipe_id)
        raise HTTPException(status_code=502, detail=str(e)) from None
    # Marque le flag dans la réponse pour le rafraîchissement optimiste.
    raw_version = result.get("tagger_version")
    result["tagger_outdated"] = raw_version is None or int(str(raw_version)) != TAGGER_VERSION
    return {"ok": True, **result}


@router.post("/retag-bulk")
async def admin_retag_bulk(
    data: BulkRetagUpdate,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> JsonDict:
    """Retag en masse : relance le tagger sur chaque recette listée."""
    results: list[JsonDict] = []
    updated = 0
    for rid in data.ids:
        try:
            result = retag_recipe(int(rid), conn=conn)
            updated += 1
            results.append({"id": int(rid), "ok": True, **result})
        except Exception as e:
            log.warning("Bulk retag failed for recipe #%s: %s", rid, e)
            results.append({"id": int(rid), "ok": False, "error": str(e)})
    return {"ok": True, "updated": updated, "results": results}


@router.get("/files.json")
async def admin_files_data(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> JsonDict:
    """Data for blacklisted and failed files tables."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    return {
        "blacklisted": [
            {
                "path": str(item["path"]),
                "provenance": str(item.get("provenance") or ""),
                "date": str(item.get("blacklisted_at") or ""),
            }
            for item in get_blacklisted_files(conn=conn, lang=lang)
        ],
        "failed": [
            {
                "path": str(item["path"]),
                "provenance": str(item.get("provenance") or ""),
                "error": str(item.get("error") or ""),
                "date": str(item.get("failed_at") or ""),
            }
            for item in get_failed_files(conn=conn, lang=lang)
        ],
        "orphans": [_orphan_row(item) for item in get_orphaned_recipes(lang=lang, conn=conn)],
    }


def _orphan_row(item: JsonDict) -> JsonDict:
    """Flatten an orphaned recipe for the admin orphans table."""
    provenance = item.get("provenance")
    prov_name = provenance.get("name") if isinstance(provenance, dict) else None
    return {
        "id": int(str(item["id"])),
        "title": str(item["title"]),
        "path": str(item["source_file"]),
        "provenance": str(prov_name or ""),
        "visible": bool(item.get("force_visible")),
    }


@router.post("/inline/{recipe_id}/category")
async def admin_inline_category(
    request: Request,
    data: InlineCategoryUpdate,
    conn: sqlite3.Connection = Depends(get_db),
    recipe_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> JsonDict:
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    if not update_recipe_category(recipe_id, data.category, conn=conn):
        raise HTTPException(status_code=404, detail=gettext("recipe.not_found", lang))
    return {"ok": True}


@router.post("/inline/{recipe_id}/tags")
async def admin_inline_tags(
    request: Request,
    data: InlineTagsUpdate,
    conn: sqlite3.Connection = Depends(get_db),
    recipe_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> JsonDict:
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    if not update_recipe_tags(recipe_id, _parse_tag_keys(data.tags), conn=conn):
        raise HTTPException(status_code=404, detail=gettext("recipe.not_found", lang))
    return {"ok": True}


@router.post("/bulk/category")
async def admin_bulk_category(
    data: BulkCategoryUpdate,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> JsonDict:
    updated = bulk_update_category(data.ids, data.category, conn=conn)
    return {"ok": True, "updated": updated}


@router.post("/bulk/tags")
async def admin_bulk_tags(
    data: BulkTagsUpdate,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> JsonDict:
    updated = bulk_update_tags(
        data.ids, _parse_tag_keys(data.add), _parse_tag_keys(data.remove), conn=conn
    )
    return {"ok": True, "updated": updated}


# ---------------------------------------------------------------------------
# Config routes
# ---------------------------------------------------------------------------


@router.post("/config/dropbox", response_class=HTMLResponse)
async def admin_config_add_dropbox(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    from recipes.shared.web import templates

    form = await request.form()
    name = str(form.get("name") or "").strip()
    refresh_token = str(form.get("refresh_token") or "").strip()
    folder = str(form.get("folder") or "").strip()
    file_filter = str(form.get("file_filter") or "").strip()

    if not name or not refresh_token:
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(
                request,
                conn,
                ("error", "Le nom et le refresh token sont obligatoires."),
            ),
            status_code=422,
        )

    connection_id = add_dropbox_connection(
        name=name,
        refresh_token=refresh_token,
        folder=folder,
        file_filter=file_filter,
        conn=conn,
    )
    if connection_id is None:
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(
                request, conn, ("error", f"Une connexion nommee '{name}' existe deja.")
            ),
            status_code=422,
        )

    return templates.TemplateResponse(
        request=request,
        name=_config_template_name(request),
        context=_admin_config_context(
            request,
            conn,
            ("ok", f"Connexion '{name}' ajoutee. Elle sera utilisee au prochain scan."),
        ),
    )


@router.get("/config/dropbox/connect")
async def admin_config_connect_dropbox(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> Response:
    """Redirect to Dropbox authorization page (offline OAuth2 flow)."""
    from recipes.shared.web import templates

    state = secrets.token_urlsafe(24)
    verifier, challenge = create_pkce_pair()
    set_setting("dropbox_oauth_state", state, conn=conn)
    set_setting("dropbox_oauth_verifier", verifier, conn=conn)
    try:
        url = build_oauth_authorize_url(_dropbox_redirect_uri(request), state, challenge)
    except ValueError as e:
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(request, conn, ("error", str(e))),
            status_code=422,
        )
    return RedirectResponse(url=url, status_code=302)


@router.get("/config/dropbox/callback", response_class=HTMLResponse, response_model=None)
async def admin_config_dropbox_callback(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse | RedirectResponse:
    """Receive Dropbox authorization code and exchange for refresh token."""
    from recipes.shared.web import templates

    template_name = _config_template_name(request)
    received_state = request.query_params.get("state")
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context=_admin_config_context(
                request, conn, ("error", f"Autorisation Dropbox refusee : {error}")
            ),
        )

    expected_state = get_setting("dropbox_oauth_state", conn=conn)
    verifier = get_setting("dropbox_oauth_verifier", conn=conn)
    delete_setting("dropbox_oauth_state", conn=conn)
    delete_setting("dropbox_oauth_verifier", conn=conn)

    if not code:
        detail = "code manquant"
    elif not expected_state or received_state != expected_state:
        detail = "state invalide — relancez la connexion depuis la page de configuration"
    else:
        detail = ""

    if detail:
        log.warning(
            "Dropbox OAuth callback rejected: %s (received state present: %s)",
            detail,
            bool(received_state),
        )
        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context=_admin_config_context(
                request, conn, ("error", f"Reponse Dropbox invalide ({detail}).")
            ),
            status_code=422,
        )

    try:
        refresh_token = exchange_authorization_code(
            str(code), _dropbox_redirect_uri(request), verifier or None
        )
        try:
            account_label = verify_connection_credentials(refresh_token)
        except Exception as e:
            log.warning("Could not fetch account label after OAuth: %s", e)
            account_label = ""
    except Exception as e:
        log.exception("Dropbox OAuth code exchange failed")
        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context=_admin_config_context(
                request, conn, ("error", f"Echange du code echoue : {e}")
            ),
        )

    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context=_admin_config_oauth_context(request, conn, refresh_token, account_label),
    )


@router.post("/config/model", response_class=HTMLResponse)
async def admin_config_set_model(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    """Override global LLM model for recipe analysis."""
    from recipes.shared.web import templates

    form = await request.form()
    model = str(form.get("llm_model") or "").strip()

    if model:
        set_setting("llm_model", model, conn=conn)
        message = ("ok", f"Modele LLM defini : '{model}'.")
    else:
        delete_setting("llm_model", conn=conn)
        message = ("ok", "Override retire : retour au modele du .env.")

    return templates.TemplateResponse(
        request=request,
        name=_config_template_name(request),
        context=_admin_config_context(request, conn, message),
    )


@router.post("/config/dropbox/default/toggle-active", response_class=HTMLResponse)
async def admin_config_toggle_default_active(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    new_active = not is_default_account_active(conn=conn)
    set_default_account_active(new_active, conn=conn)
    return _toggle_response(request, conn, "Défaut (.env)", new_active)


@router.post("/config/dropbox/default/toggle-visible", response_class=HTMLResponse)
async def admin_config_toggle_default_visible(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    new_visible = not is_default_account_visible(conn=conn)
    set_default_account_visible(new_visible, conn=conn)
    return _toggle_response(request, conn, "Défaut (.env)", True, visible=new_visible)


@router.post("/config/dropbox/default/delete", response_class=HTMLResponse)
async def admin_config_delete_default(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    from recipes.shared.web import templates

    return templates.TemplateResponse(
        request=request,
        name=_config_template_name(request),
        context=_admin_config_context(
            request,
            conn,
            ("error", "Le compte par defaut (.env) ne peut pas etre supprime ici."),
        ),
    )


@router.post("/config/dropbox/{connection_id}/toggle-active", response_class=HTMLResponse)
async def admin_config_toggle_active(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    connection_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    from recipes.shared.web import templates

    dbx_conn = get_dropbox_connection_credentials(connection_id, conn=conn)
    if not dbx_conn:
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(request, conn, ("error", "Connexion introuvable.")),
            status_code=404,
        )
    connections = {c["id"]: c for c in get_dropbox_connections(conn=conn)}
    new_active = not bool(connections[connection_id]["active"])
    set_dropbox_connection_active(connection_id, new_active, conn=conn)
    return _toggle_response(request, conn, str(dbx_conn["name"]), new_active)


@router.post("/config/dropbox/{connection_id}/toggle-visible", response_class=HTMLResponse)
async def admin_config_toggle_visible(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    connection_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    from recipes.shared.web import templates

    dbx_conn = get_dropbox_connection_credentials(connection_id, conn=conn)
    if not dbx_conn:
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(request, conn, ("error", "Connexion introuvable.")),
            status_code=404,
        )
    connections = {c["id"]: c for c in get_dropbox_connections(conn=conn)}
    new_visible = not bool(connections[connection_id]["visible"])
    set_dropbox_connection_visible(connection_id, new_visible, conn=conn)
    return _toggle_response(request, conn, str(dbx_conn["name"]), True, visible=new_visible)


@router.post("/config/dropbox/{connection_id}/delete", response_class=HTMLResponse)
async def admin_config_delete_dropbox(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    connection_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    from recipes.shared.web import templates

    if delete_dropbox_connection(connection_id, conn=conn):
        forget_connection_client(connection_id)
        message = (
            "ok",
            "Connexion supprimee, ainsi que ses recettes et fichiers associes.",
        )
    else:
        message = ("error", "Connexion introuvable.")
    return templates.TemplateResponse(
        request=request,
        name=_config_template_name(request),
        context=_admin_config_context(request, conn, message),
    )


@router.post("/config/dropbox/{connection_id}/test", response_class=HTMLResponse)
async def admin_config_test_dropbox(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    connection_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    from recipes.shared.web import templates

    dbx_conn = get_dropbox_connection_credentials(connection_id, conn=conn)
    if not dbx_conn:
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(request, conn, ("error", "Connexion introuvable.")),
            status_code=404,
        )

    try:
        account_label = verify_connection_credentials(str(dbx_conn["refresh_token"]))
    except Exception as e:
        log.exception("Dropbox connection test failed for '%s'", dbx_conn["name"])
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(
                request, conn, ("error", f"Echec de connexion pour '{dbx_conn['name']}' : {e}")
            ),
        )

    return templates.TemplateResponse(
        request=request,
        name=_config_template_name(request),
        context=_admin_config_context(
            request, conn, ("ok", f"Connexion '{dbx_conn['name']}' validee : {account_label}")
        ),
    )


# ---------------------------------------------------------------------------
# Recipe edit routes
# ---------------------------------------------------------------------------


@router.get("/edit/{recipe_id}", response_class=HTMLResponse)
async def admin_edit_form(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    recipe_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    recipe = get_recipe(recipe_id, lang=lang, conn=conn, include_hidden=True)
    if not recipe:
        raise HTTPException(status_code=404, detail=gettext("recipe.not_found", lang))
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_edit.html",
        context=_base_context(
            request,
            recipe=recipe,
            all_categories=get_all_categories(only_used=False, lang=lang, conn=conn),
            all_tags=get_existing_tags_for_prompt(lang=lang, conn=conn),
        ),
    )


@router.post("/edit/{recipe_id}", response_class=HTMLResponse)
async def admin_edit_save(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    recipe_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    from recipes.shared.web import _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    recipe = get_recipe(recipe_id, conn=conn, include_hidden=True)
    if not recipe:
        raise HTTPException(status_code=404, detail=gettext("recipe.not_found", lang))

    form = await request.form()
    title = str(form.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail=gettext("error.title_required", lang))

    description = str(form.get("description") or "").strip()
    steps_json = str(form.get("steps") or "[]").strip()
    try:
        steps = json.loads(steps_json)
        if not isinstance(steps, list):
            steps = []
    except json.JSONDecodeError:
        steps = []
    ingredients = _ingredients_from_form(form)

    base_payload = {
        "title": title,
        "description": description,
        "steps": steps,
        "ingredients": ingredients,
    }
    data: JsonDict = {
        "lang_fr": dict(base_payload),
        "lang_en": dict(base_payload),
        "servings": parse_quantity(form.get("servings")),
        "category": str(form.get("category") or "").strip() or None,
        "source_url": str(form.get("source_url") or "").strip() or None,
        "source": str(form.get("source") or "").strip() or None,
        "date": str(form.get("date") or "").strip() or None,
    }

    if not update_recipe_manual(recipe_id, data, conn=conn):
        raise HTTPException(status_code=404, detail=gettext("recipe.not_found", lang))
    sync_recipe_tags(recipe_id, _tags_from_form(form), conn=conn)

    return templates.TemplateResponse(
        request=request,
        name="partials/admin_table.html",
        context=_admin_table_context(request, conn),
    )


# ---------------------------------------------------------------------------
# Blacklist and failed files routes
# ---------------------------------------------------------------------------


@router.post("/blacklist/{recipe_id}")
async def admin_blacklist(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    recipe_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    from recipes.shared.web import templates

    blacklist_and_delete_recipe(recipe_id, conn=conn)
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_table.html",
        context=_admin_table_context(request, conn),
    )


@router.post("/unblacklist")
async def admin_unblacklist(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    path: str = Query(...),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    from recipes.shared.web import templates

    remove_from_blacklist(path, conn=conn)
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_table.html",
        context=_admin_table_context(request, conn),
    )


@router.post("/retry-failed")
async def admin_retry_failed(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    path: str = Query(...),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    from recipes.shared.web import templates

    remove_failed_file(path, conn=conn)
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_table.html",
        context=_admin_table_context(request, conn),
    )


@router.post("/orphans/{recipe_id}/show")
async def admin_orphan_show(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    recipe_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    """Force l'affichage d'une recette dont la source a disparu."""
    from recipes.shared.web import _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    if not set_recipe_force_visible(recipe_id, True, conn=conn):
        raise HTTPException(status_code=404, detail=gettext("recipe.not_found", lang))
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_table.html",
        context=_admin_table_context(request, conn),
    )


@router.post("/orphans/{recipe_id}/hide")
async def admin_orphan_hide(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    recipe_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    """Annule l'affichage forcé d'une recette orpheline."""
    from recipes.shared.web import _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    if not set_recipe_force_visible(recipe_id, False, conn=conn):
        raise HTTPException(status_code=404, detail=gettext("recipe.not_found", lang))
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_table.html",
        context=_admin_table_context(request, conn),
    )


# ---------------------------------------------------------------------------
# Admin shopping list management
# ---------------------------------------------------------------------------


@router.get("/shopping", response_class=HTMLResponse)
async def admin_shopping_lists(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    """Admin page to view and manage all shopping lists."""
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    lists = get_all_shopping_lists(conn=conn)

    lists_with_details = []
    for lst in lists:
        items = get_shopping_list_items(int(str(lst["id"])), lang=lang, conn=conn)
        lst["item_count"] = len(items)
        done_count = sum(1 for i in items if i["is_done"])
        lst["done_count"] = done_count
        lists_with_details.append(lst)

    return templates.TemplateResponse(
        request=request,
        name="admin_shopping.html",
        context=_base_context(
            request,
            lists=lists_with_details,
        ),
    )


@router.get("/shopping/{list_id}", response_class=HTMLResponse)
async def admin_shopping_list_view(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    list_id: int = Path(gt=0),
    mode: str = Query(default="edit"),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    """Admin view of a shopping list (read-only, doesn't affect counters)."""
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    shopping_list = get_shopping_list_by_id(list_id, conn=conn)
    if not shopping_list:
        raise HTTPException(status_code=404, detail=gettext("error.shopping_list_not_found", lang))

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
            can_edit=False,
            is_admin_view=True,
        ),
    )


@router.post("/shopping/{list_id}/delete")
async def admin_shopping_list_delete(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    list_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> RedirectResponse:
    """Admin delete a shopping list."""
    delete_shopping_list(list_id, conn=conn)
    return RedirectResponse(url="/admin/shopping", status_code=303)
