"""
poller.py — Poll a Dropbox folder for new or changed recipe files,
parse them, tag them via LLM, and store in SQLite.
"""

import fnmatch
import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import dropbox
import requests
from dropbox.exceptions import ApiError, AuthError
from dropbox.sharing import RequestedVisibility, SharedLinkSettings

from recipes.db import (
    get_processed_hash,
    init_db,
    is_blacklisted,
    mark_processed,
    record_failed_file,
    remove_failed_file,
    save_recipe_images,
    sync_recipe_tags,
    upsert_recipe,
)
from recipes.parsers import extract_images, extract_text
from recipes.tagger import tag_recipe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

DROPBOX_FOLDER = os.environ.get("DROPBOX_FOLDER", "")
DROPBOX_FILE_FILTER = os.environ.get("DROPBOX_FILE_FILTER", "")
SUPPORTED_EXTS = {".txt", ".docx", ".doc", ".odt", ".pdf"}
IMAGES_DIR = Path(os.environ.get("IMAGES_DIR", "/data/images"))

_dbx_client: dropbox.Dropbox | None = None
_dbx_token_expiry: float = 0
_DBX_LOCK = threading.Lock()
_DB_LOCK = threading.Lock()


def _refresh_dropbox_token() -> tuple[str, float]:
    """Refresh the Dropbox access token using the refresh token flow.

    Returns:
        A tuple of (access_token, expiry_timestamp)
    """
    refresh_token = os.environ.get("DROPBOX_REFRESH_TOKEN")
    app_key = os.environ.get("DROPBOX_APP_KEY")
    app_secret = os.environ.get("DROPBOX_APP_SECRET")

    if not all([refresh_token, app_key, app_secret]):
        raise ValueError(
            "Dropbox token refresh requires DROPBOX_REFRESH_TOKEN, "
            "DROPBOX_APP_KEY, and DROPBOX_APP_SECRET environment variables"
        )

    log.info("Refreshing Dropbox access token...")
    response = requests.post(
        "https://api.dropbox.com/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": app_key,
            "client_secret": app_secret,
        },
        timeout=30,
    )

    if not response.ok:
        error_body = response.text
        log.error(f"Dropbox token refresh failed: {response.status_code}")
        log.error(f"Response: {error_body}")
        raise ValueError(f"Dropbox token refresh failed: {error_body}")

    token_data = response.json()

    access_token = str(token_data["access_token"])
    expires_in = int(token_data.get("expires_in", 14400))
    return access_token, time.time() + expires_in - 60


def _get_dropbox_client() -> dropbox.Dropbox:
    """Get or create a Dropbox client with automatic token refresh."""
    global _dbx_client, _dbx_token_expiry

    if _dbx_client is not None and time.time() < _dbx_token_expiry:
        return _dbx_client

    with _DBX_LOCK:
        if _dbx_client is not None and time.time() < _dbx_token_expiry:
            return _dbx_client

        refresh_token = os.environ.get("DROPBOX_REFRESH_TOKEN")

        if refresh_token:
            access_token, expiry = _refresh_dropbox_token()
            _dbx_client = dropbox.Dropbox(access_token)
            _dbx_token_expiry = expiry
            return _dbx_client

        static_token = os.environ.get("DROPBOX_TOKEN")
        if static_token:
            _dbx_client = dropbox.Dropbox(static_token)
            _dbx_token_expiry = time.time() + 3600
            return _dbx_client

        raise ValueError("No Dropbox credentials configured")


def reset_dropbox_client() -> None:
    """Reset the cached Dropbox client (useful for testing)."""
    global _dbx_client, _dbx_token_expiry
    _dbx_client = None
    _dbx_token_expiry = 0


