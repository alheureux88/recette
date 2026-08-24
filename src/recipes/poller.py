"""
poller.py — Poll a Dropbox folder for new or changed recipe files,
parse them, tag them via LLM, and store in SQLite.
"""

import fnmatch
import hashlib
import logging
import os
from pathlib import Path

import dropbox
from dropbox.exceptions import ApiError
from dropbox.sharing import RequestedVisibility, SharedLinkSettings

from recipes.db import get_processed_hash, init_db, mark_processed, sync_recipe_tags, upsert_recipe
from recipes.parsers import extract_text
from recipes.tagger import tag_recipe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

DROPBOX_TOKEN = os.environ["DROPBOX_TOKEN"]
DROPBOX_FOLDER = os.environ.get("DROPBOX_FOLDER", "")
DROPBOX_FILE_FILTER = os.environ.get("DROPBOX_FILE_FILTER", "")
SUPPORTED_EXTS = {".txt", ".docx", ".doc", ".odt", ".pdf"}


def matches_filter(filename: str) -> bool:
    """Check if filename matches the filter pattern (supports * wildcard).

    Empty filter matches everything.
    """
    if not DROPBOX_FILE_FILTER:
        return True
    return fnmatch.fnmatch(filename.lower(), DROPBOX_FILE_FILTER.lower())


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def list_recipe_files(dbx: dropbox.Dropbox) -> list[dropbox.files.FileMetadata]:
    """Return all supported files in the Dropbox folder (non-recursive).

    Applies file filter from DROPBOX_FILE_FILTER env var if set.
    """
    result = dbx.files_list_folder(DROPBOX_FOLDER)
    entries = result.entries[:]
    while result.has_more:
        result = dbx.files_list_folder_continue(result.cursor)
        entries.extend(result.entries)

    files = [
        e
        for e in entries
        if isinstance(e, dropbox.files.FileMetadata)
        and Path(e.name).suffix.lower() in SUPPORTED_EXTS
        and matches_filter(e.name)
    ]

    if DROPBOX_FILE_FILTER:
        log.info(f"File filter active: '{DROPBOX_FILE_FILTER}'")

    return files


def download_file(dbx: dropbox.Dropbox, path: str) -> bytes:
    _, response = dbx.files_download(path)
    return bytes(response.content)


def get_or_create_shared_link(dbx: dropbox.Dropbox, path: str) -> str | None:
    """
    Return a public shared link for the Dropbox file.
    Reuses an existing link if one already exists, creates one otherwise.
    """
    try:
        # Check for an existing shared link first
        existing = dbx.sharing_list_shared_links(path=path, direct_only=True)
        if existing.links:
            return str(existing.links[0].url)

        # Create a new public shared link
        settings = SharedLinkSettings(requested_visibility=RequestedVisibility.public)
        result = dbx.sharing_create_shared_link_with_settings(path, settings)
        return str(result.url)
    except ApiError as e:
        log.warning(f"  Could not create shared link for {path}: {e}")
        return None


def extract_title_from_filename(filename: str) -> str:
    """Extract a default title from the filename.

    Expected format: "CATEGORY Title of recipe.ext" or "Title of recipe.ext"
    """
    name = Path(filename).stem
    parts = name.split(maxsplit=1)
    if len(parts) > 1 and parts[0].isupper():
        return parts[1]
    return name


def process_file(dbx: dropbox.Dropbox, entry: dropbox.files.FileMetadata) -> None:
    path = entry.path_lower
    log.info(f"Downloading: {path}")
    content = download_file(dbx, path)
    content_hash = file_hash(content)

    # Skip if we've already processed this exact version
    existing_hash = get_processed_hash(path)
    if existing_hash == content_hash:
        log.info(f"  Skipping (unchanged): {path}")
        return

    log.info(f"  Parsing: {entry.name}")
    try:
        raw_text = extract_text(entry.name, content)
    except Exception as e:
        log.error(f"  Parse failed for {entry.name}: {e}")
        return

    if not raw_text.strip():
        log.warning(f"  Empty text extracted from {entry.name}, skipping.")
        return

    default_title = extract_title_from_filename(entry.name)
    log.info(f"  Tagging with LLM... (default title: '{default_title}')")
    try:
        structured = tag_recipe(raw_text, default_title=default_title)
    except Exception as e:
        log.error(f"  Tagging failed for {entry.name}: {e}")
        return

    log.info("  Fetching Dropbox shared link...")
    dropbox_url = get_or_create_shared_link(dbx, path)

    structured["source_file"] = path
    structured["file_hash"] = content_hash
    structured["dropbox_url"] = dropbox_url

    recipe_id = upsert_recipe(structured)
    tags = structured.get("tags", {})
    if isinstance(tags, dict):
        sync_recipe_tags(
            recipe_id, {str(k): [str(t) for t in v] for k, v in tags.items() if isinstance(v, list)}
        )
    mark_processed(path, content_hash)

    log.info(
        f"  Saved recipe #{recipe_id}: '{structured['title']}' "
        f"| category: {structured.get('category')} "
        f"| tags: {structured.get('tags', {})} "
        f"| source_url: {structured.get('source_url')} "
        f"| dropbox_url: {dropbox_url}"
    )


def run() -> None:
    init_db()
    dbx = dropbox.Dropbox(DROPBOX_TOKEN)

    log.info(f"Checking Dropbox folder: '{DROPBOX_FOLDER or '/'}'")
    try:
        files = list_recipe_files(dbx)
    except ApiError as e:
        log.error(f"Dropbox API error: {e}")
        return

    log.info(f"Found {len(files)} recipe file(s).")
    for entry in files:
        try:
            process_file(dbx, entry)
        except Exception as e:
            log.error(f"Unexpected error processing {entry.name}: {e}")

    log.info("Done.")


if __name__ == "__main__":
    run()
