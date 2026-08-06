from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.core.config import settings
from app.runtime.capture import CameraCaptureService


class CaptureOpenBackoffTest(unittest.TestCase):
    def test_open_retries_with_backoff_before_failing(self):
        service = CameraCaptureService(rtsp_url="rtsp://user:pass@192.168.1.43:8080/stream", camera_id=2)
        service.logger = MagicMock()
        service._stop_requested = lambda: False
        service.reconnect_sleep_step_seconds = 10.0

        fake_capture = MagicMock()
        fake_capture.open.side_effect = [RuntimeError("open failed"), RuntimeError("open failed"), None]
        service.capture = fake_capture

        with patch("app.runtime.capture.time.sleep") as sleep_mock:
            service.open()

        self.assertEqual(fake_capture.open.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)
        expected_initial_delay = settings.capture_open_retry_initial_delay_seconds
        expected_second_delay = expected_initial_delay * settings.capture_open_retry_backoff_multiplier
        self.assertAlmostEqual(sleep_mock.call_args_list[0].args[0], expected_initial_delay, places=2)
        self.assertAlmostEqual(sleep_mock.call_args_list[1].args[0], expected_second_delay, places=2)


if __name__ == "__main__":
    unittest.main()
