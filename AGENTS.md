# Agent Instructions

## Commit Policy

**NE PAS faire de commit automatiquement.**

Les commits ne doivent être faits que sur demande explicite de l'utilisateur.

## Convention pour les pages d'administration

Toutes les pages d'administration doivent suivre la même structure pour assurer la cohérence visuelle.

### Structure HTML (template)

```html
{% extends "base.html" %}
{% block title %}{{ _('admin.page_title') }} — Recettes Merizzi{% endblock %}
{% block main_class %}admin-main{% endblock %}
{% block content %}

<h1 class="page-title">{{ _('admin.page_title') }}</h1>

<!-- Contenu de la page ici -->

{% endblock %}
```

### Éléments requis

- **`{% block main_class %}admin-main{% endblock %}`** — Applique `max-width: 1700px` pour la largeur
- **`<h1 class="page-title">`** — Titre stylé avec la couleur accent

### Tables

Utiliser la classe `admin-table` pour tous les tableaux:

```html
<table class="admin-table">
    <thead>...</thead>
    <tbody>...</tbody>
</table>
```

Les liens dans les cellules (`<td><a>`) sont automatiquement stylés.

### Badges (statuts, étiquettes)

Utiliser les classes `admin-badge` avec un modificateur:

```html
<span class="admin-badge admin-badge-user">Texte</span>
<span class="admin-badge admin-badge-done">Complétée</span>
<span class="admin-badge admin-badge-active">Active</span>
<span class="admin-badge admin-badge-anon">Anonyme</span>
```

### Boutons d'action

Pour les boutons dans la colonne actions:

```html
<td class="admin-actions">
    <a href="..." class="admin-btn admin-btn-view">Voir</a>
    <button class="admin-btn admin-btn-delete">Supprimer</button>
</td>
```

Ou utiliser les boutons existants: `.btn-edit`, `.btn-blacklist`, `.btn-restore`, `.btn-retry`.

### Navigation

Les liens du menu admin dans `nav.html` et `base.html` utilisent le pattern `nav.admin_*`:

```python
"nav.admin_recipes": {"fr": "Administration Recettes", "en": "Recipe administration"},
"nav.admin_system": {"fr": "Administration Système", "en": "System administration"},
"nav.admin_shopping": {"fr": "Administration Épicerie", "en": "Shopping administration"},
```

Toujours utiliser "Administration" (pas "Admin") dans les labels du menu.

### Traductions (i18n)

Clés de traduction sous le préfixe `admin.*`:

```python
"admin.page_title": {"fr": "Titre de la page", "en": "Page title"},
"admin.col_name": {"fr": "Nom", "en": "Name"},
"admin.action_view": {"fr": "Voir", "en": "View"},
```

## Règles de qualité du code

### Tests

Tout changement de logique doit être accompagné de tests unitaires ou d'intégration couvrant le comportement modifié. Les tests existants doivent continuer de passer.

### Responsabilité unique

Chaque fichier doit avoir une seule responsabilité. Si un fichier gère plusieurs concepts distincts, il doit être scindé.

### Classes communes

Les classes, utilitaires et fonctions partagés doivent être placés dans un dossier commun (ex: `utils/`, `common/`, `shared/`) plutôt que dupliqués ou éparpillés.

### Limite de 500 lignes

Tout fichier dépassant 500 lignes doit être examiné pour refactorisation. Identifier les responsabilités multiples et extraire en modules distincts.

### Code mort

Pas de code mort: toute fonction, classe ou constante sans appelant en production doit être supprimée. `vulture --config pyproject.toml` (hook pre-commit + `nox -s vulture`) doit passer. Les exceptions légitimes (helpers utilisés uniquement par les tests, handlers FastAPI) vont dans `vulture_whitelist.py` avec un commentaire justificatif.

### Organisation par feature

Le code doit être organisé par feature/domaine plutôt que par type technique. Chaque feature a son propre dossier contenant ses controllers, services, etc.

Structure attendue:
```
src/recipes/
├── features/
│   ├── admin/
│   │   ├── controllers.py   # Endpoints HTTP
│   │   ├── services.py      # Logique métier
│   │   └── ...
│   ├── recipes/
│   │   ├── controllers.py
│   │   ├── services.py
│   │   └── ...
│   ├── shopping/
│   │   ├── controllers.py
│   │   ├── services.py
│   │   └── ...
│   └── auth/
│       ├── controllers.py
│       ├── services.py
│       └── ...
├── shared/                  # Code partagé entre features
│   ├── utils.py
│   └── ...
└── ...
```

Éviter la structure par type technique (ex: `routes/admin.py`, `routes/recipes.py`).

### Typage strict

Profiter de `mypy --strict` déjà configuré:
- Typer toutes les fonctions publiques
- Utiliser `TypeAlias` pour les types complexes
- Éviter `Any` sauf justification explicite

### Gestion des erreurs

- Utiliser `HTTPException` avec des codes HTTP appropriés
- Centraliser les messages d'erreur dans `i18n.py`
- Logger les erreurs avec `logging.exception()`

### Sécurité

- Valider toutes les entrées utilisateur (Pydantic déjà utilisé)
- Échapper les sorties HTML (Jinja2 le fait par défaut)
- Utiliser `secrets.token_urlsafe()` pour les tokens

### Base de données

- Utiliser `get_db` via `Depends()` pour toutes les requêtes
- Paginer les listes (>20 éléments)
- Éviter les requêtes N+1

### Documentation

- Docstrings pour les fonctions publiques (style Google)
- Commentaires uniquement pour le "pourquoi", pas le "quoi"

### Constants et configuration

- Pas de magic numbers/strings
- Centraliser dans `config.py` ou `constants.py`

## TODO / Améliorations futures

### Base de données
- [x] Optimiser les connexions SQLite: utiliser une connexion par requête HTTP avec FastAPI `Depends(get_db)` au lieu d'ouvrir/fermer une connexion par opération DB. Réduirait l'overhead et éviterait les problèmes de "database locked".
