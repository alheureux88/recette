"""
main.py — FastAPI + Jinja2 + HTMX recipe website.
Includes a built-in APScheduler job that polls Dropbox every X minutes.

Run:  uvicorn recipes.main:app --host 0.0.0.0 --port 8000
"""

import logging
import os
import secrets
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from recipes.auth import (
    OIDC_ENABLED,
    authorize_redirect,
    fetch_token,
    get_user,
    is_admin,
    login_url,
    logout_url,
    require_admin,
    require_user,
)
from recipes.db import (
    DEFAULT_ACCOUNT_ID,
    add_dropbox_connection,
    add_favorite,
    blacklist_and_delete_recipe,
    bulk_update_category,
    bulk_update_tags,
    delete_dropbox_connection,
    delete_setting,
    get_all_categories,
    get_all_recipes_admin,
    get_all_tags_grouped,
    get_blacklisted_files,
    get_dropbox_connection_credentials,
    get_dropbox_connections,
    get_existing_tags_for_prompt,
    get_failed_files,
    get_favorite_recipes,
    get_or_create_user,
    get_recipe,
    get_recipe_provenances,
    get_setting,
    get_user_favorite_ids,
    init_db,
    is_default_account_active,
    is_default_account_visible,
    remove_failed_file,
    remove_favorite,
    remove_from_blacklist,
    search_recipes,
    set_default_account_active,
    set_default_account_visible,
    set_dropbox_connection_active,
    set_dropbox_connection_visible,
    set_setting,
    sync_recipe_tags,
    update_recipe_category,
    update_recipe_manual,
    update_recipe_tags,
)
from recipes.models import (
    BulkCategoryUpdate,
    BulkTagsUpdate,
    InlineCategoryUpdate,
    InlineTagsUpdate,
)
from recipes.poller import (
    DROPBOX_FOLDER,
    IMAGES_DIR,
    build_oauth_authorize_url,
    exchange_authorization_code,
    forget_connection_client,
    has_env_dropbox_credentials,
    verify_connection_credentials,
)
from recipes.poller import run as poll_dropbox
from recipes.units import format_ingredient, parse_quantity

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
        "is_admin": is_admin(request),
    }
    ctx.update(extra)
    return ctx


def _provenance_context() -> dict[str, object]:
    """Filtre de provenance : affiché seulement si plusieurs comptes ont des recettes."""
    provenances = get_recipe_provenances()
    return {"provenances": provenances, "show_provenance": len(provenances) > 1}


def _parse_account_param(raw: str | None) -> int | None:
    """'default' → DEFAULT_ACCOUNT_ID, entier → id de connexion, sinon None."""
    if raw is None or not raw.strip():
        return None
    if raw == "default":
        return DEFAULT_ACCOUNT_ID
    try:
        return int(raw)
    except ValueError:
        return None


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
            **_provenance_context(),
        ),
    )


@app.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = Query(default=""),
    tags: list[int] = Query(default=[]),
    category: str | None = Query(default=None),
    account: str | None = Query(default=None),
) -> HTMLResponse:
    category_id: int | None = None
    if category and category.strip():
        try:
            category_id = int(category)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="category must be a valid integer"
            ) from None
    recipes = search_recipes(
        query=q, tag_ids=tags, category_id=category_id, connection_id=_parse_account_param(account)
    )
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
            **_provenance_context(),
        },
    )


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
) -> dict[str, object]:
    """Construit le contexte d'affichage des ingrédients.

    Si la recette a un nombre de portions connu, l'ajustement se fait par portions
    (multiplicateur = portions demandées / portions de base). Sinon, l'usager peut
    multiplier directement la recette sans notion de portions.
    """
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
    display_ingredients = [format_ingredient(item, multiplicateur, systeme) for item in items]

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


@app.get("/recipe/{recipe_id}", response_class=HTMLResponse)
async def recipe_detail(
    request: Request,
    recipe_id: int = Path(gt=0),
    servings: str | None = Query(default=None),
    units: str = Query(default="original"),
    multiplier: str | None = Query(default=None),
) -> HTMLResponse:
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
            show_provenance=len(get_recipe_provenances()) > 1,
            **_ingredient_context(
                recipe, _parse_servings_param(servings), units, _parse_multiplier_param(multiplier)
            ),
        ),
    )


