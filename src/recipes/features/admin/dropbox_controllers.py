"""Admin feature — Dropbox proposals requiring owner approval.

Super-users (`require_content_admin`) may connect their own Dropbox account,
but the connection is created `pending`, inactive and invisible : it is
never polled until an owner approves it from `/admin/config`.

The OAuth dance reuses the app-level Dropbox `redirect_uri`, so no change
is required on the Dropbox app side : concurrent flows are told apart by
per-state records (`save_dropbox_oauth_state`), each bound to its author.

Helpers `_dropbox_redirect_uri`, `_admin_config_context` and
`_config_template_name` live in `admin.controllers` (same feature) and are
reused here to avoid duplicating proxy-aware URL logic and config context.
"""

import logging
import secrets
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from recipes.features.admin.controllers import (
    _admin_config_context,
    _config_template_name,
    _dropbox_redirect_uri,
)
from recipes.features.admin.services import (
    DROPBOX_STATUS_PENDING,
    MAX_PENDING_PROPOSALS_PER_USER,
    approve_dropbox_connection,
    count_pending_dropbox_proposals,
    delete_dropbox_connection,
    get_dropbox_connection_credentials,
    list_user_dropbox_proposals,
    reject_dropbox_connection,
    save_dropbox_oauth_state,
)
from recipes.features.admin.services import (
    add_dropbox_connection as _add_dropbox_connection,
)
from recipes.features.auth.services import resolve_user_id
from recipes.shared import auth as auth_module
from recipes.shared.auth import require_admin, require_content_admin
from recipes.shared.db import get_db
from recipes.shared.i18n import gettext
from recipes.shared.models import JsonDict
from recipes.shared.poller import build_oauth_authorize_url, create_pkce_pair

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _dropbox_template_name(request: Request) -> str:
    """Full page if browser navigation, partial if HTMX swap."""
    return (
        "partials/admin_dropbox.html" if "HX-Request" in request.headers else "admin_dropbox.html"
    )


def _dropbox_page_context(
    request: Request,
    conn: sqlite3.Connection,
    user_id: int,
    message: tuple[str, str] | None = None,
    oauth_refresh_token: str | None = None,
    oauth_account_label: str | None = None,
) -> JsonDict:
    """Context for the super-user Dropbox page (own proposals + prefill)."""
    from recipes.shared.web import _base_context

    ctx = _base_context(
        request,
        proposals=list_user_dropbox_proposals(user_id, conn=conn),
        message=message,
    )
    if oauth_refresh_token is not None:
        ctx["oauth_refresh_token"] = oauth_refresh_token
        ctx["oauth_account_label"] = oauth_account_label or ""
    return ctx


def _session_identity(request: Request, conn: sqlite3.Connection, lang: str) -> tuple[int, str]:
    """Resolve (numeric user id, OIDC sub) or raise 401."""
    session_user = auth_module.get_user(request)
    user_id = resolve_user_id(session_user, conn=conn)
    sub = session_user.get("sub") if session_user else None
    if user_id is None or not isinstance(sub, str) or not sub:
        raise HTTPException(status_code=401, detail=gettext("error.not_authenticated", lang))
    return user_id, sub


def _render_dropbox_page(
    request: Request,
    conn: sqlite3.Connection,
    user_id: int,
    message: tuple[str, str] | None,
    status_code: int = 200,
    oauth_refresh_token: str | None = None,
    oauth_account_label: str | None = None,
) -> HTMLResponse:
    from recipes.shared.web import templates

    return templates.TemplateResponse(
        request=request,
        name=_dropbox_template_name(request),
        context=_dropbox_page_context(
            request,
            conn,
            user_id,
            message,
            oauth_refresh_token,
            oauth_account_label,
        ),
        status_code=status_code,
    )


# ---------------------------------------------------------------------------
# Super-user proposal page
# ---------------------------------------------------------------------------


