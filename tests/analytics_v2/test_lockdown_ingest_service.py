from __future__ import annotations

import hashlib
import hmac
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services import lockdown_ingest_service


class _FakeResponse:
    def __init__(self, body: str, status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class LockdownIngestServiceTests(unittest.TestCase):
    def test_build_signature_uses_expected_format(self):
        timestamp = 1713371234
        body_json = '{"event_id":1234,"monitor_id":42}'
        secret = "top-secret"

        got = lockdown_ingest_service.build_signature(timestamp, body_json, secret)

        expected_msg = f"{timestamp}.{body_json}".encode("utf-8")
        expected = hmac.new(secret.encode("utf-8"), expected_msg, hashlib.sha256).hexdigest()
        self.assertEqual(got, expected)

    def test_build_lockdown_payload_contains_required_fields(self):
        event = SimpleNamespace(
            id=1234,
            camera_id=42,
            created_at=datetime(2026, 4, 16, 10, 30, 0),
        )

        payload = lockdown_ingest_service.build_lockdown_payload(event)

        self.assertEqual(payload["event_id"], 1234)
        self.assertEqual(payload["monitor_id"], 42)
        self.assertEqual(payload["executed_at"], "2026-04-16 07:30:00")
        self.assertEqual(payload["frames_analyzed"], 15)
        self.assertEqual(payload["filename"], "evento_1234.json")
        self.assertEqual(payload["dirname"], "16-04-2026/ID_42/evento_1234.json")

    def test_attempt_http_send_uses_post_json_and_headers(self):
        db = Mock()
        delivery = SimpleNamespace(
            event_id=1234,
            id=99,
            target_url="https://lockdown-security.com/api/events/ingest",
            attempt_count=0,
            last_attempt_at=None,
            status="pending",
            error_message=None,
        )
        body_json = '{"event_id":1234,"monitor_id":42}'
        request_timestamp = 1713371234
        request_signature = "abc123"

        def _urlopen_side_effect(request_obj, timeout):
            self.assertEqual(request_obj.get_method(), "POST")
            self.assertEqual(request_obj.data, body_json.encode("utf-8"))
            self.assertEqual(timeout, 5.0)
            self.assertEqual(request_obj.get_header("X-timestamp"), str(request_timestamp))
            self.assertEqual(request_obj.get_header("X-signature"), request_signature)
            self.assertEqual(request_obj.get_header("Content-type"), "application/json")
            return _FakeResponse("{}", 200)

        with patch.object(lockdown_ingest_service.settings, "lockdown_ingest_timeout_seconds", 5.0), patch(
            "app.services.lockdown_ingest_service._persist_payload_preview"
        ), patch("app.services.lockdown_ingest_service._store_delivery_success") as mock_success, patch(
            "app.services.lockdown_ingest_service._store_delivery_error"
        ) as mock_error, patch(
            "app.services.lockdown_ingest_service.urllib_request.urlopen", side_effect=_urlopen_side_effect
        ):
            lockdown_ingest_service._attempt_http_send(
                db,
                delivery,
                body_json,
                request_timestamp,
                request_signature,
            )

        self.assertTrue(mock_success.called)
        self.assertFalse(mock_error.called)

    def test_send_event_if_needed_errors_when_secret_missing(self):
        db = Mock()
        event = SimpleNamespace(id=1234)
        delivery = SimpleNamespace(
            event_id=1234,
            id=99,
            target_url="",
        )

        with patch("app.services.lockdown_ingest_service._event_triggers_lockdown", return_value=True), patch(
            "app.services.lockdown_ingest_service._prepare_delivery_record",
            return_value=(delivery, '{"event_id":1234}', 1713371234),
        ), patch.object(lockdown_ingest_service.settings, "lockdown_ingest_enabled", True), patch.object(
            lockdown_ingest_service.settings,
            "lockdown_ingest_url",
            "https://lockdown-security.com/api/events/ingest",
        ), patch.object(lockdown_ingest_service.settings, "lockdown_ingest_secret", ""), patch(
            "app.services.lockdown_ingest_service._store_delivery_error"
        ) as mock_store_error, patch(
            "app.services.lockdown_ingest_service._attempt_http_send"
        ) as mock_attempt:
            lockdown_ingest_service.send_event_if_needed(event, db)

        self.assertTrue(mock_store_error.called)
        self.assertFalse(mock_attempt.called)

    def test_send_event_if_needed_uses_same_json_for_signature_and_send(self):
        db = Mock()
        event = SimpleNamespace(id=1234)
        delivery = SimpleNamespace(
            event_id=1234,
            id=99,
            target_url="",
        )
        body_json = '{"event_id":1234,"monitor_id":42}'

        with patch("app.services.lockdown_ingest_service._event_triggers_lockdown", return_value=True), patch(
            "app.services.lockdown_ingest_service._prepare_delivery_record",
            return_value=(delivery, body_json, 1713371234),
        ), patch.object(lockdown_ingest_service.settings, "lockdown_ingest_enabled", True), patch.object(
            lockdown_ingest_service.settings,
            "lockdown_ingest_url",
            "https://lockdown-security.com/api/events/ingest",
        ), patch.object(lockdown_ingest_service.settings, "lockdown_ingest_secret", "secret-key"), patch(
            "app.services.lockdown_ingest_service._persist_payload_preview"
        ), patch("app.services.lockdown_ingest_service.build_signature", return_value="sig-1") as mock_sig, patch(
            "app.services.lockdown_ingest_service._attempt_http_send"
        ) as mock_send:
            lockdown_ingest_service.send_event_if_needed(event, db)

        mock_sig.assert_called_once_with(1713371234, body_json, "secret-key")
        mock_send.assert_called_once_with(db, delivery, body_json, 1713371234, "sig-1")

    def test_attempt_http_send_marks_invalid_json_response_as_error(self):
        db = Mock()
        delivery = SimpleNamespace(
            event_id=1234,
            id=99,
            target_url="https://lockdown-security.com/api/events/ingest",
            attempt_count=0,
            last_attempt_at=None,
            status="pending",
            error_message=None,
        )

        with patch.object(lockdown_ingest_service.settings, "lockdown_ingest_timeout_seconds", 5.0), patch(
            "app.services.lockdown_ingest_service._persist_payload_preview"
        ), patch("app.services.lockdown_ingest_service._store_delivery_success") as mock_success, patch(
            "app.services.lockdown_ingest_service._store_delivery_error"
        ) as mock_error, patch(
            "app.services.lockdown_ingest_service.urllib_request.urlopen",
            return_value=_FakeResponse("not-json", 200),
        ):
            lockdown_ingest_service._attempt_http_send(
                db,
                delivery,
                '{"event_id":1234}',
                1713371234,
                "sig",
            )

        self.assertFalse(mock_success.called)
        self.assertTrue(mock_error.called)


if __name__ == "__main__":
    unittest.main()
