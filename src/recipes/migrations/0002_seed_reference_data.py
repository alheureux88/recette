"""0002 - V1 reference data (taxonomy shipped with the app).

Tag families/tags (with hierarchy), categories and grocery departments.
Applied once by yoyo: any future taxonomy change gets its own migration
(targeted UPDATEs) instead of a blind `INSERT OR IGNORE` on every boot.

Rollbacks are scoped to seed rows (`WHERE name IN (...)`) so they never
delete rows created through usage (LLM tags, user-entered categories, ...).

Migration files must stay pure ASCII: yoyo reads them with the locale
default encoding, which breaks non-ASCII bytes on some platforms.
Non-ASCII display names and emojis below use Unicode escapes (identical
runtime values, ASCII-safe source).
"""

import sqlite3

from yoyo import step

__depends__ = {"0001_initial_schema"}

# (name technique, display_name_fr, display_name_en, sort_order)
TAG_FAMILIES = [
    ("origin", "Origine", "Origin", 1),
    ("diet", "R\u00e9gime alimentaire", "Diet", 2),
    ("protein", "Prot\u00e9ine principale", "Main protein", 3),
    ("cooking_method", "M\u00e9thode de cuisson", "Cooking method", 4),
]

# (name technique, display_name_fr, display_name_en, parent_name)
TAGS: dict[str, list[tuple[str, str, str, str | None]]] = {
    "origin": [
        ("asiatique", "Asiatique", "Asian", None),
        ("japonais", "Japonais", "Japanese", "asiatique"),
        ("chinois", "Chinois", "Chinese", "asiatique"),
        ("coreen", "Cor\u00e9en", "Korean", "asiatique"),
        ("thailandais", "Tha\u00eflandais", "Thai", "asiatique"),
        ("vietnamien", "Vietnamien", "Vietnamese", "asiatique"),
        ("indien", "Indien", "Indian", "asiatique"),
        ("europeen", "Europ\u00e9en", "European", None),
        ("francais", "Fran\u00e7ais", "French", "europeen"),
        ("italien", "Italien", "Italian", "europeen"),
        ("grec", "Grec", "Greek", "europeen"),
        ("espagnol", "Espagnol", "Spanish", "europeen"),
        ("allemand", "Allemand", "German", "europeen"),
        ("americain", "Am\u00e9ricain", "American", None),
        ("canadien", "Canadien", "Canadian", "americain"),
        ("quebecois", "Qu\u00e9b\u00e9cois", "Qu\u00e9b\u00e9cois", "canadien"),
        ("mexicain", "Mexicain", "Mexican", "americain"),
        ("moyen-oriental", "Moyen-Oriental", "Middle Eastern", None),
        ("libanais", "Libanais", "Lebanese", "moyen-oriental"),
        ("israelien", "Isra\u00e9lien", "Israeli", "moyen-oriental"),
        ("africain", "Africain", "African", None),
        ("marocain", "Marocain", "Moroccan", "africain"),
        ("ethiopien", "\u00c9thiopien", "Ethiopian", "africain"),
    ],
    "diet": [
        ("vegetalien", "V\u00e9g\u00e9talien", "Vegan", None),
        ("vegetarien", "V\u00e9g\u00e9tarien", "Vegetarian", None),
        ("pescetarien", "Pesc\u00e9tarien", "Pescatarian", None),
        ("sans-gluten", "Sans gluten", "Gluten-free", None),
        ("sans-produits-laitiers", "Sans produits laitiers", "Dairy-free", None),
        ("cetogene", "C\u00e9tog\u00e8ne", "Keto", None),
        ("faible-en-glucides", "Faible en glucides", "Low-carb", None),
        ("paleo", "Pal\u00e9o", "Paleo", None),
    ],
    "protein": [
        ("poulet", "Poulet", "Chicken", None),
        ("boeuf", "B\u0153uf", "Beef", None),
        ("porc", "Porc", "Pork", None),
        ("agneau", "Agneau", "Lamb", None),
        ("veau", "Veau", "Veal", None),
        ("poisson", "Poisson", "Fish", None),
        ("fruits-de-mer", "Fruits de mer", "Seafood", None),
        ("tofu", "Tofu", "Tofu", None),
        ("tempeh", "Tempeh", "Tempeh", None),
        ("lentilles", "Lentilles", "Lentils", None),
        ("oeufs", "\u0152ufs", "Eggs", None),
        ("canard", "Canard", "Duck", None),
    ],
    "cooking_method": [
        ("braise", "Brais\u00e9", "Braised", None),
        ("roti", "R\u00f4ti", "Roasted", None),
        ("saute", "Saut\u00e9", "Saut\u00e9ed", None),
        ("wok", "Wok", "Stir-fried", None),
        ("fume", "Fum\u00e9", "Smoked", None),
        ("barbecue", "Barbecue", "Barbecue", None),
        ("grille", "Grill\u00e9", "Grilled", None),
        ("frit", "Frit", "Fried", None),
        ("mijote", "Mijot\u00e9", "Slow-cooked", None),
        ("sans-cuisson", "Sans cuisson", "No-cook", None),
        ("poche", "Poch\u00e9", "Poached", None),
        ("vapeur", "Vapeur", "Steamed", None),
    ],
}

