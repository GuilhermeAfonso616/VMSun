from __future__ import annotations

import unittest
from datetime import datetime

from app.core.timezone import brazil_date_bounds_as_utc_naive, format_brazil_datetime


class BrazilTimezoneTest(unittest.TestCase):
    def test_formats_naive_utc_as_brazil_time(self):
        self.assertEqual(
            format_brazil_datetime(datetime(2026, 4, 30, 16, 53, 13)),
            "30/04/2026 13:53:13",
        )

    def test_brazil_date_filter_bounds_are_utc_storage_bounds(self):
        start, end = brazil_date_bounds_as_utc_naive("2026-04-30")

        self.assertEqual(start, datetime(2026, 4, 30, 3, 0, 0))
        self.assertEqual(end, datetime(2026, 5, 1, 3, 0, 0))


if __name__ == "__main__":
    unittest.main()
