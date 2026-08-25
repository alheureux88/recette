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
from urllib.parse import urlencode

import dropbox
import requests
from dropbox.exceptions import ApiError, AuthError
from dropbox.sharing import RequestedVisibility, SharedLinkSettings

from recipes.db import (
    get_dropbox_connection_credentials,
    get_dropbox_connections,
    get_processed_hash,
    get_setting,
    init_db,
    is_blacklisted,
    is_manually_edited,
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

_conn_clients: dict[int, tuple[dropbox.Dropbox, float]] = {}
_CONN_CLIENTS_LOCK = threading.Lock()


def _refresh_token_for(refresh_token: str, app_key: str, app_secret: str) -> tuple[str, float]:
    """Exchange a refresh token for a fresh access token.

    Returns:
        A tuple of (access_token, expiry_timestamp)
    """
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

    return _refresh_token_for(str(refresh_token), str(app_key), str(app_secret))


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
    """Reset the cached Dropbox clients (useful for testing)."""
    global _dbx_client, _dbx_token_expiry
    _dbx_client = None
    _dbx_token_expiry = 0
    with _CONN_CLIENTS_LOCK:
        _conn_clients.clear()


def has_env_dropbox_credentials() -> bool:
    """True si un compte Dropbox par défaut est configuré via l'environnement."""
    return bool(os.environ.get("DROPBOX_REFRESH_TOKEN") or os.environ.get("DROPBOX_TOKEN"))


def _env_app_credentials() -> tuple[str, str]:
    """Identifiants d'application Dropbox partagés par toutes les connexions."""
    app_key = os.environ.get("DROPBOX_APP_KEY")
    app_secret = os.environ.get("DROPBOX_APP_SECRET")
    if not app_key or not app_secret:
        raise ValueError(
            "DROPBOX_APP_KEY and DROPBOX_APP_SECRET environment variables "
            "are required to connect extra Dropbox accounts"
        )
    return app_key, app_secret


def get_connection_client(connection: dict[str, object]) -> dropbox.Dropbox:
    """Get or create a cached Dropbox client for an extra configured connection."""
    conn_id = int(str(connection["id"]))

    with _CONN_CLIENTS_LOCK:
        cached = _conn_clients.get(conn_id)
        if cached is not None and time.time() < cached[1]:
            return cached[0]

    app_key, app_secret = _env_app_credentials()
    access_token, expiry = _refresh_token_for(str(connection["refresh_token"]), app_key, app_secret)
    client = dropbox.Dropbox(access_token)
    with _CONN_CLIENTS_LOCK:
        _conn_clients[conn_id] = (client, expiry)
    return client


def forget_connection_client(connection_id: int) -> None:
    """Oublie le client en cache d'une connexion supprimee."""
    with _CONN_CLIENTS_LOCK:
        _conn_clients.pop(connection_id, None)


def verify_connection_credentials(refresh_token: str) -> str:
    """Validate a Dropbox refresh token and return the account display name.

    Uses the application credentials from the environment.
    Raises on invalid credentials; never touches the client caches.
    """
    app_key, app_secret = _env_app_credentials()
    access_token, _ = _refresh_token_for(refresh_token, app_key, app_secret)
    account = dropbox.Dropbox(access_token).users_get_current_account()
    name = getattr(account.name, "display_name", "") if account.name else ""
    email = getattr(account, "email", "") or ""
    return " — ".join(x for x in (name, email) if x) or "Compte Dropbox"


def build_oauth_authorize_url(redirect_uri: str, state: str) -> str:
    """URL d'autorisation OAuth2 Dropbox (offline → refresh token)."""
    app_key, _ = _env_app_credentials()
    params = urlencode(
        {
            "client_id": app_key,
            "response_type": "code",
            "token_access_type": "offline",
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"https://www.dropbox.com/oauth2/authorize?{params}"


def exchange_authorization_code(code: str, redirect_uri: str) -> str:
    """Échange le code d'autorisation OAuth2 contre un refresh token."""
    app_key, app_secret = _env_app_credentials()
    log.info("Exchanging Dropbox authorization code for refresh token...")
    response = requests.post(
        "https://api.dropbox.com/oauth2/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": app_key,
            "client_secret": app_secret,
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )

    if not response.ok:
        error_body = response.text
        log.error(f"Dropbox code exchange failed: {response.status_code}")
        log.error(f"Response: {error_body}")
        raise ValueError(f"Dropbox code exchange failed: {error_body}")

    token_data = response.json()
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise ValueError("Dropbox did not return a refresh token")
    return str(refresh_token)


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


def matches_filter(filename: str, pattern: str | None = None) -> bool:
    """Check if filename matches the filter pattern (supports * wildcard).

    Empty filter matches everything.
    """
    effective = DROPBOX_FILE_FILTER if pattern is None else pattern
    if not effective:
        return True
    return fnmatch.fnmatch(filename.lower(), effective.lower())


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def list_recipe_files(
    dbx: dropbox.Dropbox,
    folder: str | None = None,
    file_filter: str | None = None,
) -> list[dropbox.files.FileMetadata]:
    """Return all supported files in the Dropbox folder (non-recursive).

    Defaults to the DROPBOX_FOLDER / DROPBOX_FILE_FILTER env vars.
    """
    target_folder = DROPBOX_FOLDER if folder is None else folder
    result = dbx.files_list_folder(target_folder)
    entries = result.entries[:]
    while result.has_more:
        result = dbx.files_list_folder_continue(result.cursor)
        entries.extend(result.entries)

    files = [
        e
        for e in entries
        if isinstance(e, dropbox.files.FileMetadata)
        and Path(e.name).suffix.lower() in SUPPORTED_EXTS
        and matches_filter(e.name, file_filter)
    ]

    if file_filter:
        log.info(f"File filter active: '{file_filter}'")

    return files


def list_unsupported_files(
    dbx: dropbox.Dropbox, folder: str | None = None
) -> list[dropbox.files.FileMetadata]:
    """Return all files with unsupported extensions in the Dropbox folder."""
    target_folder = DROPBOX_FOLDER if folder is None else folder
    result = dbx.files_list_folder(target_folder)
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


def process_file(
    dbx: dropbox.Dropbox,
    entry: dropbox.files.FileMetadata,
    path_prefix: str = "",
    connection_id: int | None = None,
) -> None:
    """Download, parse, tag, and store one Dropbox file.

    `path_prefix` namespaces source paths per account so that identical
    paths on different Dropbox accounts don't collide in the database.
    `connection_id` records which configured account the recipe came from
    (None = the default .env account).
    """
    path = f"{path_prefix}{entry.path_lower}"

    if is_blacklisted(path):
        log.info(f"  Skipping (blacklisted): {path}")
        return

    log.info(f"Downloading: {path}")
    content = download_file(dbx, entry.path_lower)
    content_hash = file_hash(content)

    existing_hash = get_processed_hash(path)
    if existing_hash == content_hash:
        log.info(f"  Skipping (unchanged): {path}")
        return

    if is_manually_edited(path):
        log.warning(f"  Skipping (manually edited recipe): {path}")
        with _DB_LOCK:
            record_failed_file(path, "Recette modifiee manuellement — mise a jour Dropbox ignoree")
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
    dropbox_url = get_or_create_shared_link(dbx, entry.path_lower)

    structured["source_file"] = path
    structured["connection_id"] = connection_id
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


def _poll_account(
    dbx: dropbox.Dropbox,
    label: str,
    folder: str,
    file_filter: str = "",
    path_prefix: str = "",
    connection_id: int | None = None,
) -> None:
    log.info(f"[{label}] Checking Dropbox folder: '{folder or '/'}'")
    try:
        files = list_recipe_files(dbx, folder, file_filter)
        unsupported = list_unsupported_files(dbx, folder)
    except AuthError as e:
        log.error(f"[{label}] Dropbox authentication error: {e}")
        log.info("Token may have expired. Please refresh your Dropbox credentials.")
        return
    except ApiError as e:
        log.error(f"[{label}] Dropbox API error: {e}")
        return

    for entry in unsupported:
        ext = Path(entry.name).suffix.lower()
        with _DB_LOCK:
            record_failed_file(
                f"{path_prefix}{entry.path_lower}", f"Unsupported file extension: {ext}"
            )

    log.info(f"[{label}] Found {len(files)} recipe file(s).")
    max_workers = int(os.environ.get("POLL_WORKERS", "5"))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_file, dbx, entry, path_prefix, connection_id): entry
            for entry in files
        }
        for future in as_completed(futures):
            entry = futures[future]
            try:
                future.result()
            except AuthError as e:
                log.error(
                    f"[{label}] Dropbox authentication error while processing {entry.name}: {e}"
                )
                log.info("Token may have expired. Please refresh your Dropbox credentials.")
                executor.shutdown(wait=False, cancel_futures=True)
                return
            except Exception as e:
                log.error(f"Unexpected error processing {entry.name}: {e}")


def run() -> None:
    init_db()

    accounts: list[tuple[str, dropbox.Dropbox, str, str, str, int | None]] = []

    if has_env_dropbox_credentials():
        if get_setting("default_active", "1") != "0":
            try:
                accounts.append(("", _get_dropbox_client(), DROPBOX_FOLDER, "", "", None))
            except Exception as e:
                log.error(f"Default (.env) Dropbox account unavailable: {e}")
        else:
            log.info("Default (.env) Dropbox account is paused — skipping.")
    else:
        log.info("No default (.env) Dropbox account configured — skipping.")

    for conn in get_dropbox_connections():
        conn_id = int(str(conn["id"]))
        label = str(conn["name"])
        if not conn["active"]:
            log.info(f"[{label}] Synchronization paused — skipping.")
            continue
        try:
            # La liste publique ne contient pas les identifiants ; on les
            # recupere separement pour construire le client.
            client = get_connection_client(get_dropbox_connection_credentials(conn_id) or conn)
        except Exception as e:
            log.error(f"[{label}] Could not create Dropbox client: {e}")
            continue
        accounts.append(
            (
                label,
                client,
                str(conn["folder"]),
                str(conn["file_filter"]),
                f"account:{conn_id}:",
                conn_id,
            )
        )

    if not accounts:
        log.warning("No active Dropbox accounts configured. Nothing to poll.")
        return

    for label, dbx, folder, file_filter, prefix, account_id in accounts:
        _poll_account(dbx, label or "default", folder, file_filter, prefix, account_id)

    log.info("Done.")


if __name__ == "__main__":
    run()
