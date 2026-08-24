"""
tagger.py — Send raw recipe text to an LLM (OpenAI or Anthropic) and get back
structured data: title, description, ingredients, instructions, tags, and source_url.
"""

import json
import os
from typing import Any

PROVIDER = os.environ.get("LLM_PROVIDER", "openai").lower()
MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
API_KEY = os.environ["LLM_API_KEY"]

if PROVIDER == "anthropic":
    from anthropic import Anthropic

    client: Any = Anthropic(api_key=API_KEY)
else:
    from openai import OpenAI

    base_url = os.environ.get("LLM_BASE_URL")
    client = OpenAI(api_key=API_KEY, base_url=base_url) if base_url else OpenAI(api_key=API_KEY)

SYSTEM_PROMPT = """
Tu es un parseur de recettes. À partir du texte brut extrait d'un fichier de
recette, retourne un objet JSON avec exactement ces champs :

{
  "title": "Nom de la recette",
  "description": "Résumé en une ou deux phrases",
  "ingredients": ["ingrédient 1", "ingrédient 2", ...],
  "instructions": "Instructions complètes en une seule chaîne, en préservant les étapes",
  "tags": ["étiquette1", "étiquette2", ...],
  "source_url": "https://..." ou null
}

Pour source_url : si le texte contient une URL pointant vers un site de recettes
(p. ex. allrecipes.com, seriouseats.com, food52.com, ou tout blogue de cuisine),
extrais-la. S'il y a plusieurs URLs, choisis celle qui correspond le plus
probablement à la page de recette originale. Si aucune URL n'est présente,
retourne null.

Directives pour les étiquettes — inclus toutes celles qui s'appliquent :
- Cuisine : italien, français, mexicain, asiatique, américain, moyen-oriental, indien, grec, etc.
- Régime : végétarien, végétalien, sans gluten, sans produits laitiers, cétogène, faible en glucides, paléo
- Type de repas : déjeuner, dîner, souper, dessert, collation, apéritif, soupe, salade, accompagnement
- Ingrédient principal : poulet, bœuf, porc, poisson, fruits de mer, pâtes, riz, œufs, tofu, lentilles, etc.
- Méthode de cuisson : au four, grillé, frit, mijoteuse, autocuiseur, sans cuisson, une seule casserole
- Temps : rapide (moins de 30 min), semaine (30-60 min), fin de semaine (plus d'une heure)

Retourne UNIQUEMENT du JSON valide. Pas de markdown, pas d'explication, pas de blocs de code.
""".strip()


def tag_recipe(raw_text: str) -> dict[str, object]:
    """
    Call the LLM and return a structured recipe dict.
    Raises ValueError if the response can't be parsed.
    """
    if PROVIDER == "anthropic":
        response = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": raw_text[:6000]},
            ],
        )
        raw = response.content[0].text.strip()
    else:
        response = client.chat.completions.create(
            model=MODEL,
            max_tokens=1000,
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": raw_text[:6000]},
            ],
        )
        raw = (response.choices[0].message.content or "").strip()

    # Strip accidental markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON: {e}\n\nRaw response:\n{raw}") from None

    # Normalize — ensure required fields exist
    data.setdefault("title", "Recette sans titre")
    data.setdefault("description", "")
    data.setdefault("ingredients", [])
    data.setdefault("instructions", "")
    data.setdefault("tags", [])
    data.setdefault("source_url", None)

    # Sanity-check source_url — discard if it doesn't look like a URL
    url = data["source_url"]
    if url and not (isinstance(url, str) and url.startswith("http")):
        data["source_url"] = None

    # Lowercase and deduplicate tags
    data["tags"] = sorted({t.lower().strip() for t in data["tags"] if t})

    return dict(data)
