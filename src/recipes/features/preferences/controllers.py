"""Preferences feature — per-user settings page (logged-in users only)."""

import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from recipes.features.preferences.services import (
    VALID_THEMES,
    get_department_order,
    get_preferences,
    save_preferences,
)
from recipes.features.shopping.services import (
    apply_department_order,
    get_shopping_departments,
)
from recipes.shared import auth as auth_module
from recipes.shared.db import get_db
from recipes.shared.i18n import (
    COOKIE_MAX_AGE,
    LANGUAGE_COOKIE,
    SUPPORTED_LANGUAGES,
    THEME_COOKIE,
    gettext,
)
from recipes.shared.models import DepartmentOrderUpdate, JsonDict, ThemeUpdate

router = APIRouter(tags=["preferences"])


def _current_user_id(request: Request) -> int | None:
    """Return the logged-in user ID, or None for anonymous sessions."""
    # Résolution via le module (et non un import direct) pour que les tests
    # puissent simuler l'usager avec monkeypatch sur `recipes.shared.auth`.
    user = auth_module.get_user(request)
    if not user:
        return None
    try:
        return int(str(user.get("id")))
    except (TypeError, ValueError):
        return None


def _login_redirect() -> RedirectResponse:
    if not auth_module.OIDC_ENABLED:
        return RedirectResponse(url="/", status_code=302)
    return RedirectResponse(url="/auth/login", status_code=302)


@router.get("/preferences", response_class=HTMLResponse, response_model=None)
async def preferences_page(
    request: Request, conn: sqlite3.Connection = Depends(get_db)
) -> HTMLResponse | RedirectResponse:
    """Show the preferences page (logged-in users only)."""
    from recipes.shared.web import _base_context, _resolve_request_lang, templates

    user_id = _current_user_id(request)
    if user_id is None:
        return _login_redirect()
    lang = _resolve_request_lang(request)
    prefs = get_preferences(user_id, conn=conn)
    departments = get_shopping_departments(lang=lang, conn=conn)
    ordered = apply_department_order(
        departments, [str(n) for n in prefs.get("department_order", [])]
    )
    saved = request.query_params.get("saved") == "1"
    return templates.TemplateResponse(
        request=request,
        name="preferences.html",
        context=_base_context(
            request,
            prefs=prefs,
            departments=ordered,
            saved=saved,
        ),
    )


@router.post("/preferences", response_model=None)
async def preferences_save(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    language: str | None = Form(default=None),
    units: str | None = Form(default=None),
    theme: str | None = Form(default=None),
    print_images: str | None = Form(default=None),
    print_tags: str | None = Form(default=None),
    print_description: str | None = Form(default=None),
    print_links: str | None = Form(default=None),
    print_step_ingredients: str | None = Form(default=None),
    show_step_ingredients: str | None = Form(default=None),
    department_order: str | None = Form(default=None),
) -> RedirectResponse:
    """Save preferences from the HTML form, then redirect back."""
    from recipes.shared.web import _resolve_request_lang

    user_id = _current_user_id(request)
    if user_id is None:
        return _login_redirect()
    patch: JsonDict = {}
    if language is not None:
        patch["language"] = language
    if units is not None:
        patch["units"] = units
    if theme is not None:
        patch["theme"] = theme
    # Le formulaire envoie toujours les cases : absente = décochée.
    for key, value in (
        ("print_images", print_images),
        ("print_tags", print_tags),
        ("print_description", print_description),
        ("print_links", print_links),
        ("print_step_ingredients", print_step_ingredients),
    ):
        patch[key] = value if value is not None else False
    # Case à cocher d'affichage (défaut coché) : absente = décochée.
    patch["show_step_ingredients"] = (
        show_step_ingredients if show_step_ingredients is not None else False
    )
    if department_order is not None:
        patch["department_order"] = (
            [name for name in department_order.split(",") if name.strip()]
            if department_order.strip()
            else []
        )
    prefs = save_preferences(user_id, patch, conn=conn)
    lang = _resolve_request_lang(request)
    candidate = str(prefs.get("language", lang))
    response = RedirectResponse(url="/preferences?saved=1", status_code=303)
    if candidate in SUPPORTED_LANGUAGES:
        response.set_cookie(
            key=LANGUAGE_COOKIE,
            value=candidate,
            max_age=COOKIE_MAX_AGE,
            samesite="lax",
            httponly=True,
        )
    apply_theme_cookie(response, str(prefs.get("theme", "system")))
    return response


