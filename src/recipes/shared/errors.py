"""errors.py — Rendu centralisé des pages d'erreur.

Une seule responsabilité : négocier le format de réponse des erreurs
(HTML pour les navigateurs, JSON pour les clients API / TestClient) :
- 404 contextuelle (variante recette / épicerie / générique),
- 403 / 422 / 500 génériques via `error.html`.
"""

from typing import Literal, TypeAlias

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from recipes.shared.i18n import gettext
from recipes.shared.web import _base_context, _resolve_request_lang, templates

NotFoundVariant: TypeAlias = Literal["recipe", "shopping", "generic"]
ErrorStatus: TypeAlias = Literal[403, 422, 500]

_ERROR_TITLE_KEYS: dict[ErrorStatus, str] = {
    403: "errorpage.title_403",
    422: "errorpage.title_422",
    500: "errorpage.title_500",
}

_ERROR_MESSAGE_KEYS: dict[ErrorStatus, str] = {
    403: "errorpage.message_403",
    422: "errorpage.message_422",
    500: "errorpage.message_500",
}


def infer_not_found_variant(path: str) -> NotFoundVariant:
    """Déduit la variante 404 depuis le chemin demandé."""
    normalized = (path or "").lower()
    if normalized.startswith("/recipe") or normalized.startswith("/favorites"):
        return "recipe"
    if normalized.startswith("/shopping"):
        return "shopping"
    return "generic"


def wants_html(request: Request) -> bool:
    """True si le client attend du HTML (navigation navigateur / HTMX)."""
    accept = request.headers.get("accept", "")
    return "text/html" in accept.lower()


def default_detail(variant: NotFoundVariant, lang: str) -> str:
    """Message `detail` par défaut pour chaque variante (réutilisé en JSON)."""
    if variant == "recipe":
        return gettext("recipe.not_found", lang)
    if variant == "shopping":
        return gettext("error.shopping_list_not_found", lang)
    return gettext("notfound.message_generic", lang)


def render_not_found(
    request: Request,
    variant: NotFoundVariant | None = None,
    detail: str | None = None,
) -> Response:
    """Rend une 404 HTML (navigateur) ou JSON (API/TestClient).

    Pourquoi la négociation de contenu : les endpoints déclarés
    `HTMLResponse` sont aussi appelés par des clients JSON (tests,
    fetch) qui attendent `{"detail": ...}`. Seuls les clients qui
    annoncent `Accept: text/html` reçoivent la page complète.
    """
    lang = _resolve_request_lang(request)
    resolved: NotFoundVariant = variant or infer_not_found_variant(request.url.path)
    message = detail or default_detail(resolved, lang)
    if not wants_html(request):
        return JSONResponse(status_code=404, content={"detail": message})
    return templates.TemplateResponse(
        request=request,
        name="404.html",
        status_code=404,
        context=_base_context(
            request,
            variant=resolved,
            detail=message,
            request_path=request.url.path,
        ),
    )


def render_error(
    request: Request,
    status_code: ErrorStatus,
    detail: str | None = None,
) -> Response:
    """Rend une erreur 403 / 422 / 500 en HTML (navigateur) ou JSON (API).

    La 500 ne répercute jamais `detail` côté HTML : le message générique
    évite de fuir des détails internes, le vrai traceback part dans les
    logs via le handler `main.py` (`logging.exception`).
    """
    lang = _resolve_request_lang(request)
    title = gettext(_ERROR_TITLE_KEYS[status_code], lang)
    if status_code == 500 or detail is None:
        message = gettext(_ERROR_MESSAGE_KEYS[status_code], lang)
    else:
        message = detail
    if not wants_html(request):
        return JSONResponse(status_code=status_code, content={"detail": message})
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        status_code=status_code,
        context=_base_context(
            request,
            code=status_code,
            title=title,
            message=message,
            request_path=request.url.path,
        ),
    )