def _with_retry(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Execute a Dropbox API call with automatic retry on auth errors."""
    global _dbx_client, _dbx_token_expiry

    try:
        return func(*args, **kwargs)
    except AuthError as e:
        if "expired_access_token" in str(e):
            log.warning("Dropbox token expired, refreshing...")
            _dbx_client = None
            _dbx_token_expiry = 0
            dbx = _get_dropbox_client()
            # Retry with new client - need to update the dbx reference in args
            new_args = list(args)
            for i, arg in enumerate(new_args):
                if isinstance(arg, dropbox.Dropbox):
                    new_args[i] = dbx
            return func(*new_args, **kwargs)
        raise


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


def list_unsupported_files(dbx: dropbox.Dropbox) -> list[dropbox.files.FileMetadata]:
    """Return all files with unsupported extensions in the Dropbox folder."""
    result = dbx.files_list_folder(DROPBOX_FOLDER)
    entries = result.entries[:]
    while result.has_more:
        result = dbx.files_list_folder_continue(result.cursor)
        entries.extend(result.entries)

    return [
        e
        for e in entries
        if isinstance(e, dropbox.files.FileMetadata)
        and Path(e.name).suffix.lower() not in SUPPORTED_EXTS
    ]


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


def _save_images(recipe_id: int, filename: str, content: bytes) -> None:
    try:
        images = extract_images(filename, content)
    except Exception as e:
        log.warning(f"  Image extraction failed for {filename}: {e}")
        return

    if not images:
        return

    recipe_dir = IMAGES_DIR / str(recipe_id)
    recipe_dir.mkdir(parents=True, exist_ok=True)

    saved_filenames: list[str] = []
    for img_name, img_bytes in images:
        dest = recipe_dir / img_name
        dest.write_bytes(img_bytes)
        saved_filenames.append(img_name)
        log.info(f"  Saved image: {img_name} ({len(img_bytes)} bytes)")

    save_recipe_images(recipe_id, saved_filenames)
    log.info(f"  Saved {len(saved_filenames)} image(s) for recipe #{recipe_id}")


def process_file(dbx: dropbox.Dropbox, entry: dropbox.files.FileMetadata) -> None:
    path = entry.path_lower

    if is_blacklisted(path):
        log.info(f"  Skipping (blacklisted): {path}")
        return

    log.info(f"Downloading: {path}")
    content = download_file(dbx, path)
    content_hash = file_hash(content)

    existing_hash = get_processed_hash(path)
    if existing_hash == content_hash:
        log.info(f"  Skipping (unchanged): {path}")
        return

    log.info(f"  Parsing: {entry.name}")
    try:
        raw_text = extract_text(entry.name, content)
    except Exception as e:
        log.error(f"  Parse failed for {entry.name}: {e}")
        with _DB_LOCK:
            record_failed_file(path, f"Parse error: {e}")
        return

    if not raw_text.strip():
        log.warning(f"  Empty text extracted from {entry.name}, skipping.")
        with _DB_LOCK:
            record_failed_file(path, "Empty text extracted")
        return

    default_title = extract_title_from_filename(entry.name)
    log.info(f"  Tagging with LLM... (default title: '{default_title}')")
    try:
        structured = tag_recipe(raw_text, default_title=default_title)
    except Exception as e:
        log.error(f"  Tagging failed for {entry.name}: {e}")
        with _DB_LOCK:
            record_failed_file(path, f"Tagging error: {e}")
        return

    log.info("  Fetching Dropbox shared link...")
    dropbox_url = get_or_create_shared_link(dbx, path)

    structured["source_file"] = path
    structured["file_hash"] = content_hash
    structured["dropbox_url"] = dropbox_url
    structured["file_modified_at"] = (
        entry.client_modified.isoformat() if entry.client_modified else None
    )

    with _DB_LOCK:
        recipe_id = upsert_recipe(structured)
        tags = structured.get("tags", {})
        if isinstance(tags, dict):
            sync_recipe_tags(
                recipe_id,
                {str(k): [str(t) for t in v] for k, v in tags.items() if isinstance(v, list)},
            )
        _save_images(recipe_id, entry.name, content)
        mark_processed(path, content_hash)
        remove_failed_file(path)

    log.info(
        f"  Saved recipe #{recipe_id}: '{structured['title']}' "
        f"| category: {structured.get('category')} "
        f"| tags: {structured.get('tags', {})} "
        f"| source_url: {structured.get('source_url')} "
        f"| dropbox_url: {dropbox_url}"
    )


def run() -> None:
    init_db()
    dbx = _get_dropbox_client()

    log.info(f"Checking Dropbox folder: '{DROPBOX_FOLDER or '/'}'")
    try:
        files = list_recipe_files(dbx)
        unsupported = list_unsupported_files(dbx)
    except AuthError as e:
        log.error(f"Dropbox authentication error: {e}")
        log.info("Token may have expired. Please refresh your Dropbox credentials.")
        return
    except ApiError as e:
        log.error(f"Dropbox API error: {e}")
        return

    for entry in unsupported:
        ext = Path(entry.name).suffix.lower()
        with _DB_LOCK:
            record_failed_file(entry.path_lower, f"Unsupported file extension: {ext}")

    log.info(f"Found {len(files)} recipe file(s).")
    max_workers = int(os.environ.get("POLL_WORKERS", "5"))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_file, dbx, entry): entry for entry in files}
        for future in as_completed(futures):
            entry = futures[future]
            try:
                future.result()
            except AuthError as e:
                log.error(f"Dropbox authentication error while processing {entry.name}: {e}")
                log.info("Token may have expired. Please refresh your Dropbox credentials.")
                executor.shutdown(wait=False, cancel_futures=True)
                return
            except Exception as e:
                log.error(f"Unexpected error processing {entry.name}: {e}")

    log.info("Done.")


if __name__ == "__main__":
    run()
