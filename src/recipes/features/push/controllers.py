"""Push feature — Web Push notification endpoints."""

import sqlite3
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from recipes.features.push.services import (
    delete_push_subscription,
    get_push_subscription,
    save_push_subscription,
)
from recipes.shared.auth import get_user
from recipes.shared.db import get_db
from recipes.shared.i18n import gettext
from recipes.shared.models import (
    PushSubscriptionRegister,
    TimerCancelRequest,
    TimerScheduleRequest,
)
from recipes.shared.push import (
    VAPID_PUBLIC_KEY,
    cancel_timer_notification,
    schedule_timer_notification,
)
from recipes.shared.web import _resolve_request_lang

router = APIRouter(prefix="/api/push", tags=["push"])

_scheduler: BackgroundScheduler | None = None


def set_scheduler(scheduler: BackgroundScheduler | None) -> None:
    """Set the scheduler instance for timer notifications."""
    global _scheduler
    _scheduler = scheduler


def _get_user_from_request_safe(request: Request | None) -> dict[str, Any] | None:
    """Safely get user from request, returning None if no request or no user."""
    if request is None:
        return None
    try:
        return get_user(request)
    except Exception:
        return None


@router.get("/vapid-public-key")
async def vapid_public_key() -> dict[str, str]:
    """Return the VAPID public key for the browser to subscribe to push."""
    return {"vapid_public_key": VAPID_PUBLIC_KEY}


@router.post("/subscribe")
async def subscribe(
    data: PushSubscriptionRegister, conn: sqlite3.Connection = Depends(get_db)
) -> dict[str, object]:
    """Register a push subscription from the browser."""
    user = _get_user_from_request_safe(None)
    user_id = user["id"] if user else None
    sub_id = save_push_subscription(user_id, data.endpoint, data.subscription, conn=conn)
    return {"ok": True, "id": sub_id}


@router.post("/unsubscribe")
async def unsubscribe(
    endpoint: str = Query(...), conn: sqlite3.Connection = Depends(get_db)
) -> dict[str, object]:
    """Unregister a push subscription."""
    deleted = delete_push_subscription(endpoint, conn=conn)
    return {"ok": True, "deleted": deleted}


@router.post("/schedule-timer")
async def schedule_timer(
    request: Request,
    data: TimerScheduleRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    """Schedule a push notification for when a timer completes."""
    lang = _resolve_request_lang(request)
    if not _scheduler:
        raise HTTPException(status_code=503, detail=gettext("error.scheduler_unavailable", lang))

    sub = get_push_subscription(data.endpoint, conn=conn)
    if not sub:
        raise HTTPException(status_code=404, detail=gettext("error.subscription_not_found", lang))

    subscription_obj = sub["subscription"]
    if not isinstance(subscription_obj, dict):
        raise HTTPException(status_code=500, detail=gettext("error.invalid_subscription", lang))

    job_id = schedule_timer_notification(
        _scheduler,
        data.recipe_id,
        data.step_index,
        data.duration_seconds,
        subscription_obj,
    )
    return {"ok": True, "job_id": job_id}


@router.post("/cancel-timer")
async def cancel_timer(request: Request, data: TimerCancelRequest) -> dict[str, object]:
    """Cancel scheduled timer notifications for a recipe step."""
    if not _scheduler:
        lang = _resolve_request_lang(request)
        raise HTTPException(status_code=503, detail=gettext("error.scheduler_unavailable", lang))

    removed = cancel_timer_notification(_scheduler, data.recipe_id, data.step_index)
    return {"ok": True, "removed": removed}