CATEGORIES = [
    ("entree", "Entr\u00e9e", "Starter", 1),
    ("plat-principal", "Plat principal", "Main course", 2),
    ("salade", "Salade", "Salad", 3),
    ("soupe", "Soupe", "Soup", 4),
    ("sauce", "Sauce", "Sauce", 5),
    ("dessert", "Dessert", "Dessert", 6),
    ("accompagnement", "Accompagnement", "Side dish", 7),
    ("collation", "Collation", "Snack", 8),
    ("aperitif", "Ap\u00e9ritif", "Appetizer", 9),
]

SHOPPING_DEPARTMENTS = [
    ("fruits-legumes", "Fruits et l\u00e9gumes", "Fruits & Vegetables", 1, "\U0001f96c"),
    ("boucherie", "Boucherie", "Butcher", 2, "\U0001f969"),
    ("poissonnerie", "Poissonnerie", "Fish counter", 3, "\U0001f41f"),
    ("charcuterie", "Charcuterie", "Deli / Charcuterie", 4, "\U0001f953"),
    ("boulangerie", "Boulangerie", "Bakery", 5, "\U0001f956"),
    ("produits-laitiers", "Produits laitiers", "Dairy", 6, "\U0001f9c0"),
    ("surgel\u00e9s", "Surgel\u00e9s", "Frozen goods", 7, "\U0001f9ca"),
    ("epicerie", "\u00c9picerie", "Grocery / Pantry", 8, "\U0001f96b"),
    ("pret-a-manger", "Pr\u00eat-\u00e0-manger", "Ready to eat", 9, "\U0001f961"),
    ("entretien", "Entretien / M\u00e9nage", "Household / Cleaning", 10, "\U0001f9f9"),
    ("autre", "Autre / Ind\u00e9termin\u00e9", "Other / Unknown", 99, "\u2753"),
]


def apply_families(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO tag_families"
        " (name, display_name_fr, display_name_en, sort_order)"
        " VALUES (?, ?, ?, ?)",
        TAG_FAMILIES,
    )


def rollback_families(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM tag_families WHERE name IN ('origin', 'diet', 'protein', 'cooking_method')",
    )


def apply_tags(conn: sqlite3.Connection) -> None:
    for family_name, tags in TAGS.items():
        conn.executemany(
            "INSERT OR IGNORE INTO tags (family_id, name, display_name_fr, display_name_en)"
            " SELECT id, ?, ?, ? FROM tag_families WHERE name = ?",
            [(name, fr, en, family_name) for name, fr, en, _parent in tags],
        )
    for family_name, tags in TAGS.items():
        for name, _fr, _en, parent_name in tags:
            if parent_name:
                conn.execute(
                    "UPDATE tags SET parent_id = ("
                    " SELECT p.id FROM tags p"
                    " JOIN tag_families f ON p.family_id = f.id"
                    " WHERE f.name = ? AND p.name = ?)"
                    " WHERE family_id = (SELECT id FROM tag_families WHERE name = ?)"
                    " AND name = ?",
                    (family_name, parent_name, family_name, name),
                )


def rollback_tags(conn: sqlite3.Connection) -> None:
    for family_name, tags in TAGS.items():
        conn.executemany(
            "DELETE FROM tags"
            " WHERE family_id = (SELECT id FROM tag_families WHERE name = ?)"
            " AND name = ?",
            [(family_name, name) for name, _fr, _en, _parent in tags],
        )


def apply_categories(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO categories"
        " (name, display_name_fr, display_name_en, sort_order)"
        " VALUES (?, ?, ?, ?)",
        CATEGORIES,
    )


def rollback_categories(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM categories WHERE name IN"
        " ('entree', 'plat-principal', 'salade', 'soupe', 'sauce',"
        " 'dessert', 'accompagnement', 'collation', 'aperitif')",
    )


def apply_departments(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO shopping_departments"
        " (name, display_name_fr, display_name_en, sort_order, emoji)"
        " VALUES (?, ?, ?, ?, ?)",
        SHOPPING_DEPARTMENTS,
    )


def rollback_departments(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM shopping_departments WHERE name IN"
        " ('fruits-legumes', 'boucherie', 'poissonnerie', 'charcuterie',"
        " 'boulangerie', 'produits-laitiers', 'surgel\u00e9s', 'epicerie',"
        " 'pret-a-manger', 'entretien', 'autre')",
    )


steps = [
    step(apply_families, rollback_families),
    step(apply_tags, rollback_tags),
    step(apply_categories, rollback_categories),
    step(apply_departments, rollback_departments),
]
