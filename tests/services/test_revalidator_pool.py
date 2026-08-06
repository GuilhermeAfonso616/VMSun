from __future__ import annotations

import threading
import unittest

from app.core.config import settings
from app.services.revalidator_pool import RevalidatorPool


class RevalidatorPoolTest(unittest.TestCase):
    def setUp(self):
        self._original = {
            "revalidator_pool_enabled": settings.revalidator_pool_enabled,
            "revalidator_pool_max_concurrency": settings.revalidator_pool_max_concurrency,
            "revalidator_pool_max_queue_size": settings.revalidator_pool_max_queue_size,
            "revalidator_pool_job_timeout_seconds": settings.revalidator_pool_job_timeout_seconds,
            "revalidator_pool_max_job_age_seconds": settings.revalidator_pool_max_job_age_seconds,
        }
        settings.revalidator_pool_enabled = True
        settings.revalidator_pool_max_concurrency = 1
        settings.revalidator_pool_max_queue_size = 0
        settings.revalidator_pool_job_timeout_seconds = 1.0
        settings.revalidator_pool_max_job_age_seconds = 10.0

    def tearDown(self):
        for key, value in self._original.items():
            setattr(settings, key, value)

    def test_returns_fallback_when_pool_is_full(self):
        pool = RevalidatorPool()
        entered = threading.Event()
        release = threading.Event()

        def hold_job():
            entered.set()
            release.wait(timeout=2.0)
            return "done"

        worker = threading.Thread(
            target=lambda: pool.run("ia2", hold_job, lambda reason: f"fallback:{reason}"),
            daemon=True,
        )
        worker.start()
        self.assertTrue(entered.wait(timeout=1.0))

        result = pool.run("ia3", lambda: "unexpected", lambda reason: f"fallback:{reason}")

        release.set()
        worker.join(timeout=2.0)
        self.assertEqual(result, "fallback:revalidator_pool_queue_full")


if __name__ == "__main__":
    unittest.main()