@app.get("/recipe/{recipe_id}/ingredients", response_class=HTMLResponse)
async def recipe_ingredients(
    request: Request,
    recipe_id: int = Path(gt=0),
    servings: str | None = Query(default=None),
    units: str = Query(default="original"),
    multiplier: str | None = Query(default=None),
) -> HTMLResponse:
    """Partial HTMX : la section ingrédients avec portions/multiplicateur et unités."""
    recipe = get_recipe(recipe_id)
    if not recipe:
        return HTMLResponse("<h1>Recette introuvable</h1>", status_code=404)
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
            ),
        },
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
    groups = userinfo.get("groups", [])
    request.session["user"] = {
        "id": user_id,
        "sub": subject,
        "name": userinfo.get("name"),
        "groups": groups,
    }
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


ADMIN_TABS = ("recipes", "config")


def _normalize_admin_tab(raw: str) -> str:
    return raw if raw in ADMIN_TABS else "recipes"


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    tab: str = Query(default="recipes"),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    tab = _normalize_admin_tab(tab)

    if tab == "config":
        # Navigation pleine page : le contexte complet de l'onglet Configuration
        # (connexions, statuts, etc.) doit etre fourni, sinon le tableau est vide.
        ctx = _admin_config_context(request)
        ctx["tab"] = tab
        return templates.TemplateResponse(request=request, name="admin.html", context=ctx)

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context=_admin_table_context(request),
    )


TAG_FAMILIES = ("origin", "diet", "protein", "cooking_method")


def _admin_table_context(request: Request) -> dict[str, object]:
    return _base_context(
        request,
        recipes=get_all_recipes_admin(),
        blacklisted=get_blacklisted_files(),
        failed=get_failed_files(),
        all_categories=get_all_categories(only_used=False),
        all_tags=get_existing_tags_for_prompt(),
        tab="recipes",
    )


def _recipe_row(recipe: dict[str, object]) -> dict[str, object]:
    """Aplati une recette admin pour le tableau client (Tabulator)."""
    raw_category = recipe.get("category")
    category = raw_category if isinstance(raw_category, dict) else None
    raw_tags = recipe.get("tags")
    tags = [t for t in raw_tags if isinstance(t, dict)] if isinstance(raw_tags, list) else []
    return {
        "id": int(str(recipe["id"])),
        "title": str(recipe["title"]),
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
        "favorite_count": int(str(recipe["favorite_count"])) if recipe.get("favorite_count") else 0,
    }


def _parse_tag_keys(keys: list[str]) -> dict[str, list[str]]:
    """'origin:francais' -> {"origin": ["francais"]} ; familles inconnues ignorees."""
    result: dict[str, list[str]] = {}
    for key in keys:
        family, _, name = key.partition(":")
        if family in TAG_FAMILIES and name:
            result.setdefault(family, []).append(name)
    return result


@app.get("/admin/recipes.json")
async def admin_recipes_data(
    _user: dict[str, Any] = Depends(require_admin),
) -> dict[str, object]:
    """Donnees du tableau d'administration : recettes, categories et etiquettes."""
    return {
        "recipes": [_recipe_row(r) for r in get_all_recipes_admin()],
        "categories": get_all_categories(only_used=False),
        "tags": get_existing_tags_for_prompt(),
    }


@app.get("/admin/files.json")
async def admin_files_data(
    _user: dict[str, Any] = Depends(require_admin),
) -> dict[str, object]:
    """Donnees des tableaux de fichiers blacklistes et en erreur."""
    return {
        "blacklisted": [
            {
                "path": str(item["path"]),
                "provenance": str(item.get("provenance") or ""),
                "date": str(item.get("blacklisted_at") or ""),
            }
            for item in get_blacklisted_files()
        ],
        "failed": [
            {
                "path": str(item["path"]),
                "provenance": str(item.get("provenance") or ""),
                "error": str(item.get("error") or ""),
                "date": str(item.get("failed_at") or ""),
            }
            for item in get_failed_files()
        ],
    }


