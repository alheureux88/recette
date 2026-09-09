"""
push.py — Web Push notifications via VAPID.

VAPID (Voluntary Application Server Identification) keys identify your server
to push services. Generate once with `python -m recipes.push generate-keys`,
store in .env as VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY.
"""

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from pywebpush import WebPushException, webpush

log = logging.getLogger(__name__)

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:recipes@example.com")


def generate_vapid_keys() -> tuple[str, str]:
    """Generate a new VAPID key pair.

    Returns:
        Tuple of (public_key, private_key) as base64 strings.
    """
    import base64

    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    private_numbers = private_key.private_numbers()

    private_bytes = private_numbers.private_value.to_bytes(32, byteorder="big")
    private_key_b64 = base64.urlsafe_b64encode(private_bytes).decode("ascii").rstrip("=")

    public_key = private_key.public_key()
    public_numbers = public_key.public_numbers()
    x_bytes = public_numbers.x.to_bytes(32, byteorder="big")
    y_bytes = public_numbers.y.to_bytes(32, byteorder="big")

    uncompressed = b"\x04" + x_bytes + y_bytes
    public_key_b64 = base64.urlsafe_b64encode(uncompressed).decode("ascii").rstrip("=")

    return public_key_b64, private_key_b64


def get_vapid_claims() -> dict[str, Any]:
    """Build VAPID claims for webpush."""
    return {
        "sub": VAPID_CLAIMS_EMAIL,
    }


def send_push_notification(
    subscription: dict[str, Any],
    title: str,
    body: str,
    url: str | None = None,
    data: dict[str, Any] | None = None,
) -> bool:
    """Send a push notification to a subscription.

    Args:
        subscription: The push subscription object from the browser.
        title: Notification title.
        body: Notification body text.
        url: Optional URL to open when notification is clicked.
        data: Optional additional data to include in the notification.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        log.warning("VAPID keys not configured, cannot send push notification")
        return False

    payload: dict[str, Any] = {
        "title": title,
        "body": body,
        "icon": "/static/icon-192.png",
        "badge": "/static/favicon-32x32.png",
    }
    if url:
        payload["url"] = url
    if data:
        payload["data"] = data

    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=get_vapid_claims(),
        )
        log.info("Push notification sent: %s", title)
        return True
    except WebPushException as e:
        log.exception("Push notification failed")
        if e.response and e.response.status_code in (404, 410):
            log.info("Subscription expired or invalid")
            return False
        return False
    except Exception:
        log.exception("Unexpected push error")
        return False


def schedule_timer_notification(
    scheduler: Any,
    recipe_id: int,
    step_index: int,
    duration_seconds: int,
    subscription: dict[str, Any],
) -> str:
    """Schedule a push notification for when a timer completes.

    Args:
        scheduler: The APScheduler instance.
        recipe_id: The recipe ID.
        step_index: The step index (0-based).
        duration_seconds: Timer duration in seconds.
        subscription: The push subscription object.

    Returns:
        The job ID for the scheduled notification.
    """
    from recipes.shared.db import get_recipe

    recipe = get_recipe(recipe_id)
    recipe_title = recipe["title"] if recipe else "Recipe"

    end_time = datetime.now(UTC) + timedelta(seconds=duration_seconds)
    job_id = f"timer_{recipe_id}_{step_index}_{int(end_time.timestamp())}"

    def send_notification() -> None:
        title = "Timer complete!"
        body = f"Step {step_index + 1} of {recipe_title}"
        url = f"/recipe/{recipe_id}/cook"

        success = send_push_notification(
            subscription=subscription,
            title=title,
            body=body,
            url=url,
            data={"recipe_id": recipe_id, "step_index": step_index},
        )

        if not success:
            log.warning("Timer notification failed for recipe %s step %s", recipe_id, step_index)

    scheduler.add_job(
        send_notification,
        "date",
        run_date=end_time,
        id=job_id,
        replace_existing=True,
        misfire_grace_time=60,
    )

    log.info("Scheduled timer notification: %s at %s", job_id, end_time)
    return job_id


def cancel_timer_notification(scheduler: Any, recipe_id: int, step_index: int) -> int:
    """Cancel all pending timer notifications for a recipe step.

    Returns:
        Number of jobs removed.
    """
    removed = 0
    for job in scheduler.get_jobs():
        if job.id.startswith(f"timer_{recipe_id}_{step_index}_"):
            job.remove()
            removed += 1
    return removed


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "generate-keys":
        pub, priv = generate_vapid_keys()
        print("Add these to your .env file:\n")
        print(f"VAPID_PUBLIC_KEY={pub}")
        print(f"VAPID_PRIVATE_KEY={priv}")
    else:
        print("Usage: python -m recipes.push generate-keys")
