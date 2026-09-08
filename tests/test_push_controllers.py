"""Tests for push feature — controllers."""

from unittest.mock import patch

import pytest

from recipes.shared.db import init_db


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()


class TestPushRoutes:
    def test_vapid_public_key(self, client):
        resp = client.get("/api/push/vapid-public-key")
        assert resp.status_code == 200
        data = resp.json()
        assert "vapid_public_key" in data

    def test_subscribe(self, client):
        payload = {
            "endpoint": "https://push.example.com/test",
            "subscription": {"keys": {"p256dh": "key1", "auth": "auth1"}},
        }
        resp = client.post("/api/push/subscribe", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "id" in data

    def test_unsubscribe(self, client):
        # First subscribe
        payload = {
            "endpoint": "https://push.example.com/unsub",
            "subscription": {"keys": {"p256dh": "key1", "auth": "auth1"}},
        }
        client.post("/api/push/subscribe", json=payload)
        # Then unsubscribe
        resp = client.post("/api/push/unsubscribe?endpoint=https://push.example.com/unsub")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_schedule_timer_without_scheduler(self, client):
        payload = {
            "recipe_id": 1,
            "step_index": 0,
            "duration_seconds": 60,
            "endpoint": "https://push.example.com/timer",
        }
        with patch("recipes.features.push.controllers._scheduler", None):
            resp = client.post("/api/push/schedule-timer", json=payload)
            assert resp.status_code == 503

    def test_cancel_timer_without_scheduler(self, client):
        payload = {"recipe_id": 1, "step_index": 0}
        with patch("recipes.features.push.controllers._scheduler", None):
            resp = client.post("/api/push/cancel-timer", json=payload)
            assert resp.status_code == 503