@app.post("/admin/inline/{recipe_id}/category")
async def admin_inline_category(
    data: InlineCategoryUpdate,
    recipe_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> dict[str, object]:
    if not update_recipe_category(recipe_id, data.category):
        raise HTTPException(status_code=404, detail="Recipe not found")
    return {"ok": True}


@app.post("/admin/inline/{recipe_id}/tags")
async def admin_inline_tags(
    data: InlineTagsUpdate,
    recipe_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> dict[str, object]:
    if not update_recipe_tags(recipe_id, _parse_tag_keys(data.tags)):
        raise HTTPException(status_code=404, detail="Recipe not found")
    return {"ok": True}


@app.post("/admin/bulk/category")
async def admin_bulk_category(
    data: BulkCategoryUpdate,
    _user: dict[str, Any] = Depends(require_admin),
) -> dict[str, object]:
    updated = bulk_update_category(data.ids, data.category)
    return {"ok": True, "updated": updated}


@app.post("/admin/bulk/tags")
async def admin_bulk_tags(
    data: BulkTagsUpdate,
    _user: dict[str, Any] = Depends(require_admin),
) -> dict[str, object]:
    updated = bulk_update_tags(data.ids, _parse_tag_keys(data.add), _parse_tag_keys(data.remove))
    return {"ok": True, "updated": updated}


def _admin_config_context(
    request: Request, message: tuple[str, str] | None = None
) -> dict[str, object]:
    """Contexte du partial Configuration. `message` = (kind, text)."""
    return _base_context(
        request,
        connections=get_dropbox_connections(),
        env_dropbox_enabled=has_env_dropbox_credentials(),
        default_active=is_default_account_active(),
        default_visible=is_default_account_visible(),
        dropbox_folder=DROPBOX_FOLDER,
        llm_model=get_setting("llm_model", ""),
        llm_model_default=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
        message=message,
        tab="config",
    )


@app.post("/admin/config/dropbox", response_class=HTMLResponse)
async def admin_config_add_dropbox(
    request: Request,
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
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
                ("error", "Le nom et le refresh token sont obligatoires."),
            ),
            status_code=422,
        )

    connection_id = add_dropbox_connection(
        name=name,
        refresh_token=refresh_token,
        folder=folder,
        file_filter=file_filter,
    )
    if connection_id is None:
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(
                request, ("error", f"Une connexion nommee '{name}' existe deja.")
            ),
            status_code=422,
        )

    return templates.TemplateResponse(
        request=request,
        name=_config_template_name(request),
        context=_admin_config_context(
            request, ("ok", f"Connexion '{name}' ajoutee. Elle sera utilisee au prochain scan.")
        ),
    )


def _dropbox_redirect_uri(request: Request) -> str:
    return f"{str(request.base_url).rstrip('/')}/admin/config/dropbox/callback"


def _config_template_name(request: Request) -> str:
    """Pleine page si navigation navigateur, partial si swap HTMX."""
    return "partials/admin_config.html" if "HX-Request" in request.headers else "admin.html"


@app.get("/admin/config/dropbox/connect")
async def admin_config_connect_dropbox(
    request: Request,
    _user: dict[str, Any] = Depends(require_admin),
) -> Response:
    """Redirige vers la page d'autorisation Dropbox (flux OAuth2 offline)."""
    state = secrets.token_urlsafe(24)
    # Stocke en DB : la session cookie peut etre perdue entre le depart vers
    # Dropbox et le retour (autre hote, navigation separee, etc.)
    set_setting("dropbox_oauth_state", state)
    try:
        url = build_oauth_authorize_url(_dropbox_redirect_uri(request), state)
    except ValueError as e:
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(request, ("error", str(e))),
            status_code=422,
        )
    return RedirectResponse(url=url, status_code=302)


def _admin_config_oauth_context(
    request: Request, refresh_token: str, account_label: str
) -> dict[str, object]:
    ctx = _admin_config_context(
        request,
        (
            "ok",
            "Compte Dropbox autorise. Choisissez un nom pour finaliser la connexion.",
        ),
    )
    ctx["oauth_refresh_token"] = refresh_token
    ctx["oauth_account_label"] = account_label
    return ctx