def apply_theme_cookie(response: RedirectResponse | JSONResponse, theme: str) -> None:
    """Reflète le thème dans un cookie (ou le supprime si 'system'/invalide).

    Le gabarit de base applique ce cookie sans accès DB, dès la requête
    suivante — y compris juste après la connexion.
    """
    if theme in ("light", "dark"):
        response.set_cookie(
            key=THEME_COOKIE,
            value=theme,
            max_age=COOKIE_MAX_AGE,
            samesite="lax",
            httponly=True,
        )
    else:
        response.delete_cookie(key=THEME_COOKIE)


@router.post("/api/preferences/departments")
async def preferences_save_order(
    request: Request,
    data: DepartmentOrderUpdate,
    conn: sqlite3.Connection = Depends(get_db),
) -> JSONResponse:
    """Save the user's department display order (drag & drop)."""
    from recipes.shared.web import _resolve_request_lang

    user_id = _current_user_id(request)
    if user_id is None:
        lang = _resolve_request_lang(request)
        raise HTTPException(status_code=401, detail=gettext("error.not_authenticated", lang))
    valid_names = {str(d.get("name")) for d in get_shopping_departments(lang="fr", conn=conn)}
    unknown = [name for name in data.order if name not in valid_names]
    if unknown:
        lang = _resolve_request_lang(request)
        raise HTTPException(status_code=400, detail=gettext("error.invalid_order", lang))
    save_preferences(user_id, {"department_order": list(data.order)}, conn=conn)
    return JSONResponse({"ok": True})


@router.post("/api/preferences/theme")
async def preferences_save_theme(
    request: Request,
    data: ThemeUpdate,
    conn: sqlite3.Connection = Depends(get_db),
) -> JSONResponse:
    """Save the theme from the header toggle (light/dark/system)."""
    from recipes.shared.web import _resolve_request_lang

    user_id = _current_user_id(request)
    if user_id is None:
        lang = _resolve_request_lang(request)
        raise HTTPException(status_code=401, detail=gettext("error.not_authenticated", lang))
    theme = data.theme.strip().lower()
    if theme not in VALID_THEMES:
        lang = _resolve_request_lang(request)
        raise HTTPException(status_code=400, detail=gettext("error.invalid_theme", lang))
    prefs = save_preferences(user_id, {"theme": theme}, conn=conn)
    saved = str(prefs.get("theme", "system"))
    response = JSONResponse({"ok": True, "theme": saved})
    apply_theme_cookie(response, saved)
    return response


def theme_for_request(request: Request) -> str:
    """Theme to apply in the base template.

    Returns 'light'/'dark' (explicit choice), 'system' (logged-in user
    following the OS) or '' (anonymous: legacy localStorage behavior).
    Cookie-based so no database access is needed on every page.
    """
    if auth_module.get_user(request) is None:
        return ""
    raw = request.cookies.get(THEME_COOKIE)
    return raw if raw in ("light", "dark") else "system"


def ordered_departments_for_user(
    request: Request,
    lang: str,
    conn: sqlite3.Connection,
) -> list[JsonDict]:
    """Shopping departments in the current user's preferred order."""
    departments = get_shopping_departments(lang=lang, conn=conn)
    return apply_department_order(
        departments, get_department_order(_current_user_id(request), conn=conn)
    )


def units_for_user(request: Request, conn: sqlite3.Connection) -> str:
    """Default units system for the current user (or 'original')."""
    user_id = _current_user_id(request)
    if user_id is None:
        return "original"
    prefs = get_preferences(user_id, conn=conn)
    units = prefs.get("units")
    return str(units) if units in ("original", "metric", "imperial") else "original"


def show_step_ingredients_for_user(request: Request, conn: sqlite3.Connection) -> bool:
    """Afficher les ingrédients dans les étapes (défaut vrai, y compris anonymes)."""
    user_id = _current_user_id(request)
    if user_id is None:
        return True
    prefs = get_preferences(user_id, conn=conn)
    value = prefs.get("show_step_ingredients", True)
    return bool(value) if isinstance(value, bool) else True


def print_prefs_for_user(request: Request, conn: sqlite3.Connection) -> dict[str, bool]:
    """Default print checkboxes for the current user."""
    user_id = _current_user_id(request)
    if user_id is None:
        return {}
    prefs = get_preferences(user_id, conn=conn)
    return {
        key: bool(prefs.get(key))
        for key in (
            "print_images",
            "print_tags",
            "print_description",
            "print_links",
            "print_step_ingredients",
        )
    }
