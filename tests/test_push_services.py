"""Tests for push feature — services."""

import pytest

from recipes.features.push.services import (
    delete_push_subscription,
    get_all_push_subscriptions,
    get_push_subscription,
    save_push_subscription,
)
from recipes.shared.db import init_db


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()


class TestPushSubscriptions:
    def test_save_subscription_anonymous(self):
        sub = {"endpoint": "https://push.example.com/anon", "keys": {"p256dh": "key2"}}
        sub_id = save_push_subscription(user_id=None, endpoint=sub["endpoint"], subscription=sub)
        assert sub_id > 0

    def test_save_subscription_with_user(self):
        from recipes.features.auth.services import get_or_create_user

        user_id = get_or_create_user("test-subject", "test@example.com", "Test User")
        sub = {"endpoint": "https://push.example.com/sub1", "keys": {"p256dh": "key1"}}
        sub_id = save_push_subscription(user_id=user_id, endpoint=sub["endpoint"], subscription=sub)
        assert sub_id > 0

    def test_save_subscription_update_existing(self):
        sub = {"endpoint": "https://push.example.com/update", "keys": {"p256dh": "key1"}}
        sub_id1 = save_push_subscription(user_id=None, endpoint=sub["endpoint"], subscription=sub)
        sub["keys"]["p256dh"] = "key_updated"
        sub_id2 = save_push_subscription(user_id=None, endpoint=sub["endpoint"], subscription=sub)
        assert sub_id1 == sub_id2

    def test_get_subscription(self):
        sub = {"endpoint": "https://push.example.com/get", "keys": {"p256dh": "key1"}}
        save_push_subscription(user_id=None, endpoint=sub["endpoint"], subscription=sub)
        found = get_push_subscription(sub["endpoint"])
        assert found is not None
        assert found["endpoint"] == sub["endpoint"]
        assert found["subscription"]["keys"]["p256dh"] == "key1"

    def test_get_subscription_not_found(self):
        found = get_push_subscription("https://nonexistent.com")
        assert found is None

    def test_delete_subscription(self):
        sub = {"endpoint": "https://push.example.com/delete", "keys": {"p256dh": "key1"}}
        save_push_subscription(user_id=None, endpoint=sub["endpoint"], subscription=sub)
        assert delete_push_subscription(sub["endpoint"]) is True
        assert get_push_subscription(sub["endpoint"]) is None

    def test_delete_subscription_not_found(self):
        assert delete_push_subscription("https://nonexistent.com") is False

    def test_get_all_subscriptions(self):
        sub1 = {"endpoint": "https://push.example.com/all1", "keys": {"p256dh": "key1"}}
        sub2 = {"endpoint": "https://push.example.com/all2", "keys": {"p256dh": "key2"}}
        save_push_subscription(user_id=None, endpoint=sub1["endpoint"], subscription=sub1)
        save_push_subscription(user_id=None, endpoint=sub2["endpoint"], subscription=sub2)
        all_subs = get_all_push_subscriptions()
        assert len(all_subs) >= 2


def test_timer_notification_scheduler_typed():
    """Le scheduler push est typé BackgroundScheduler (pas Any)."""
    import typing

    from apscheduler.schedulers.background import BackgroundScheduler

    from recipes.shared import push as push_module

    assert (
        typing.get_type_hints(push_module.schedule_timer_notification)["scheduler"]
        is BackgroundScheduler
    )
    assert (
        typing.get_type_hints(push_module.cancel_timer_notification)["scheduler"]
        is BackgroundScheduler
    )