@router.get("/dropbox", response_class=HTMLResponse)
async def dropbox_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_content_admin),
) -> HTMLResponse:
    """List the viewer's Dropbox proposals and offer to connect a new one."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    user_id, _sub = _session_identity(request, conn, lang)
    return _render_dropbox_page(request, conn, user_id, None)


@router.get("/dropbox/connect")
async def dropbox_connect(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_content_admin),
) -> Response:
    """Redirect to Dropbox authorization (proposal flow, stays inactive)."""
    from recipes.shared.web import _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    _user_id, sub = _session_identity(request, conn, lang)
    state = secrets.token_urlsafe(24)
    verifier, challenge = create_pkce_pair()
    save_dropbox_oauth_state(state, sub, "propose", verifier, conn=conn)
    try:
        url = build_oauth_authorize_url(_dropbox_redirect_uri(request), state, challenge)
    except ValueError as exc:
        return templates.TemplateResponse(
            request=request,
            name=_dropbox_template_name(request),
            context=_dropbox_page_context(request, conn, _user_id, ("error", str(exc))),
            status_code=422,
        )
    return RedirectResponse(url=url, status_code=302)


@router.post("/dropbox", response_class=HTMLResponse)
async def dropbox_propose(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _user: dict[str, Any] = Depends(require_content_admin),
) -> HTMLResponse:
    """Submit a Dropbox connection as pending (inactive + invisible)."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    user_id, _sub = _session_identity(request, conn, lang)
    form = await request.form()
    name = str(form.get("name") or "").strip()
    refresh_token = str(form.get("refresh_token") or "").strip()
    folder = str(form.get("folder") or "").strip()
    file_filter = str(form.get("file_filter") or "").strip()

    if not name or not refresh_token:
        return _render_dropbox_page(
            request,
            conn,
            user_id,
            ("error", gettext("flash.dropbox_name_required", lang)),
            status_code=422,
        )
    if count_pending_dropbox_proposals(user_id, conn=conn) >= MAX_PENDING_PROPOSALS_PER_USER:
        return _render_dropbox_page(
            request,
            conn,
            user_id,
            (
                "error",
                gettext(
                    "flash.dropbox_limit_reached",
                    lang,
                    n=MAX_PENDING_PROPOSALS_PER_USER,
                ),
            ),
            status_code=422,
        )
    connection_id = _add_dropbox_connection(
        name=name,
        refresh_token=refresh_token,
        folder=folder,
        file_filter=file_filter,
        conn=conn,
        active=False,
        visible=False,
        proposed_by_user_id=user_id,
        status=DROPBOX_STATUS_PENDING,
    )
    if connection_id is None:
        return _render_dropbox_page(
            request,
            conn,
            user_id,
            ("error", gettext("flash.dropbox_name_taken", lang, name=name)),
            status_code=422,
        )
    log.info("Dropbox proposal '%s' submitted by user %s (pending)", name, user_id)
    return _render_dropbox_page(
        request,
        conn,
        user_id,
        ("ok", gettext("flash.dropbox_proposed", lang, name=name)),
    )


@router.post("/dropbox/{connection_id}/delete", response_class=HTMLResponse)
async def dropbox_delete_proposal(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    connection_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_content_admin),
) -> HTMLResponse:
    """Delete the viewer's own pending/rejected proposal (404 otherwise)."""
    from recipes.shared.web import _resolve_request_lang

    lang = _resolve_request_lang(request)
    user_id, _sub = _session_identity(request, conn, lang)
    proposals = {p["id"]: p for p in list_user_dropbox_proposals(user_id, conn=conn)}
    proposal = proposals.get(connection_id)
    if proposal is None or str(proposal.get("status")) not in ("pending", "rejected"):
        return _render_dropbox_page(
            request,
            conn,
            user_id,
            ("error", gettext("flash.dropbox_connection_missing", lang)),
            status_code=404,
        )
    delete_dropbox_connection(connection_id, conn=conn)
    log.info("Dropbox proposal %s deleted by proposer %s", connection_id, user_id)
    return _render_dropbox_page(
        request,
        conn,
        user_id,
        ("ok", gettext("flash.dropbox_proposal_deleted", lang)),
    )


# ---------------------------------------------------------------------------
# Owner approval (system administration)
# ---------------------------------------------------------------------------


@router.post("/config/dropbox/{connection_id}/approve", response_class=HTMLResponse)
async def admin_config_approve_dropbox(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    connection_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    """Approve a pending proposal: approved + active + visible."""
    from recipes.shared.web import _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    dbx_conn = get_dropbox_connection_credentials(connection_id, conn=conn)
    if not dbx_conn or not approve_dropbox_connection(connection_id, conn=conn):
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(
                request,
                conn,
                ("error", gettext("flash.dropbox_connection_missing", lang)),
            ),
            status_code=404,
        )
    log.info("Dropbox proposal %s approved by owner", connection_id)
    return templates.TemplateResponse(
        request=request,
        name=_config_template_name(request),
        context=_admin_config_context(
            request,
            conn,
            ("ok", gettext("flash.dropbox_approved", lang, name=str(dbx_conn["name"]))),
        ),
    )


@router.post("/config/dropbox/{connection_id}/reject", response_class=HTMLResponse)
async def admin_config_reject_dropbox(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    connection_id: int = Path(gt=0),
    _user: dict[str, Any] = Depends(require_admin),
) -> HTMLResponse:
    """Reject a pending proposal (row kept so the proposer sees the outcome)."""
    from recipes.shared.web import _resolve_request_lang, templates

    lang = _resolve_request_lang(request)
    dbx_conn = get_dropbox_connection_credentials(connection_id, conn=conn)
    if not dbx_conn or not reject_dropbox_connection(connection_id, conn=conn):
        return templates.TemplateResponse(
            request=request,
            name=_config_template_name(request),
            context=_admin_config_context(
                request,
                conn,
                ("error", gettext("flash.dropbox_connection_missing", lang)),
            ),
            status_code=404,
        )
    log.info("Dropbox proposal %s rejected by owner", connection_id)
    return templates.TemplateResponse(
        request=request,
        name=_config_template_name(request),
        context=_admin_config_context(
            request,
            conn,
            ("ok", gettext("flash.dropbox_rejected", lang, name=str(dbx_conn["name"]))),
        ),
    )