@app.get("/admin/config/dropbox/callback", response_class=HTMLResponse, response_model=None)
async def admin_config_dropbox_callback(
    request: Request,
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse | RedirectResponse:
    """Recoit le code d'autorisation Dropbox et l'echange contre un refresh token."""
    template_name = _config_template_name(request)
    received_state = request.query_params.get("state")
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context=_admin_config_context(
                request, ("error", f"Autorisation Dropbox refusee : {error}")
            ),
        )

    expected_state = get_setting("dropbox_oauth_state")
    delete_setting("dropbox_oauth_state")

    if not code:
        detail = "code manquant"
    elif not expected_state or received_state != expected_state:
        detail = "state invalide — relancez la connexion depuis la page de configuration"
    else:
        detail = ""

    if detail:
        log.warning(
            f"Dropbox OAuth callback rejected: {detail} "
            f"(received state present: {bool(received_state)})"
        )
        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context=_admin_config_context(
                request, ("error", f"Reponse Dropbox invalide ({detail}).")
            ),
            status_code=422,
        )

    try:
        refresh_token = exchange_authorization_code(str(code), _dropbox_redirect_uri(request))
        try:
            account_label = verify_connection_credentials(refresh_token)
        except Exception as e:
            log.warning(f"Could not fetch account label after OAuth: {e}")
            account_label = ""
    except Exception as e:
        log.error(f"Dropbox OAuth code exchange failed: {e}")
        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context=_admin_config_context(request, ("error", f"Echange du code echoue : {e}")),
        )

    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context=_admin_config_oauth_context(request, refresh_token, account_label),
    )


