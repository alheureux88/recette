"""
i18n.py — Internationalisation front-end (UI only).

Catalogue statique FR/EN avec helpers gettext/ngettext inspirés de GNU gettext.
Aucun contenu issu de la base de données n'est traduit ici : la couche DB
(génération LLM, tags, catégories) sera internationalisée dans une PR séparée.

Usage:
    from recipes.i18n import _, ngettext, available_languages, resolve_language

    msg = _("Bonjour")
    msg = ngettext("1 recette", "{n} recettes", count)
"""

from __future__ import annotations

from typing import Final

SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = ("fr", "en")
DEFAULT_LANGUAGE: Final[str] = "fr"
LANGUAGE_COOKIE: Final[str] = "lang"
COOKIE_MAX_AGE: Final[int] = 60 * 60 * 24 * 365  # 1 an

# Pluriels par langue (forme simple, deux formes). Format : (singular, plural).
# `ngettext(singular, plural, n)` choisit selon la langue et la règle associée.
# FR/EN utilisent tous deux la forme "singular si n <= 1, plural sinon".
PLURAL_RULES: Final[dict[str, str]] = {
    "fr": "nplurals=2; plural=(n > 1);",
    "en": "nplurals=2; plural=(n != 1);",
}


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
# Chaque entrée : { "fr": "...", "en": "..." }. Les placeholders {name}
# sont préservés tels quels (substitution via str.format par l'appelant si
# nécessaire ; les helpers de ce module ne les traitent pas).
TRANSLATIONS: Final[dict[str, dict[str, str]]] = {
    # --- Navigation / header ---
    "nav.toggle_theme": {
        "fr": "Basculer clair / sombre",
        "en": "Toggle light / dark",
    },
    "nav.favorites": {"fr": "Mes favoris", "en": "My favorites"},
    "nav.admin_recipes": {
        "fr": "Administration Recettes",
        "en": "Recipe administration",
    },
    "nav.admin_system": {
        "fr": "Administration Système",
        "en": "System administration",
    },
    "nav.logout": {"fr": "Déconnexion", "en": "Log out"},
    "nav.login": {"fr": "Connexion", "en": "Log in"},
    # --- Page d'accueil ---
    "home.subtitle_one": {"fr": "1 recette à découvrir", "en": "1 recipe to discover"},
    "home.subtitle_other": {
        "fr": "{n} recettes à découvrir",
        "en": "{n} recipes to discover",
    },
    "home.search_placeholder": {
        "fr": "Rechercher des recettes...",
        "en": "Search recipes...",
    },
    "home.filters_button": {"fr": "Filtres", "en": "Filters"},
    "home.filters_close": {"fr": "Fermer les filtres", "en": "Close filters"},
    "home.filters_title": {"fr": "Filtres", "en": "Filters"},
    "home.filter_category": {"fr": "Catégorie", "en": "Category"},
    "home.filter_provenance": {"fr": "Provenance", "en": "Source"},
    "home.filter_all": {"fr": "Toutes", "en": "All"},
    "home.no_results": {
        "fr": "Aucune recette trouvée. Essayez une autre recherche ou étiquette.",
        "en": "No recipe found. Try a different search or tag.",
    },
    # --- Page recette ---
    "recipe.back_to_list": {"fr": "← Toutes les recettes", "en": "← All recipes"},
    "recipe.cook_mode": {"fr": "Mode cuisine", "en": "Cook mode"},
    "recipe.print_options": {"fr": "Options d'impression", "en": "Print options"},
    "recipe.print_images": {"fr": "Images", "en": "Images"},
    "recipe.print_tags": {"fr": "Étiquettes", "en": "Tags"},
    "recipe.print_description": {"fr": "Description", "en": "Description"},
    "recipe.print_links": {"fr": "Liens externes", "en": "External links"},
    "recipe.print": {"fr": "Imprimer", "en": "Print"},
    "recipe.instructions": {"fr": "Instructions", "en": "Instructions"},
    "recipe.ingredients": {"fr": "Ingrédients", "en": "Ingredients"},
    "recipe.servings": {"fr": "Portions", "en": "Servings"},
    "recipe.multiplier": {"fr": "Multiplicateur", "en": "Multiplier"},
    "recipe.units": {"fr": "Unités", "en": "Units"},
    "recipe.units_original": {"fr": "Recette originale", "en": "Original recipe"},
    "recipe.units_metric": {"fr": "Métrique", "en": "Metric"},
    "recipe.units_imperial": {"fr": "Impérial", "en": "Imperial"},
    "recipe.not_found": {"fr": "Recette introuvable", "en": "Recipe not found"},
    "recipe.link_original_site": {
        "fr": "Voir le site original",
        "en": "View original site",
    },
    "recipe.link_original_file": {
        "fr": "Voir le fichier original",
        "en": "View original file",
    },
    # --- Mode cuisine ---
    "cook.back": {"fr": "Retour", "en": "Back"},
    "cook.back_aria": {"fr": "Retour à la recette", "en": "Back to recipe"},
    "cook.reset": {"fr": "Réinitialiser", "en": "Reset"},
    "cook.ingredients_title": {"fr": "Ingrédients", "en": "Ingredients"},
    "cook.steps_title": {"fr": "Étapes", "en": "Steps"},
    "cook.progress_ingredient_one": {"fr": "ingrédient", "en": "ingredient"},
    "cook.progress_ingredient_other": {
        "fr": "ingrédients",
        "en": "ingredients",
    },
    "cook.progress_step_one": {"fr": "étape", "en": "step"},
    "cook.progress_step_other": {"fr": "étapes", "en": "steps"},
    # --- Favoris ---
    "favorites.title": {"fr": "Mes favoris", "en": "My favorites"},
    "favorites.add": {"fr": "Ajouter aux favoris", "en": "Add to favorites"},
    "favorites.remove": {"fr": "Retirer des favoris", "en": "Remove from favorites"},
    # --- Administration : titres / nav ---
    "admin.recipes_title": {
        "fr": "Administration des recettes",
        "en": "Recipe administration",
    },
    "admin.system_title": {
        "fr": "Administration système",
        "en": "System administration",
    },
    # --- Administration : tableau ---
    "admin.filter_all": {"fr": "Toutes", "en": "All"},
    "admin.filter_no_tags": {"fr": "Sans étiquettes", "en": "No tags"},
    "admin.filter_no_category": {"fr": "Sans catégorie", "en": "No category"},
    "admin.filter_no_family": {
        "fr": "Sans {family}",
        "en": "No {family}",
    },
    "admin.selection_count": {"fr": "{n} sélectionnée(s)", "en": "{n} selected"},
    "admin.bulk_category": {"fr": "Catégorie", "en": "Category"},
    "admin.bulk_none": {"fr": "(aucune)", "en": "(none)"},
    "admin.bulk_apply": {"fr": "Appliquer", "en": "Apply"},
    "admin.bulk_add_tags": {"fr": "Ajouter étiquettes", "en": "Add tags"},
    "admin.bulk_remove_tags": {
        "fr": "Retirer étiquettes",
        "en": "Remove tags",
    },
    "admin.bulk_add": {"fr": "Ajouter", "en": "Add"},
    "admin.bulk_remove": {"fr": "Retirer", "en": "Remove"},
    "admin.blacklist_title": {"fr": "Fichiers blacklister", "en": "Blacklisted files"},
    "admin.failed_title": {"fr": "Fichiers en erreur", "en": "Failed files"},
    "admin.tab_recipe": {"fr": "Recette", "en": "Recipe"},
    "admin.tab_provenance": {"fr": "Provenance", "en": "Source"},
    "admin.tab_ingestion": {"fr": "Ingestion", "en": "Ingestion"},
    "admin.tab_file": {"fr": "Fichier", "en": "File"},
    "admin.tab_category": {"fr": "Categorie", "en": "Category"},
    "admin.tab_tags": {"fr": "Étiquettes", "en": "Tags"},
    "admin.tab_manual": {"fr": "Manuel", "en": "Manual"},
    "admin.tab_favorites": {"fr": "Favoris", "en": "Favorites"},
    "admin.action_edit": {"fr": "Modifier", "en": "Edit"},
    "admin.action_blacklist": {"fr": "Blacklister", "en": "Blacklist"},
    "admin.empty_recipes": {"fr": "Aucune recette", "en": "No recipes"},
    "admin.empty_blacklist": {
        "fr": "Aucun fichier blackliste",
        "en": "No blacklisted files",
    },
    "admin.empty_failed": {
        "fr": "Aucun fichier en erreur",
        "en": "No failed files",
    },
    "admin.col_path": {"fr": "Chemin", "en": "Path"},
    "admin.col_provenance": {"fr": "Provenance", "en": "Source"},
    "admin.col_date": {"fr": "Date", "en": "Date"},
    "admin.col_error": {"fr": "Erreur", "en": "Error"},
    "admin.action_restore": {"fr": "Retablir", "en": "Restore"},
    "admin.action_retry": {"fr": "Reessayer", "en": "Retry"},
    "admin.confirm_blacklist": {
        "fr": "Supprimer cette recette et l'empecher d'etre re-importee ?",
        "en": "Delete this recipe and prevent it from being re-imported?",
    },
    "admin.confirm_unblacklist": {
        "fr": "Retirer ce fichier de la blacklist ? Il sera re-importe au prochain scan.",
        "en": "Remove this file from the blacklist? It will be re-imported on next scan.",
    },
    "admin.confirm_retry": {
        "fr": "Retirer ce fichier de la liste d'erreurs ? Il sera re-essayee au prochain scan.",
        "en": "Remove this file from the error list? It will be retried on next scan.",
    },
    "admin.confirm_select_first": {
        "fr": "Selectionnez d'abord des recettes.",
        "en": "Please select recipes first.",
    },
    "admin.confirm_pick_tag": {
        "fr": "Choisissez au moins une etiquette.",
        "en": "Please pick at least one tag.",
    },
    "admin.confirm_apply_category": {
        "fr": "Appliquer cette categorie a {n} recette(s) ?",
        "en": "Apply this category to {n} recipe(s)?",
    },
    "admin.confirm_bulk_tags": {
        "fr": "{action} {n} etiquette(s) pour {count} recette(s) ?",
        "en": "{action} {n} tag(s) for {count} recipe(s)?",
    },
    "admin.bulk_add_action": {"fr": "Ajouter", "en": "Add"},
    "admin.bulk_remove_action": {"fr": "Retirer", "en": "Remove"},
    "admin.alert_save_error": {
        "fr": "Erreur lors de la sauvegarde.",
        "en": "Error while saving.",
    },
    "admin.alert_update_error": {
        "fr": "Erreur lors de la mise a jour.",
        "en": "Error while updating.",
    },
    # --- Administration : édition recette ---
    "admin.edit_title": {"fr": "Modifier la recette", "en": "Edit recipe"},
    "admin.field_title": {"fr": "Titre", "en": "Title"},
    "admin.field_servings": {"fr": "Portions", "en": "Servings"},
    "admin.field_category": {"fr": "Catégorie", "en": "Category"},
    "admin.field_source_url": {"fr": "URL source", "en": "Source URL"},
    "admin.field_description": {"fr": "Description", "en": "Description"},
    "admin.tags_legend": {"fr": "Étiquettes", "en": "Tags"},
    "admin.ingredients_label": {"fr": "Ingrédients", "en": "Ingredients"},
    "admin.ing_qty_min": {"fr": "Qté min", "en": "Min qty"},
    "admin.ing_qty_max": {"fr": "Qté max", "en": "Max qty"},
    "admin.ing_unit": {"fr": "Unité", "en": "Unit"},
    "admin.ing_food": {"fr": "Ingrédient", "en": "Ingredient"},
    "admin.ing_remove": {"fr": "Retirer", "en": "Remove"},
    "admin.ing_add": {"fr": "+ Ajouter un ingrédient", "en": "+ Add ingredient"},
    "admin.field_instructions": {"fr": "Instructions", "en": "Instructions"},
    "admin.manual_warning": {
        "fr": (
            "En sauvegardant, cette recette sera marquée comme modifiée manuellement : "
            "les futures mises à jour du fichier Dropbox seront ignorées et signalées "
            "dans les fichiers en erreur."
        ),
        "en": (
            "When saving, this recipe will be marked as manually edited: "
            "future Dropbox file updates will be ignored and flagged in the error files."
        ),
    },
    "admin.save": {"fr": "Sauvegarder", "en": "Save"},
    "admin.cancel": {"fr": "Annuler", "en": "Cancel"},
    # --- Administration : configuration Dropbox ---
    "config.connections_title": {
        "fr": "Connexions Dropbox",
        "en": "Dropbox connections",
    },
    "config.env_note_active": {
        "fr": (
            "Le compte configuré via les variables d'environnement (.env) reste "
            "actif en complément des connexions ajoutées ci-dessous. Compte par "
            "défaut (.env) :"
        ),
        "en": (
            "The account configured via environment variables (.env) remains "
            "active alongside the connections added below. Default account (.env):"
        ),
    },
    "config.env_active_badge": {"fr": "actif", "en": "active"},
    "config.env_note_none": {
        "fr": (
            "Aucun compte par défaut n'est configuré dans le .env. Ajoutez une "
            "connexion Dropbox ci-dessous pour démarrer la synchronisation."
        ),
        "en": (
            "No default account is configured in .env. Add a Dropbox connection "
            "below to start synchronization."
        ),
    },
    "config.col_name": {"fr": "Nom", "en": "Name"},
    "config.col_sync": {"fr": "Synchronisation", "en": "Sync"},
    "config.col_recipes": {"fr": "Recettes", "en": "Recipes"},
    "config.col_folder": {"fr": "Dossier", "en": "Folder"},
    "config.col_filter": {"fr": "Filtre", "en": "Filter"},
    "config.status_active": {"fr": "Active", "en": "Active"},
    "config.status_paused": {"fr": "En pause", "en": "Paused"},
    "config.status_visible": {"fr": "Visibles", "en": "Visible"},
    "config.status_hidden": {"fr": "Masquées", "en": "Hidden"},
    "config.default_label": {"fr": "Défaut (.env)", "en": "Default (.env)"},
    "config.action_stop": {"fr": "Arreter", "en": "Stop"},
    "config.action_start": {"fr": "Demarrer", "en": "Start"},
    "config.action_hide": {"fr": "Masquer les recettes", "en": "Hide recipes"},
    "config.action_show": {"fr": "Afficher les recettes", "en": "Show recipes"},
    "config.action_test": {"fr": "Tester", "en": "Test"},
    "config.action_delete": {"fr": "Supprimer", "en": "Delete"},
    "config.hint_toggle": {
        "fr": (
            "« Arreter » suspend la synchronisation du compte au prochain scan ; "
            "les recettes restent accessibles. Masquer les recettes les retire de "
            "la page principale sans les supprimer."
        ),
        "en": (
            '"Stop" suspends the account\'s synchronization on the next scan; '
            "recipes remain accessible. Hiding recipes removes them from the "
            "main page without deleting them."
        ),
    },
    "config.add_title": {"fr": "Ajouter une connexion", "en": "Add a connection"},
    "config.connect_dropbox": {
        "fr": "Connecter un compte Dropbox",
        "en": "Connect a Dropbox account",
    },
    "config.connect_hint": {
        "fr": (
            "Vous serez redirige vers Dropbox pour autoriser l'application. Le "
            "refresh token sera recupere automatiquement."
        ),
        "en": (
            "You will be redirected to Dropbox to authorize the application. The "
            "refresh token will be retrieved automatically."
        ),
    },
    "config.field_name": {"fr": "Nom de la connexion", "en": "Connection name"},
    "config.name_placeholder": {
        "fr": "Ex : Compte famille",
        "en": "e.g. Family account",
    },
    "config.field_refresh_token": {"fr": "Refresh token", "en": "Refresh token"},
    "config.refresh_placeholder": {
        "fr": "Collez un refresh token ou utilisez le bouton ci-dessus",
        "en": "Paste a refresh token or use the button above",
    },
    "config.field_folder": {"fr": "Dossier a scanner", "en": "Folder to scan"},
    "config.folder_placeholder": {
        "fr": "/Recettes (vide = racine)",
        "en": "/Recipes (empty = root)",
    },
    "config.field_file_filter": {
        "fr": "Filtre de fichiers",
        "en": "File filter",
    },
    "config.file_filter_placeholder": {
        "fr": "Ex : BOEUF* ou *.odt",
        "en": "e.g. BEEF* or *.odt",
    },
    "config.dropbox_app_hint": {
        "fr": (
            "L'application Dropbox utilisee est celle configuree dans le .env "
            "(DROPBOX_APP_KEY / DROPBOX_APP_SECRET), partagee par toutes les "
            "connexions."
        ),
        "en": (
            "The Dropbox app used is the one configured in .env "
            "(DROPBOX_APP_KEY / DROPBOX_APP_SECRET), shared by all connections."
        ),
    },
    "config.action_add": {"fr": "Ajouter la connexion", "en": "Add connection"},
    "config.llm_title": {
        "fr": "Modèle d'analyse (LLM)",
        "en": "Analysis model (LLM)",
    },
    "config.llm_field": {
        "fr": "Modèle (override global)",
        "en": "Model (global override)",
    },
    "config.llm_placeholder": {"fr": "{default} (défaut .env)", "en": "{default} (.env default)"},
    "config.llm_apply": {"fr": "Appliquer", "en": "Apply"},
    "config.llm_reset": {"fr": "Retour au défaut", "en": "Reset to default"},
    "config.llm_hint": {
        "fr": (
            "Utilisé par l'analyse LLM des recettes importées. Laisser vide pour "
            "utiliser le modèle configuré dans le .env ({default}). S'applique au "
            "prochain scan."
        ),
        "en": (
            "Used by the LLM analysis of imported recipes. Leave empty to use the "
            "model configured in .env ({default}). Applies on the next scan."
        ),
    },
    # --- Flash messages / erreurs ---
    "flash.dropbox_name_required": {
        "fr": "Le nom et le refresh token sont obligatoires.",
        "en": "Name and refresh token are required.",
    },
    "flash.dropbox_name_taken": {
        "fr": "Une connexion nommee '{name}' existe deja.",
        "en": "A connection named '{name}' already exists.",
    },
    "flash.dropbox_added": {
        "fr": "Connexion '{name}' ajoutee. Elle sera utilisee au prochain scan.",
        "en": "Connection '{name}' added. It will be used on the next scan.",
    },
    "flash.dropbox_oauth_state": {
        "fr": ("Compte Dropbox autorise. Choisissez un nom pour finaliser la connexion."),
        "en": "Dropbox account authorized. Pick a name to finalize the connection.",
    },
    "flash.dropbox_oauth_denied": {
        "fr": "Autorisation Dropbox refusee : {error}",
        "en": "Dropbox authorization denied: {error}",
    },
    "flash.dropbox_invalid_response": {
        "fr": "Reponse Dropbox invalide ({detail}).",
        "en": "Invalid Dropbox response ({detail}).",
    },
    "flash.dropbox_exchange_failed": {
        "fr": "Echange du code echoue : {error}",
        "en": "Code exchange failed: {error}",
    },
    "flash.dropbox_connection_missing": {
        "fr": "Connexion introuvable.",
        "en": "Connection not found.",
    },
    "flash.dropbox_cannot_delete_default": {
        "fr": "Le compte par defaut (.env) ne peut pas etre supprime ici.",
        "en": "The default account (.env) cannot be deleted here.",
    },
    "flash.dropbox_test_failed": {
        "fr": "Echec de connexion pour '{name}' : {error}",
        "en": "Connection failed for '{name}': {error}",
    },
    "flash.dropbox_test_ok": {
        "fr": "Connexion '{name}' validee : {account}",
        "en": "Connection '{name}' validated: {account}",
    },
    "flash.dropbox_deleted": {
        "fr": "Connexion supprimee, ainsi que ses recettes et fichiers associes.",
        "en": "Connection deleted, along with its recipes and associated files.",
    },
    "flash.dropbox_sync_state": {
        "fr": "Synchronisation '{name}' {state}.",
        "en": "Synchronization '{name}' {state}.",
    },
    "flash.dropbox_state_active": {"fr": "demarree", "en": "started"},
    "flash.dropbox_state_paused": {"fr": "arretee", "en": "stopped"},
    "flash.dropbox_state_visible": {"fr": "visible", "en": "visible"},
    "flash.dropbox_state_hidden": {"fr": "masquee", "en": "hidden"},
    "flash.llm_set": {
        "fr": "Modele LLM defini : '{model}'.",
        "en": "LLM model set: '{model}'.",
    },
    "flash.llm_reset": {
        "fr": "Override retire : retour au modele du .env.",
        "en": "Override removed: falling back to .env model.",
    },
    # --- Switcher de langue ---
    "lang.switch": {"fr": "Langue", "en": "Language"},
    "lang.switch_to": {"fr": "Passer en {code}", "en": "Switch to {code}"},
    "lang.fr": {"fr": "Français", "en": "French"},
    "lang.en": {"fr": "Anglais", "en": "English"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def available_languages() -> tuple[str, ...]:
    """Langues supportées (copie pour ne pas exposer la constante mutable)."""
    return SUPPORTED_LANGUAGES


def is_supported(lang: str) -> bool:
    return lang in SUPPORTED_LANGUAGES


def _normalize(raw: str | None) -> str | None:
    if raw is None:
        return None
    candidate = raw.strip().lower().replace("_", "-")
    if not candidate:
        return None
    primary = candidate.split("-", 1)[0]
    return primary if is_supported(primary) else None


def resolve_language(
    cookie_value: str | None,
    accept_language: str | None,
) -> str:
    """Résout la langue effective.

    Priorité : cookie explicite > en-tête Accept-Language > langue par défaut.
    Toute valeur invalide retombe sur `DEFAULT_LANGUAGE`.
    """
    forced = _normalize(cookie_value)
    if forced is not None:
        return forced
    if accept_language:
        for raw in accept_language.split(","):
            tag = raw.split(";", 1)[0]
            lang = _normalize(tag)
            if lang is not None:
                return lang
    return DEFAULT_LANGUAGE


def _resolve_msg(key: str, lang: str) -> str:
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key
    return entry.get(lang) or entry.get(DEFAULT_LANGUAGE) or key


def gettext(key: str, lang: str, **values: object) -> str:
    """Traduit une clé. Les `values` sont injectés via str.format_map si
    la chaîne contient des placeholders ; les placeholders absents sont
    laissés intacts."""
    template = _resolve_msg(key, lang)
    if not values:
        return template
    try:
        return template.format_map(_SafeDict(values))
    except (KeyError, IndexError):
        return template


_ = gettext  # alias court style GNU gettext


def ngettext(
    key_singular: str,
    key_plural: str,
    n: int,
    lang: str,
    **values: object,
) -> str:
    """Sélectionne singulier/pluriel selon la langue et le nombre.

    `key_singular` et `key_plural` sont des clés distinctes du catalogue.
    """
    rule = PLURAL_RULES.get(lang, PLURAL_RULES[DEFAULT_LANGUAGE])
    use_singular = _eval_plural_rule(rule, n)
    chosen = key_singular if use_singular else key_plural
    values = {**values, "n": n}
    return gettext(chosen, lang, **values)


def _eval_plural_rule(rule: str, n: int) -> bool:
    """Évalue une règle PLURAL simplifiée du type
    'nplurals=2; plural=(<expr>);'. Seules les comparaisons numériques simples
    sur `n` sont supportées (suffisant pour FR/EN)."""
    expr = rule.split(";", 2)
    if len(expr) < 2:
        return n == 1
    body = expr[1].strip()
    if not body.startswith("plural=(") or not body.endswith(");"):
        return n == 1
    body = body[len("plural=(") : -len(");")].strip()
    try:
        return bool(eval(body, {"n": n}, {}))  # noqa: S307 — expression bornée
    except Exception:
        return n == 1


class _SafeDict(dict[str, object]):
    """dict qui laisse intacts les placeholders absents lors d'un format_map."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
