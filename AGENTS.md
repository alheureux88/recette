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

## TODO / Améliorations futures

### Base de données
- [ ] Optimiser les connexions SQLite: utiliser une connexion par requête HTTP avec FastAPI `Depends(get_db)` au lieu d'ouvrir/fermer une connexion par opération DB. Réduirait l'overhead et éviterait les problèmes de "database locked".