@app.post("/admin/config/model", response_class=HTMLResponse)
async def admin_config_set_model(
    request: Request,
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    """Override global du modele LLM utilise pour l'analyse des recettes."""
    form = await request.form()
    model = str(form.get("llm_model") or "").strip()

    if model:
        set_setting("llm_model", model)
        message = ("ok", f"Modele LLM defini : '{model}'.")
    else:
        delete_setting("llm_model")
        message = ("ok", "Override retire : retour au modele du .env.")

    return templates.TemplateResponse(
        request=request,
        name=_config_template_name(request),
        context=_admin_config_context(request, message),
    )


def _toggle_response(
    request: Request, label: str, active: bool, visible: bool | None = None
) -> HTMLResponse:
    if visible is None:
        etat = "demarree" if active else "arretee"
    else:
        etat = "visible" if visible else "masquee"
    return templates.TemplateResponse(
        request=request,
        name=_config_template_name(request),
        context=_admin_config_context(request, ("ok", f"Synchronisation '{label}' {etat}.")),
    )


@app.post("/admin/config/dropbox/default/toggle-active", response_class=HTMLResponse)
async def admin_config_toggle_default_active(
    request: Request,
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    new_active = not is_default_account_active()
    set_default_account_active(new_active)
    return _toggle_response(request, "Défaut (.env)", new_active)


@app.post("/admin/config/dropbox/default/toggle-visible", response_class=HTMLResponse)
async def admin_config_toggle_default_visible(
    request: Request,
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    new_visible = not is_default_account_visible()
    set_default_account_visible(new_visible)
    return _toggle_response(request, "Défaut (.env)", True, visible=new_visible)


@app.post("/admin/config/dropbox/default/delete", response_class=HTMLResponse)
async def admin_config_delete_default(
    request: Request,
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=_config_template_name(request),
        context=_admin_config_context(
            request,
            ("error", "Le compte par defaut (.env) ne peut pas etre supprime ici."),
        ),
    )


@app.post("/admin/config/dropbox/{connection_id}/toggle-active", response_class=HTMLResponse)
async def admin_config_toggle_active(
    request: Request,
    connection_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    conn = get_dropbox_connection_credentials(connection_id)
    if not conn:
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(request, ("error", "Connexion introuvable.")),
            status_code=404,
        )
    connections = {c["id"]: c for c in get_dropbox_connections()}
    new_active = not bool(connections[connection_id]["active"])
    set_dropbox_connection_active(connection_id, new_active)
    return _toggle_response(request, str(conn["name"]), new_active)


@app.post("/admin/config/dropbox/{connection_id}/toggle-visible", response_class=HTMLResponse)
async def admin_config_toggle_visible(
    request: Request,
    connection_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    conn = get_dropbox_connection_credentials(connection_id)
    if not conn:
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(request, ("error", "Connexion introuvable.")),
            status_code=404,
        )
    connections = {c["id"]: c for c in get_dropbox_connections()}
    new_visible = not bool(connections[connection_id]["visible"])
    set_dropbox_connection_visible(connection_id, new_visible)
    return _toggle_response(request, str(conn["name"]), True, visible=new_visible)


@app.post("/admin/config/dropbox/{connection_id}/delete", response_class=HTMLResponse)
async def admin_config_delete_dropbox(
    request: Request,
    connection_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    if delete_dropbox_connection(connection_id):
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
        context=_admin_config_context(request, message),
    )


@app.post("/admin/config/dropbox/{connection_id}/test", response_class=HTMLResponse)
async def admin_config_test_dropbox(
    request: Request,
    connection_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    conn = get_dropbox_connection_credentials(connection_id)
    if not conn:
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(request, ("error", "Connexion introuvable.")),
            status_code=404,
        )

    try:
        account_label = verify_connection_credentials(str(conn["refresh_token"]))
    except Exception as e:
        log.error(f"Dropbox connection test failed for '{conn['name']}': {e}")
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(
                request, ("error", f"Echec de connexion pour '{conn['name']}' : {e}")
            ),
        )

    return templates.TemplateResponse(
        request=request,
        name=_config_template_name(request),
        context=_admin_config_context(
            request, ("ok", f"Connexion '{conn['name']}' validee : {account_label}")
        ),
    )


def _ingredients_from_form(form: Any) -> list[dict[str, object]]:
    """Construit la liste d'ingrédients à partir des rangées du formulaire
    (ing_min / ing_max / ing_unit / ing_food soumis en listes parallèles)."""
    foods = form.getlist("ing_food")
    mins = form.getlist("ing_min")
    maxs = form.getlist("ing_max")
    units = form.getlist("ing_unit")

    ingredients: list[dict[str, object]] = []
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


@app.get("/admin/edit/{recipe_id}", response_class=HTMLResponse)
async def admin_edit_form(
    request: Request,
    recipe_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    recipe = get_recipe(recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_edit.html",
        context=_base_context(
            request,
            recipe=recipe,
            all_categories=get_all_categories(only_used=False),
            all_tags=get_existing_tags_for_prompt(),
        ),
    )


@app.post("/admin/edit/{recipe_id}", response_class=HTMLResponse)
async def admin_edit_save(
    request: Request,
    recipe_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    recipe = get_recipe(recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    form = await request.form()
    title = str(form.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="title is required")

    data: dict[str, object] = {
        "title": title,
        "description": str(form.get("description") or "").strip(),
        "instructions": str(form.get("instructions") or "").strip(),
        "ingredients": _ingredients_from_form(form),
        "servings": parse_quantity(form.get("servings")),
        "category": str(form.get("category") or "").strip() or None,
        "source_url": str(form.get("source_url") or "").strip() or None,
    }

    if not update_recipe_manual(recipe_id, data):
        raise HTTPException(status_code=404, detail="Recipe not found")
    sync_recipe_tags(recipe_id, _tags_from_form(form))

    return templates.TemplateResponse(
        request=request,
        name="partials/admin_table.html",
        context=_admin_table_context(request),
    )


@app.post("/admin/blacklist/{recipe_id}")
async def admin_blacklist(
    request: Request,
    recipe_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    blacklist_and_delete_recipe(recipe_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_table.html",
        context=_admin_table_context(request),
    )


@app.post("/admin/unblacklist")
async def admin_unblacklist(
    request: Request,
    path: str = Query(...),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    remove_from_blacklist(path)
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_table.html",
        context=_admin_table_context(request),
    )


@app.post("/admin/retry-failed")
async def admin_retry_failed(
    request: Request,
    path: str = Query(...),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    remove_failed_file(path)
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_table.html",
        context=_admin_table_context(request),
    )
