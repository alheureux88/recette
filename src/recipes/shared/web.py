"""web.py — Infrastructure web partagée (templates Jinja + contexte de base).

Extrait de `recipes.main` pour inverser la dépendance feature → main :
les features importent désormais `recipes.shared.web`, jamais `recipes.main`.
"""

from contextvars import ContextVar

from fastapi import Request
from fastapi.templating import Jinja2Templates

from recipes.shared.auth import (
    OIDC_ENABLED,
    get_user,
    is_admin,
    login_url,
    logout_url,
)
from recipes.shared.db import DEFAULT_ACCOUNT_ID
from recipes.shared.i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGE_COOKIE,
    SUPPORTED_LANGUAGES,
    available_languages,
    gettext,
    ngettext,
    resolve_language,
)

current_lang: ContextVar[str] = ContextVar("request_lang", default=DEFAULT_LANGUAGE)


def _resolve_request_lang(request: Request) -> str:
    """Résout la langue depuis le cookie ou l'en-tête Accept-Language."""
    return resolve_language(
        request.cookies.get(LANGUAGE_COOKIE),
        request.headers.get("accept-language"),
    )


def _translate(key: str, **values: object) -> str:
    """Helper exposé dans les templates comme `{{ _('key', **values) }}`.

    La langue courante vient de `current_lang` (ContextVar posée par
    le middleware `locale_middleware`) : sûr en async / concurrence,
    contrairement à un attribut global mutable.
    """
    return gettext(key, current_lang.get(), **values)


def _ntranslate(singular: str, plural: str, n: int, **values: object) -> str:
    """Helper pluriel exposé dans les templates comme `ngettext`."""
    return ngettext(singular, plural, n, current_lang.get(), **values)


templates = Jinja2Templates(directory="templates")
templates.env.globals["_"] = _translate
templates.env.globals["ngettext"] = _ntranslate
templates.env.globals["supported_languages"] = SUPPORTED_LANGUAGES
templates.env.globals["default_language"] = DEFAULT_LANGUAGE


def _base_context(request: Request, **extra: object) -> dict[str, object]:
    """Contexte de base commun à toutes les pages HTML."""
    ctx: dict[str, object] = {
        "user": get_user(request),
        "auth_enabled": OIDC_ENABLED,
        "login_url": login_url(request),
        "logout_url": logout_url(request),
        "is_admin": is_admin(request),
        "lang": _resolve_request_lang(request),
        "available_languages": available_languages(),
    }
    ctx.update(extra)
    return ctx


def _shopping_list_user_id(request: Request) -> int | None:
    """Return the user ID for shopping list ownership, or None for anonymous."""
    user = get_user(request)
    return user["id"] if user else None


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


def _provenance_context(request: Request, conn: object) -> dict[str, object]:
    """Filtre de provenance : affiché seulement si plusieurs comptes ont des recettes.

    Import local pour éviter une dépendance shared → features au niveau module.
    """
    from recipes.features.admin.services import get_recipe_provenances

    lang = _resolve_request_lang(request)
    provenances = get_recipe_provenances(conn=conn)  # type: ignore[arg-type]
    for provenance in provenances:
        if provenance.get("id") == DEFAULT_ACCOUNT_ID:
            provenance["name"] = gettext("account.default", lang)
    return {"provenances": provenances, "show_provenance": len(provenances) > 1}
